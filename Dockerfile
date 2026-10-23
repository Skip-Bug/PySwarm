# Берем облегченный Python 3.13
FROM python:3.13-slim

# Устанавливаем рабочую папку
WORKDIR /app

# Копируем только зависимости (для кэширования)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь остальной код
COPY . .

# Открываем порт для FastAPI
EXPOSE 8000

# Команда запуска
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]