import asyncio
import contextlib
import csv
import json
import os
import signal
import sys
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

locust_process = None

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
        raise HTTPException(
            status_code=400,
            detail="Только JSON файлы разрешены",
        )

    try:
        contents = await file.read()
        parsing_content = json.loads(contents)

        # Валидируем данные через Pydantic.
        requests_list = [ApiRequest(**item) for item in parsing_content]

        # СОХРАНЯЕМ файл на диск для Locust
        ensure_tmp_dir()

        with open("tmp/test_data.json", "w", encoding="utf-8") as f:
            json.dump(parsing_content, f, ensure_ascii=False, indent=2)

        return {
            "message": f"Успешно распарсено и сохранено {len(requests_list)} запросов",
            "first_request": requests_list[0],
        }

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Невалидный JSON формат")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


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


# --- РЕАЛЬНЫЙ ЗАПУСК ТЕСТА ---
@app.post("/api/start")
async def start_load_test(config: TestConfig):
    ensure_tmp_dir()
    cleanup_old_files("tmp")
    try:
        async with httpx.AsyncClient() as client:
            await client.get("http://127.0.0.1:8089/quit", timeout=1.0)
    except httpx.RequestError:
        # Если Locust не запущен (ошибка соединения) — это нормально, идем дальше
        pass
    command = [
        sys.executable,
        "-m",
        "locust",
        "-f",
        "locust_file.py",
        "-u",
        str(config.users),
        "-r",
        str(config.spawn_rate),
        "--host",
        config.host,
        "--run-time",
        f"{config.duration}s",
        "--csv=tmp/test_results",
        "--csv-full-history",
        "--autostart",
        "--stop-timeout",
        "1",
    ]

    env = os.environ.copy()
    if config.token:
        env["PYSWARM_TOKEN"] = config.token

    global locust_process
    try:
        locust_process = await asyncio.create_subprocess_exec(
            *command,
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )

        return {
            "status": "started",
            "message": "Тест запущен в фоне на 30 секунд",
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка запуска: {str(e)}",
        )


@app.post("/api/stop")
async def stop_load_test():
    global locust_process

    # 1. Просим по-хорошему (подавляем ошибку, если Locust уже мертв)
    with contextlib.suppress(httpx.RequestError):
        async with httpx.AsyncClient() as client:
            await client.get("http://127.0.0.1:8089/quit", timeout=1.0)

    # 2. Если процесса нет или он уже завершился — выходим
    if not locust_process or locust_process.returncode is not None:
        return {"status": "not_running", "message": "Тест уже не запущен"}

    # 3. Добиваем процесс
    if os.name == "nt":
        locust_process.terminate()
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(locust_process.pid), signal.SIGTERM)  # type: ignore

    # 4. Ждем завершения и очищаем
    await locust_process.wait()
    locust_process = None

    return {"status": "killed", "message": "Процесс Locust жестко завершен"}


def get_latest_metrics(file_path: str) -> Optional[Dict[str, Any]]:
    """Читает последнюю непустую строку из CSV и возвращает словарь с метриками."""
    try:
        with open(file_path, mode="r", encoding="utf-8") as f:
            lines = f.readlines()

        # Ищем самую последнюю строку, в которой есть хоть какие-то данные
        last_line = None
        for line in reversed(lines):
            if line.strip():
                last_line = line.strip()
                break

        if not last_line:
            return None

        reader = csv.reader([last_line])
        row = next(reader, None)

        # Проверяем, что в строке достаточно столбцов, чтобы не было IndexError
        if not row or len(row) < 6:
            return None

        return {
            "timestamp": row[0],
            "user_count": row[1],
            "total_rps": row[4],
            "fails": row[5],
        }

    except (FileNotFoundError, PermissionError):
        # Если файла нет (тест не запущен) или он занят (Windows), просто молчим
        return None


@app.websocket("/ws/metrics")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    try:
        while True:
            # Вызываем функцию, она делает всю грязную работу
            metrics = get_latest_metrics("tmp/test_results_stats_history.csv")

            # Если данные есть — отправляем
            if metrics:
                print(f"➡️ ОТПРАВКА: {metrics}")
                await websocket.send_json(metrics)
            else:
                print("⏳ Данных пока нет (файл пуст или заблокирован)")
            # Ждем секунду и повторяем
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
    }


app.mount("/", StaticFiles(directory="static", html=True), name="static")
