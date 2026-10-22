import asyncio
import csv
import json
import sys
from typing import Any, Dict, Optional

from fastapi import (
    FastAPI,
    File,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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
    users: int = 10
    spawn_rate: int = 1
    host: str = "http://127.0.0.1:8000"


# --- МОДЕЛИ ДАННЫХ (Pydantic) ---
# Здесь описание модели, как выглядит ОДИН запрос внутри JSON-файла.
class ApiRequest(BaseModel):
    method: str  # Например, "GET" или "POST"
    url: str  # Куда стучимся (например, "/api/users")
    headers: Optional[Dict[str, str]] = None  # Заголовки (необязательно)
    body: Optional[Dict[str, Any]] = None  # Тело запроса (необязательно)


# --- ЭНДПОИНТЫ ---
@app.get("/")
async def root():
    return {"message": "PySwarm Backend is running!"}


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
        with open("test_data.json", "w", encoding="utf-8") as f:
            json.dump(parsing_content, f, ensure_ascii=False, indent=2)

        return {
            "message": f"Успешно распарсено и сохранено {len(requests_list)} запросов",
            "first_request": requests_list[0],
        }

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Невалидный JSON формат")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- РЕАЛЬНЫЙ ЗАПУСК ТЕСТА ---
@app.post("/api/start")
async def start_load_test(config: TestConfig):
    command = [
        sys.executable,
        "-m",
        "locust",
        "-f",
        "locust_file.py",
        "--headless",
        "-u",
        str(config.users),
        "-r",
        str(config.spawn_rate),
        "--host",
        config.host,
        "--run-time",
        "30s",  # Увеличим до 30 секунд, чтобы было время посмотреть график
        "--csv=test_results",
        "--csv-full-history",
    ]

    try:
        # Запускаем в фоне. stdout/stderr уводим в DEVNULL, чтобы не забивать память
        await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
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
            metrics = get_latest_metrics("test_results_stats_history.csv")

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
