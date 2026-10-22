import json
from typing import Any, Dict, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

app = FastAPI(title="PySwarm API")


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
    # 1. Проверяем расширение файла
    if not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Только JSON файлы разрешены")

    try:
        # 2. Читаем файл
        contents = await file.read()

        # 3. Парсим JSON
        parsing_content = json.loads(contents)

        # 4. Валидируем данные через Pydantic.
        requests_list = [ApiRequest(**item) for item in parsing_content]

        # 5. Возвращаем успешный ответ (пока просто количество запросов и первый из них для проверки)
        return {
            "message": f"Успешно распарсено {len(requests_list)} запросов",
            "first_request": requests_list[0],
        }

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Невалидный JSON формат")
    except Exception as e:
        # Ловим ошибки валидации Pydantic
        raise HTTPException(status_code=400, detail=str(e))
