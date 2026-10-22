import asyncio
import csv
import json
import sys
from typing import Any, Dict, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

app = FastAPI(title="PySwarm API")


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
    # Проверяем расширение файла
    if not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Только JSON файлы разрешены")

    try:
        # Читаем файл
        contents = await file.read()

        # Парсим JSON
        parsing_content = json.loads(contents)

        # Валидируем данные через Pydantic.
        requests_list = [ApiRequest(**item) for item in parsing_content]

        # Возвращаем успешный ответ (пока просто количество запросов и первый из них для проверки)
        return {
            "message": f"Успешно распарсено {len(requests_list)} запросов",
            "first_request": requests_list[0],
        }

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Невалидный JSON формат")
    except Exception as e:
        # Ловим ошибки валидации Pydantic
        raise HTTPException(status_code=400, detail=str(e))


# --- РЕАЛЬНЫЙ ЗАПУСК ТЕСТА ---
@app.post("/api/start")
async def start_load_test(config: TestConfig):
    # Формируем команду
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
        "10s",
        "--csv=test_results",
    ]

    try:
        # Запускаем процесс и ждем
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        # Читаем CSV файл
        metrics = {}
        try:
            with open("test_results_stats.csv", mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row["Name"] == "Aggregated":
                        metrics = {
                            "requests": int(row["Request Count"]),
                            "fails": int(row["Failure Count"]),
                            "median_ms": float(row["Median Response Time"]),
                            "rps": float(row["Requests/s"]),
                        }
                        break
        except FileNotFoundError:
            metrics = {"error": "Файл статистики не найден"}

        # Возвращаем ЕДИНСТВЕННЫЙ финальный результат
        return {
            "status": "finished",
            "exit_code": process.returncode,
            "metrics": metrics,
            "output": stdout.decode()[-500:],  # Вернули лог
            "errors": stderr.decode()[-500:],  # Вернули ошибки
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка запуска: {str(e)}",
        )
