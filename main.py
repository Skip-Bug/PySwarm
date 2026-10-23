import asyncio
import json
import os
from typing import Any, Dict, Optional

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

app = FastAPI(title="PySwarm API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Разрешаем всем (для локальной разработки сойдет)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- МОДЕЛЬ ДЛЯ КОНФИГУРАЦИИ ТЕСТА ---
class TestConfig(BaseModel):
    users: int = Field(default=10, ge=1, le=10000)
    spawn_rate: int = Field(default=1, ge=1, le=1000)
    host: str = "http://127.0.0.1:8000"
    duration: int = Field(default=30, ge=5, le=7200)
    wait_time: float = Field(default=1.0, ge=0.0, le=10.0)
    token: Optional[str] = None


# --- МОДЕЛИ ДАННЫХ (Pydantic) ---
# Здесь описание модели, как выглядит ОДИН запрос внутри JSON-файла.
class ApiRequest(BaseModel):
    method: str  # Например, "GET" или "POST"
    url: str  # Куда стучимся (например, "/api/users")
    headers: Optional[Dict[str, str]] = None  # Заголовки (необязательно)
    body: Optional[Dict[str, Any]] = None  # Тело запроса (необязательно)


# --- ЭНДПОИНТЫ ---
def ensure_tmp_dir():
    """Создает папку tmp, если её еще нет."""
    os.makedirs("tmp", exist_ok=True)


@app.post("/api/upload")
async def upload_json(file: UploadFile = File(...)):
    if not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Только JSON файлы разрешены")

    try:
        contents = await file.read()
        parsing_content = json.loads(contents)
        requests_list = [ApiRequest(**item) for item in parsing_content]
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Невалидный JSON формат")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    ensure_tmp_dir()
    with open("tmp/test_data.json", "w", encoding="utf-8") as f:
        json.dump(parsing_content, f, ensure_ascii=False, indent=2)

    return {
        "message": f"Успешно распарсено и сохранено {len(requests_list)} запросов",
        "first_request": requests_list[0],
    }


def cleanup_old_files(folder: str):
    """Удаляет старые файлы результатов перед новым запуском."""
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
    ensure_tmp_dir()
    cleanup_old_files("tmp")

    # Сохраняем настройки в файл, чтобы воркеры их прочитали
    worker_config = {"token": config.token, "wait_time": config.wait_time}
    with open("tmp/config.json", "w", encoding="utf-8") as f:
        json.dump(worker_config, f)

    # Отправляем команду Мастеру Locust (который крутится в отдельном контейнере)
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            # Сначала сбрасываем статистику и запускаем
            await client.get("http://locust_master:8089/stats/reset", timeout=2.0)

            # Формируем запрос на старт
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

        return {"status": "started", "message": "Тест запущен на воркерах"}

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Ошибка связи с Locust Master: {str(e)}"
        )


@app.post("/api/stop")
async def stop_load_test():
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            # Команда "stop" останавливает текущий тест (пользователи перестают генерироваться)
            await client.get("http://locust_master:8089/stop", timeout=2.0)
        return {"status": "stopped", "message": "Тест остановлен"}
    except Exception as e:
        return {"status": "error", "message": f"Ошибка: {str(e)}"}


async def get_latest_metrics() -> Optional[Dict[str, Any]]:
    """Запрашивает статистику напрямую из API Locust."""
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(
                "http://locust_master:8089/stats/requests",
                timeout=3.0,  # <-- Увеличили таймаут, чтобы сервер успевал
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
        # Теперь мы увидим тип ошибки (например, ReadTimeout)
        print(f"❌ Ошибка получения метрик: {type(e).__name__} - {e}")
        return None


@app.websocket("/ws/metrics")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    try:
        while True:
            # Ждем метрики из API (асинхронно, без чтения файлов)
            metrics = await get_latest_metrics()

            if metrics:
                await websocket.send_json(metrics)

            # Ждем секунду перед следующим запросом
            await asyncio.sleep(1)

    except WebSocketDisconnect:
        print("Клиент отключился от WebSocket")


# --- ЭНДПОИНТ ДЛЯ КОНФИГУРАЦИИ ФРОНТЕНДА ---
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
