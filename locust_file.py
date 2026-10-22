from locust import FastHttpUser, between, task

ALFA = 1
OMEGA = 2


class PySwarmUser(FastHttpUser):
    # Пауза между запросами одного пользователя (от 1 до 2 секунд)
    wait_time = between(ALFA, OMEGA)

    @task
    def check_root(self):
        # Стучимся на главную страницу нашего API
        self.client.get("/")
