import json
import random

from locust import FastHttpUser, constant, events, task

REQUESTS = []


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Этот код выполняется один раз при старте Locust на каждом воркере."""
    global REQUESTS

    # Загружаем сами запросы
    try:
        with open("tmp/test_data.json", "r", encoding="utf-8") as f:
            REQUESTS = json.load(f)
        print(f"✅ Успешно загружено {len(REQUESTS)} запросов")
    except FileNotFoundError:
        print("❌ Файл test_data.json не найден!")
        return

    # Загружаем настройки (токен и задержку), которые сохранил FastAPI
    try:
        with open("tmp/config.json", "r", encoding="utf-8") as f:
            config = json.load(f)

        token = config.get("token")
        wait_time = config.get("wait_time", 1.0)

        # Динамически меняем задержку для всех пользователей
        PySwarmUser.wait_time = constant(wait_time)
        print(f"⚙️ Применена задержка: {wait_time} сек.")

        # Внедряем токен
        if token:
            print("🔑 Токен найден! Внедряем в заголовки.")
            for req in REQUESTS:
                if "headers" not in req or req["headers"] is None:
                    req["headers"] = {}
                req["headers"]["Authorization"] = f"Bearer {token}"

    except FileNotFoundError:
        print("⚠️ Файл config.json не найден, используем настройки по умолчанию.")


class PySwarmUser(FastHttpUser):
    # Значение по умолчанию (перезапишется из config.json)
    wait_time = constant(1.0)

    @task
    def dynamic_request(self):
        if not REQUESTS:
            return

        req = random.choice(REQUESTS)
        method = req.get("method", "GET")
        url = req.get("url", "/")
        headers = req.get("headers", {})
        body = req.get("body", None)

        self.client.request(
            method=method,
            url=url,
            headers=headers,
            json=body,
        )
