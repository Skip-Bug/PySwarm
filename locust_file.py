import json
import os
import random

from locust import FastHttpUser, between, events, task

ALFA = 1
OMEGA = 2

REQUESTS = []  # Глобальная переменная, где мы будем хранить наши запросы


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Этот код выполняется один раз при старте Locust."""
    global REQUESTS

    try:
        with open("tmp/test_data.json", "r", encoding="utf-8") as f:
            REQUESTS = json.load(f)
    except FileNotFoundError:
        print("❌ Файл test_data.json не найден! Загрузите его через /api/upload")
        return  # Ранний выход, чтобы не делать лишние отступы

    print(f"✅ Успешно загружено {len(REQUESTS)} запросов из test_data.json")

    token = os.environ.get("PYSWARM_TOKEN")
    if not token:
        return  # Если токена нет, дальше не идем

    print("🔑 Токен найден! Внедряем его в заголовки всех запросов.")
    for req in REQUESTS:
        if "headers" not in req or req["headers"] is None:
            req["headers"] = {}
        req["headers"]["Authorization"] = f"Bearer {token}"


class PySwarmUser(FastHttpUser):
    # Пауза между запросами одного пользователя (от ALFA до OMEGA секунд)
    wait_time = between(ALFA, OMEGA)

    @task
    def dynamic_request(self):
        # Если файл не был загружен или пустой, ничего не делаем
        if not REQUESTS:
            return

        # Выбираем случайный запрос из списка
        req = random.choice(REQUESTS)

        # Достаем данные из выбранного запроса
        method = req.get("method", "GET")
        url = req.get("url", "/")
        headers = req.get("headers", {})
        body = req.get("body", None)

        # Отправляем запрос
        # Используем универсальный метод request(), чтобы поддерживать любые HTTP-методы
        self.client.request(
            method=method,
            url=url,
            headers=headers,
            json=body,
        )
