import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx
from fastapi import (
    FastAPI,
    File,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pyswarm")

app = FastAPI(title="PySwarm API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TestConfig(BaseModel):
    users: int = Field(default=10, ge=1, le=10000)
    spawn_rate: int = Field(default=1, ge=1, le=1000)
    host: str = "http://127.0.0.1:8000"
    duration: int = Field(default=30, ge=5, le=7200)
    wait_time: float = Field(default=1.0, ge=0.0, le=10.0)
    token: Optional[str] = None


class ApiRequest(BaseModel):
    method: str
    url: str
    headers: Optional[Dict[str, str]] = None
    body: Optional[Dict[str, Any]] = None


def ensure_tmp_dir():
    os.makedirs("tmp", exist_ok=True)


@app.post("/api/upload_folder")
async def upload_folder(files: List[UploadFile] = File(...)):
    ensure_tmp_dir()
    all_requests = []
    results = []

    for file in files:
        name = os.path.basename(file.filename or "unknown.json")

        if not name.endswith(".json"):
            results.append({"file": name, "status": "skipped", "reason": "не .json"})
            continue

        try:
            contents = await file.read()
            data = json.loads(contents)
        except json.JSONDecodeError as e:
            results.append(
                {"file": name, "status": "skipped", "reason": f"битый JSON: {e}"}
            )
            continue

        if not isinstance(data, list):
            results.append({"file": name, "status": "skipped", "reason": "не список"})
            continue

        try:
            [ApiRequest(**item) for item in data]
        except Exception as e:
            results.append(
                {"file": name, "status": "skipped", "reason": f"невалидный запрос: {e}"}
            )
            continue

        all_requests.extend(data)
        results.append({"file": name, "status": "ok", "count": len(data)})

    if not all_requests:
        logger.error("❌ Ни один файл не прошел валидацию.")
        return {
            "saved_files": 0,
            "skipped_files": len(results),
            "total_requests": 0,
            "files": results,
        }

    # Сохраняем ОДИН объединенный файл
    with open("tmp/test_data.json", "w", encoding="utf-8") as f:
        json.dump(all_requests, f, ensure_ascii=False, indent=2)

    saved = sum(1 for r in results if r["status"] == "ok")
    skipped = len(results) - saved
    logger.info(
        "Папка загружена: ok=%d, пропущено=%d, всего запросов=%d",
        saved,
        skipped,
        len(all_requests),
    )
    return {
        "saved_files": saved,
        "skipped_files": skipped,
        "total_requests": len(all_requests),
        "files": results,
    }


def cleanup_old_files(folder: str):
    files_to_delete = [
        "test_results_stats_history.csv",
        "test_results_stats.csv",
        "test_results_failures.csv",
    ]
    for filename in files_to_delete:
        filepath = os.path.join(folder, filename)
        if not os.path.exists(filepath):
            continue
        try:
            os.remove(filepath)
        except PermissionError:
            pass


@app.post("/api/start")
async def start_load_test(config: TestConfig):
    logger.info(
        "Запуск теста: users=%d, spawn=%d, host=%s, duration=%ds",
        config.users,
        config.spawn_rate,
        config.host,
        config.duration,
    )
    ensure_tmp_dir()
    cleanup_old_files("tmp")

    worker_config = {"token": config.token, "wait_time": config.wait_time}
    with open("tmp/config.json", "w", encoding="utf-8") as f:
        json.dump(worker_config, f)

    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            await client.get("http://locust_master:8089/stats/reset", timeout=2.0)

            payload = {
                "user_count": config.users,
                "spawn_rate": config.spawn_rate,
                "host": config.host,
                "run_time": f"{config.duration}s",
            }

            response = await client.post(
                "http://locust_master:8089/swarm", data=payload, timeout=5.0
            )
            response.raise_for_status()

        logger.info("Тест успешно запущен на воркерах")
        return {"status": "started", "message": "Тест запущен на воркерах"}

    except Exception as e:
        logger.error("Ошибка связи с Locust Master: %s", e)
        raise HTTPException(
            status_code=500, detail=f"Ошибка связи с Locust Master: {str(e)}"
        )


@app.post("/api/stop")
async def stop_load_test():
    logger.info("Остановка теста")
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            await client.get("http://locust_master:8089/stop", timeout=2.0)
        logger.info("Тест остановлен")
        return {"status": "stopped", "message": "Тест остановлен"}
    except Exception as e:
        logger.error("Ошибка при остановке теста: %s", e)
        return {"status": "error", "message": f"Ошибка: {str(e)}"}


async def get_latest_metrics() -> Optional[Dict[str, Any]]:
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(
                "http://locust_master:8089/stats/requests",
                timeout=3.0,
            )
            response.raise_for_status()
            data = response.json()

        if data.get("state") not in ("running", "spawning", "ready"):
            return None

        total_fails = sum(stat.get("num_failures", 0) for stat in data.get("stats", []))

        return {
            "timestamp": str(int(asyncio.get_event_loop().time())),
            "user_count": str(data.get("user_count", 0)),
            "total_rps": str(round(data.get("total_rps", 0), 2)),
            "fails": str(total_fails),
        }
    except Exception as e:
        logger.error("Ошибка получения метрик: %s - %s", type(e).__name__, e)
        return None


@app.websocket("/ws/metrics")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    try:
        while True:
            metrics = await get_latest_metrics()

            if metrics:
                await websocket.send_json(metrics)

            await asyncio.sleep(1)

    except WebSocketDisconnect:
        logger.info("Клиент отключился от WebSocket")


@app.get("/api/config")
async def get_ui_config():
    return {
        "users": {
            "min": int(os.getenv("MAX_USERS_MIN", 1)),
            "max": int(os.getenv("MAX_USERS_MAX", 1000)),
            "step": 1,
            "default": int(os.getenv("MAX_USERS_DEFAULT", 20)),
        },
        "spawn": {
            "min": 1,
            "max": 100,
            "step": 1,
            "default": 2,
        },
        "duration": {
            "min": int(os.getenv("TEST_DURATION_MIN", 5)),
            "max": int(os.getenv("TEST_DURATION_MAX", 7200)),
            "step": 5,
            "default": int(os.getenv("TEST_DURATION_DEFAULT", 30)),
        },
        "wait": {
            "min": 0.0,
            "max": 5.0,
            "step": 0.1,
            "default": 1.0,
        },
    }


app.mount("/", StaticFiles(directory="static", html=True), name="static")
