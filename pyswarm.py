"""
PySwarm standalone (с защитой от переполнения памяти).
Run:   python pyswarm.py
Build: pyinstaller --onefile --name pyswarm pyswarm.py
"""

import asyncio
import json
import logging
import multiprocessing
import os
import random
import sys
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import httpx
import psutil
import uvicorn
from fastapi import (
    FastAPI,
    File,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pyswarm")

LOCUST_HOST = "127.0.0.1"
LOCUST_PORT = 8089
BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8000

# --- НАСТРОЙКИ ЗАЩИТЫ ---
MAX_SYSTEM_MEMORY_PERCENT = 85  # Останавливать тест, если RAM загружена на 85%
MAX_LOCUST_PROCESS_MEM_MB = 2048  # Останавливать, если процесс Locust съел > 2 ГБ
MAX_UPLOAD_SIZE_MB = 50  # Максимальный размер загружаемого JSON

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_DIR = os.path.join(BASE_DIR, "tmp")

# -------- embedded static --------
INDEX_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>PySwarm Dashboard</title>
    <link rel="stylesheet" href="/style.css?v=2">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body>
    <div class="controls">
        <div class="controls-row">
            <div class="control-block">
                <label>Пользователи (Нагрузка)</label>
                <div class="input-wrapper">
                    <input type="range" id="usersRange">
                    <input type="number" id="usersInput" class="styled-input" style="width: 60px; text-align: center;">
                </div>
            </div>
            <div class="control-block">
                <label>Скорость (юзеров/сек)</label>
                <div class="input-wrapper">
                    <input type="range" id="spawnRange">
                    <input type="number" id="spawnInput" class="styled-input" style="width: 60px; text-align: center;">
                </div>
            </div>
            <div class="control-block">
                <label>Длительность (сек)</label>
                <div class="input-wrapper">
                    <input type="range" id="durationRange">
                    <input type="number" id="durationInput" class="styled-input" style="width: 60px; text-align: center;">
                </div>
            </div>
            <div class="control-block">
                <label>Задержка (сек)</label>
                <div class="input-wrapper">
                    <input type="range" id="waitRange">
                    <input type="number" id="waitInput" class="styled-input" style="width: 60px; text-align: center;" step="0.1">
                </div>
            </div>
        </div>

        <div class="controls-row">
            <div class="control-block">
                <label>Целевой URL</label>
                <input type="text" id="hostInput" value="http://127.0.0.1:8000" class="styled-input" style="width: 350px;">
            </div>
            <div class="control-block">
                <label>Bearer Токен (опционально)</label>
                <input type="text" id="tokenInput" placeholder="Вставьте токен..." class="styled-input" style="width: 350px;">
            </div>
            <div class="control-block">
                <label>Режим контура</label>
                <div style="display: flex; align-items: center; height: 38px;">
                    <button type="button" id="loopModeBtn" class="btn-closed" onclick="toggleLoopMode()">Закрытый контур</button>
                </div>
            </div>
        </div>

        <div class="controls-row">
            <div class="control-block">
                <label>Папка с JSON</label>
                <div style="display: flex; align-items: center; gap: 15px;">
                    <input type="file" id="folderInput" webkitdirectory directory multiple class="styled-input" style="width: 220px; font-size: 13px;">
                    <span id="uploadStatus" style="font-size: 14px; color: #4CAF50;"></span>
                </div>
            </div>
            <div class="control-block">
                <button onclick="startTest()" id="startBtn" style="padding: 10px 30px;">Запустить тест</button>
                <button onclick="stopTest()" id="stopBtn" style="background: #F44336; padding: 10px 30px; display: none;">Остановить</button>
            </div>
        </div>

        <div id="status" style="text-align: center; font-weight: bold; color: #ff9800; font-size: 18px; margin-top: 10px;">Статус: Ожидание</div>
    </div>
    <div class="container">
        <div class="card">
            <h2>RPS (Запросы в секунду)</h2>
            <canvas id="rpsChart"></canvas>
        </div>
        <div class="card">
            <h2>Пользователи / Ошибки</h2>
            <canvas id="usersChart"></canvas>
        </div>
    </div>
    <div class="container" style="margin-top: 20px;">
        <div class="card" style="width: 95%;">
            <h2 style="display: flex; justify-content: space-between; align-items: center;">
                <span>Детальная статистика по эндпоинтам</span>
                <span id="cpuLoad" style="color: #ff9800; font-size: 14px; margin: 0;">CPU: 0%</span>
            </h2>
            <table style="width: 100%; border-collapse: collapse; text-align: left; font-size: 14px;">
                <thead>
                    <tr style="border-bottom: 2px solid #555; color: #bbb;">
                        <th style="padding: 10px;">Метод</th>
                        <th style="padding: 10px;">Эндпоинт</th>
                        <th style="padding: 10px; color: #4CAF50;">Среднее (Latency)</th>
                        <th style="padding: 10px;">p50 (медиана)</th>
                        <th style="padding: 10px;">p90</th>
                        <th style="padding: 10px;">p95</th>
                    </tr>
                </thead>
                <tbody id="statsTableBody">
                </tbody>
            </table>
        </div>
    </div>
    <script src="/script.js"></script>
</body>
</html>"""

STYLE_CSS = """body { font-family: sans-serif; background: #1e1e1e; color: #fff; margin: 20px; }
.container { display: flex; flex-wrap: wrap; gap: 20px; justify-content: center; }
.card { background: #2d2d2d; padding: 20px; border-radius: 8px; width: 45%; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
h2 { text-align: center; font-size: 18px; margin-bottom: 15px; color: #4CAF50; }
.controls { text-align: center; margin-bottom: 20px; }
button { padding: 10px 20px; font-size: 16px; background: #4CAF50; color: white; border: none; border-radius: 4px; cursor: pointer; }
button:hover { background: #45a049; }
#status { text-align: center; margin-top: 10px; font-weight: bold; color: #ff9800; }
.control-group { display: inline-flex; flex-direction: column; align-items: center; margin: 0 20px; vertical-align: top; }
.control-group label { font-size: 14px; color: #bbb; margin-bottom: 8px; }
.input-wrapper { display: flex; align-items: center; gap: 10px; }
input[type="range"] { cursor: pointer; accent-color: #4CAF50; }
input[type="number"] { width: 60px; padding: 5px; background: #333; color: #fff; border: 1px solid #555; border-radius: 4px; text-align: center; }
.controls-row { display: flex; justify-content: center; align-items: flex-end; gap: 30px; margin-bottom: 20px; flex-wrap: wrap; }
.control-block { display: flex; flex-direction: column; align-items: center; }
.control-block label { font-size: 14px; color: #bbb; margin-bottom: 8px; }
.styled-input { padding: 8px; background: #333; color: #fff; border: 1px solid #555; border-radius: 4px; }
#loopModeBtn { width: 180px; height: 38px; padding: 0; box-sizing: border-box; display: flex; justify-content: center; align-items: center; white-space: nowrap; font-weight: bold; font-size: 14px; border-radius: 6px; border: 2px solid transparent; transition: all 0.2s ease-in-out; color: #fff; cursor: pointer; }
#loopModeBtn.btn-closed { background-color: #2196F3 !important; border-color: #1976D2 !important; }
#loopModeBtn.btn-closed:hover { background-color: #1E88E5 !important; box-shadow: 0 0 10px rgba(33, 150, 243, 0.4); }
#loopModeBtn.btn-open { background-color: #ff9800 !important; border-color: #f57c00 !important; }
#loopModeBtn.btn-open:hover { background-color: #fb8c00 !important; box-shadow: 0 0 10px rgba(255, 152, 0, 0.4); }"""

SCRIPT_JS = """const API_BASE = window.location.origin;
const WS_URL = (window.location.protocol === 'https:' ? 'wss://' : 'ws://') + window.location.host + '/ws/metrics';
let isOpenLoop = false;
const usersRange = document.getElementById('usersRange');
const usersInput = document.getElementById('usersInput');
const spawnRange = document.getElementById('spawnRange');
const spawnInput = document.getElementById('spawnInput');
const durationRange = document.getElementById('durationRange');
const durationInput = document.getElementById('durationInput');
const waitRange = document.getElementById('waitRange');
const waitInput = document.getElementById('waitInput');

usersRange.addEventListener('input', () => { usersInput.value = usersRange.value; });
usersInput.addEventListener('input', () => { usersRange.value = usersInput.value; });

spawnRange.addEventListener('input', () => { spawnInput.value = spawnRange.value; });
spawnInput.addEventListener('input', () => { spawnRange.value = spawnInput.value; });

durationRange.addEventListener('input', () => { durationInput.value = durationRange.value; });
durationInput.addEventListener('input', () => { durationRange.value = durationInput.value; });

waitRange.addEventListener('input', () => { waitInput.value = waitRange.value; });
waitInput.addEventListener('input', () => { waitRange.value = waitInput.value; });

async function loadConfig() {
    try {
        const response = await fetch(API_BASE + '/api/config');
        const config = await response.json();

        usersRange.min = usersInput.min = config.users.min;
        usersRange.max = usersInput.max = config.users.max;
        usersRange.step = usersInput.step = config.users.step;
        usersRange.value = usersInput.value = config.users.default;

        spawnRange.min = spawnInput.min = config.spawn.min;
        spawnRange.max = spawnInput.max = config.spawn.max;
        spawnRange.step = spawnInput.step = config.spawn.step;
        spawnRange.value = spawnInput.value = config.spawn.default;

        durationRange.min = durationInput.min = config.duration.min;
        durationRange.max = durationInput.max = config.duration.max;
        durationRange.step = durationInput.step = config.duration.step;
        durationRange.value = durationInput.value = config.duration.default;

        waitRange.min = waitInput.min = config.wait.min;
        waitRange.max = waitInput.max = config.wait.max;
        waitRange.step = waitInput.step = config.wait.step;
        waitRange.value = waitInput.value = config.wait.default;

    } catch (error) {
        console.error("Не удалось загрузить конфигурацию:", error);
    }
}

loadConfig();

const commonOptions = {
    responsive: true,
    animation: false,
    scales: {
        x: { display: false },
        y: { beginAtZero: true, grid: { color: '#444' }, ticks: { color: '#ccc' } }
    },
    plugins: { legend: { labels: { color: '#ccc' } } }
};

const rpsChart = new Chart(document.getElementById('rpsChart'), {
    type: 'line',
    data: {
        labels: [],
        datasets: [{ label: 'RPS', data: [], borderColor: '#4CAF50', backgroundColor: 'rgba(76, 175, 80, 0.2)', fill: true, tension: 0.3, pointRadius: 0 }]
    },
    options: commonOptions
});

const usersChart = new Chart(document.getElementById('usersChart'), {
    type: 'line',
    data: {
        labels: [],
        datasets: [
            { label: 'Пользователи', data: [], borderColor: '#2196F3', tension: 0.3, pointRadius: 0 },
            { label: 'Ошибки', data: [], borderColor: '#F44336', tension: 0.3, pointRadius: 0 }
        ]
    },
    options: commonOptions
});

let ws;

function toggleLoopMode() {
    isOpenLoop = !isOpenLoop;
    const btn = document.getElementById('loopModeBtn');

    if (isOpenLoop) {
        btn.innerText = "Открытый контур";
        btn.className = "btn-open";
    } else {
        btn.innerText = "Закрытый контур";
        btn.className = "btn-closed";
    }
}

function startTest() {
    document.getElementById('status').innerText = "Статус: Запуск Locust...";

    const users = parseInt(usersInput.value);
    const spawnRate = parseInt(spawnInput.value);
    const duration = parseInt(durationInput.value);
    const wait_time = parseFloat(waitInput.value);
    const token = document.getElementById('tokenInput').value.trim();
    const host = document.getElementById('hostInput').value.trim();
    const is_open_loop = isOpenLoop;
    fetch(API_BASE + '/api/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            users: users,
            spawn_rate: spawnRate,
            host: host,
            duration: duration,
            wait_time: wait_time,
            token: token,
            is_open_loop: is_open_loop
        })
    })
    .then(response => response.json())
    .then(data => {
        document.getElementById('status').innerText = `Статус: Тест идет (${duration} сек)...`;

        document.getElementById('startBtn').style.display = 'none';
        document.getElementById('stopBtn').style.display = 'inline-block';

        rpsChart.data.labels = [];
        rpsChart.data.datasets[0].data = [];
        usersChart.data.labels = [];
        usersChart.data.datasets[0].data = [];
        usersChart.data.datasets[1].data = [];
        rpsChart.update();
        usersChart.update();

        connectWebSocket();
        setTimeout(() => {
            document.getElementById('status').innerText = "Статус: Тест завершен";
            document.getElementById('startBtn').style.display = 'inline-block';
            document.getElementById('stopBtn').style.display = 'none';
            if (ws) ws.close();
        }, (duration + 2) * 1000);
    })
    .catch(error => {
        document.getElementById('status').innerText = "Ошибка: " + error;
    });
}
async function stopTest() {
    try {
        const response = await fetch(API_BASE + '/api/stop', { method: 'POST' });
        if (!response.ok) {
            throw new Error(`Ошибка: ${response.status} ${response.statusText}`);
        }

        document.getElementById('status').innerText = "Статус: Тест остановлен (вручную)";

        document.getElementById('startBtn').style.display = 'inline-block';
        document.getElementById('stopBtn').style.display = 'none';

        if (ws) ws.close();
    } catch (error) {
        console.error("Ошибка при остановке:", error);
    }
}
function connectWebSocket() {
    if (ws) ws.close();

    ws = new WebSocket(WS_URL);

    ws.onmessage = (event) => {

        const data = JSON.parse(event.data);
        
        // Если сервер прислал сигнал о критической памяти
        if (data.error_msg) {
             document.getElementById('status').innerText = data.error_msg;
             document.getElementById('status').style.color = "#F44336";
             document.getElementById('startBtn').style.display = 'inline-block';
             document.getElementById('stopBtn').style.display = 'none';
             if (ws) ws.close();
             return;
        }

        rpsChart.data.labels.push(data.timestamp);
        rpsChart.data.datasets[0].data.push(parseFloat(data.total_rps) || 0);

        usersChart.data.labels.push(data.timestamp);
        usersChart.data.datasets[0].data.push(parseInt(data.user_count) || 0);
        usersChart.data.datasets[1].data.push(parseInt(data.fails) || 0);

        if (rpsChart.data.labels.length > 30) {
            rpsChart.data.labels.shift();
            rpsChart.data.datasets[0].data.shift();
            usersChart.data.labels.shift();
            usersChart.data.datasets[0].data.shift();
            usersChart.data.datasets[1].data.shift();
        }

        rpsChart.update();
        usersChart.update();

        if (data.cpu_load !== undefined) {
            document.getElementById('cpuLoad').innerText = `CPU: ${data.cpu_load}% | RAM: ${data.mem_load}%`;
        }

        if (data.stats_table) {
            const tbody = document.getElementById('statsTableBody');
            tbody.innerHTML = '';

            data.stats_table.forEach(stat => {
                const tr = document.createElement('tr');
                tr.style.borderBottom = '1px solid #444';
                tr.innerHTML = `
                    <td style="padding: 8px; font-weight: bold; color: ${stat.method === 'GET' ? '#4CAF50' : '#2196F3'};">${stat.method}</td>
                    <td style="padding: 8px;">${stat.name}</td>
                    <td style="padding: 8px; color: #4CAF50;">${stat.avg} ms</td>
                    <td style="padding: 8px;">${stat.p50} ms</td>
                    <td style="padding: 8px;">${stat.p90} ms</td>
                    <td style="padding: 8px;">${stat.p95} ms</td>
                `;
                tbody.appendChild(tr);
            });
        }
    };

    ws.onclose = (event) => {
        console.log("WebSocket закрыт.");
        if (document.getElementById('status').innerText.includes("Тест идет")) {
            document.getElementById('status').innerText = "Статус: Тест завершен";
            document.getElementById('startBtn').style.display = 'inline-block';
            document.getElementById('stopBtn').style.display = 'none';
        }
    };
    ws.onerror = (error) => {
        console.log("Ошибка WebSocket:", error);
    }
}

document.getElementById('folderInput').addEventListener('change', async (event) => {
    const files = Array.from(event.target.files);
    if (!files.length) return;

    const jsonFiles = files.filter(f => f.name.endsWith('.json'));
    if (!jsonFiles.length) {
        document.getElementById('uploadStatus').innerText = "❌ В папке нет .json файлов";
        document.getElementById('uploadStatus').style.color = "#F44336";
        return;
    }

    const formData = new FormData();
    jsonFiles.forEach(file => {
        formData.append("files", file);
    });

    const statusEl = document.getElementById('uploadStatus');
    statusEl.innerText = `⏳ Загрузка ${jsonFiles.length} файлов...`;
    statusEl.style.color = "#ff9800";

    try {
        const response = await fetch(API_BASE + '/api/upload_folder', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();

        if (response.ok) {
            let msg = `✅ Загружено файлов: ${data.saved_files}, запросов: ${data.total_requests}`;
            if (data.skipped_files > 0) {
                msg += ` (пропущено: ${data.skipped_files})`;
            }
            statusEl.innerText = msg;
            statusEl.style.color = "#4CAF50";

            console.log("Сводка по файлам:", data.files);
        } else {
            statusEl.innerText = `❌ Ошибка: ${data.detail || 'неизвестная'}`;
            statusEl.style.color = "#F44336";
        }
    } catch (error) {
        statusEl.innerText = `❌ Ошибка соединения`;
        statusEl.style.color = "#F44336";
    }
});"""


# ================= Locust subprocess =================
def run_locust_process():
    """Entry point для дочернего процесса: Locust standalone (web-режим)."""
    from locust import FastHttpUser, constant, constant_throughput, events, task
    from locust.env import Environment

    REQUESTS: List[dict] = []

    class PySwarmUser(FastHttpUser):
        wait_time = constant(1.0)

        @task
        def dynamic_request(self):
            if not REQUESTS:
                return
            req = random.choice(REQUESTS)
            # FastHttpUser по умолчанию не сохраняет response тела в памяти
            self.client.request(
                method=req.get("method", "GET"),
                url=req.get("url", "/"),
                headers=req.get("headers", {}) or {},
                json=req.get("body"),
            )

    # ВАЖНО: передаем глобальный events в Environment
    env = Environment(user_classes=[PySwarmUser], events=events)

    @events.test_start.add_listener
    def on_test_start(environment, **kwargs):
        test_data_path = os.path.join(TMP_DIR, "test_data.json")
        try:
            with open(test_data_path, encoding="utf-8") as f:
                REQUESTS.clear()
                REQUESTS.extend(json.load(f))
            logger.info("Locust: успешно загружено %d запросов", len(REQUESTS))
        except FileNotFoundError:
            logger.error(
                "Locust: Файл test_data.json НЕ НАЙДЕН по пути: %s", test_data_path
            )
            return

        try:
            with open(os.path.join(TMP_DIR, "config.json"), encoding="utf-8") as f:
                cfg = json.load(f)
            token = cfg.get("token")
            if cfg.get("is_open_loop"):
                PySwarmUser.wait_time = constant_throughput(1.0)
            else:
                PySwarmUser.wait_time = constant(cfg.get("wait_time", 1.0))
            if token:
                for r in REQUESTS:
                    r.setdefault("headers", {})["Authorization"] = f"Bearer {token}"
        except FileNotFoundError:
            pass

    env.create_local_runner()
    env.create_web_ui(LOCUST_HOST, LOCUST_PORT)
    logger.info("Locust готов на http://%s:%d", LOCUST_HOST, LOCUST_PORT)
    env.web_ui.greenlet.join()


# ================= FastAPI =================
class TestConfig(BaseModel):
    users: int = Field(default=10, ge=1, le=2000)  # ИЗМЕНЕНО: макс 2000 вместо 10000
    spawn_rate: int = Field(default=1, ge=1, le=500)
    host: str = "http://127.0.0.1:8000"
    duration: int = Field(default=30, ge=5, le=7200)
    wait_time: float = Field(default=1.0, ge=0.0, le=10.0)
    token: Optional[str] = None
    is_open_loop: bool = False


class ApiRequest(BaseModel):
    method: str
    url: str
    headers: Optional[Dict[str, str]] = None
    body: Optional[Dict[str, Any]] = None


def _ensure_tmp():
    os.makedirs(TMP_DIR, exist_ok=True)


# --- ГЛОБАЛЬНЫЙ ФЛАГ ОСТАНОВКИ ИЗ-ЗА ПАМЯТИ ---
MEMORY_CRITICAL = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global MEMORY_CRITICAL
    MEMORY_CRITICAL = False
    _ensure_tmp()
    logger.info("Рабочая папка (tmp): %s", TMP_DIR)

    proc = multiprocessing.Process(target=run_locust_process, daemon=True)
    proc.start()

    # wait for Locust
    for _ in range(100):
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get(
                    f"http://{LOCUST_HOST}:{LOCUST_PORT}/stats/requests", timeout=0.3
                )
                if r.status_code == 200:
                    logger.info("Locust доступен")
                    break
        except Exception:
            pass
        await asyncio.sleep(0.1)
    else:
        logger.error("Locust не поднялся за 10 секунд")

    # Запуск таска мониторинга памяти
    async def memory_watchdog():
        global MEMORY_CRITICAL
        while True:
            try:
                # Проверяем память системы
                sys_mem = psutil.virtual_memory()
                # Проверяем память самого процесса Locust
                p = psutil.Process(proc.pid)
                locust_mem_mb = p.memory_info().rss / 1024 / 1024

                if (
                    sys_mem.percent >= MAX_SYSTEM_MEMORY_PERCENT
                    or locust_mem_mb > MAX_LOCUST_PROCESS_MEM_MB
                ):
                    logger.warning(
                        "MEMORY WATCHDOG: Критическое потребление памяти! Sys: %d%%, Locust: %d MB",
                        sys_mem.percent,
                        locust_mem_mb,
                    )
                    MEMORY_CRITICAL = True
                    async with httpx.AsyncClient() as client:
                        try:
                            await client.post(
                                f"http://{LOCUST_HOST}:{LOCUST_PORT}/stop"
                            )
                        except Exception:
                            pass
            except Exception:
                pass
            await asyncio.sleep(2)

    watchdog_task = asyncio.create_task(memory_watchdog())

    yield

    watchdog_task.cancel()
    if proc.is_alive():
        proc.terminate()


app = FastAPI(title="PySwarm standalone", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
async def index():
    return INDEX_HTML


@app.get("/style.css")
async def style():
    return Response(STYLE_CSS, media_type="text/css")


@app.get("/script.js")
async def script():
    return Response(SCRIPT_JS, media_type="application/javascript")


@app.post("/api/upload_folder")
async def upload_folder(files: List[UploadFile] = File(...)):
    _ensure_tmp()
    all_requests, results = [], []
    max_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024

    for file in files:
        name = os.path.basename(file.filename or "unknown.json")
        if not name.endswith(".json"):
            results.append({"file": name, "status": "skipped", "reason": "не .json"})
            continue

        try:
            contents = await file.read()
            if len(contents) > max_bytes:
                results.append(
                    {
                        "file": name,
                        "status": "skipped",
                        "reason": f"файл больше {MAX_UPLOAD_SIZE_MB}МБ",
                    }
                )
                continue
            data = json.loads(contents)
        except json.JSONDecodeError as e:
            results.append(
                {"file": name, "status": "skipped", "reason": f"битый JSON: {e}"}
            )
            continue
        except Exception as e:
            results.append({"file": name, "status": "skipped", "reason": str(e)})
            continue

        if not isinstance(data, list):
            results.append({"file": name, "status": "skipped", "reason": "не список"})
            continue
        try:
            [ApiRequest(**it) for it in data]
        except Exception as e:
            results.append(
                {"file": name, "status": "skipped", "reason": f"невалидный запрос: {e}"}
            )
            continue
        all_requests.extend(data)
        results.append({"file": name, "status": "ok", "count": len(data)})

    if not all_requests:
        return {
            "saved_files": 0,
            "skipped_files": len(results),
            "total_requests": 0,
            "files": results,
        }

    with open(os.path.join(TMP_DIR, "test_data.json"), "w", encoding="utf-8") as f:
        json.dump(all_requests, f, ensure_ascii=False, indent=2)

    saved = sum(1 for r in results if r["status"] == "ok")
    return {
        "saved_files": saved,
        "skipped_files": len(results) - saved,
        "total_requests": len(all_requests),
        "files": results,
    }


@app.post("/api/start")
async def start_load_test(config: TestConfig):
    global MEMORY_CRITICAL
    MEMORY_CRITICAL = False  # Сбрасываем флаг при новом запуске

    _ensure_tmp()
    with open(os.path.join(TMP_DIR, "config.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "token": config.token,
                "wait_time": config.wait_time,
                "is_open_loop": config.is_open_loop,
            },
            f,
        )
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            await client.post(
                f"http://{LOCUST_HOST}:{LOCUST_PORT}/stats/reset", timeout=2.0
            )
            r = await client.post(
                f"http://{LOCUST_HOST}:{LOCUST_PORT}/swarm",
                data={
                    "user_count": config.users,
                    "spawn_rate": config.spawn_rate,
                    "host": config.host,
                    "run_time": f"{config.duration}s",
                },
                timeout=5.0,
            )
            r.raise_for_status()
        return {"status": "started", "message": "Тест запущен"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Locust: {e}")


@app.post("/api/stop")
async def stop_load_test():
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            await client.post(f"http://{LOCUST_HOST}:{LOCUST_PORT}/stop", timeout=2.0)
        return {"status": "stopped"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


async def get_latest_metrics() -> Optional[Dict[str, Any]]:
    global MEMORY_CRITICAL
    try:
        cpu_load = psutil.cpu_percent()
        mem_load = psutil.virtual_memory().percent

        if MEMORY_CRITICAL:
            return {
                "error_msg": "🛑 ТЕСТ ОСТАНОВЛЕН: Превышен лимит потребления памяти!"
            }

        async with httpx.AsyncClient(trust_env=False) as client:
            r = await client.get(
                f"http://{LOCUST_HOST}:{LOCUST_PORT}/stats/requests", timeout=3.0
            )
            r.raise_for_status()
            data = r.json()

        if data.get("state") not in ("running", "spawning", "ready"):
            return None

        total_fails = sum(s.get("num_failures", 0) for s in data.get("stats", []))
        stats_table = [
            {
                "name": s.get("name", ""),
                "method": s.get("method", "GET"),
                "avg": round(s.get("avg_response_time", 0), 1),
                "p50": s.get("median_response_time", 0),
                "p90": s.get("response_time_percentile_0.9", 0),
                "p95": s.get("response_time_percentile_0.95", 0),
            }
            for s in data.get("stats", [])
            if s.get("name") != "Aggregated"
        ]
        return {
            "timestamp": str(int(asyncio.get_running_loop().time())),
            "user_count": str(data.get("user_count", 0)),
            "total_rps": str(round(data.get("total_rps", 0), 2)),
            "fails": str(total_fails),
            "cpu_load": cpu_load,
            "mem_load": mem_load,
            "stats_table": stats_table,
        }
    except Exception as e:
        logger.error("metrics: %s", e)
        return None


@app.websocket("/ws/metrics")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            m = await get_latest_metrics()
            if m:
                await websocket.send_json(m)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass


@app.get("/api/config")
async def get_ui_config():
    # ИЗМЕНЕНО: max users теперь 2000, а не 10000
    return {
        "users": {
            "min": 1,
            "max": int(os.getenv("MAX_USERS_MAX", 2000)),
            "step": 1,
            "default": int(os.getenv("MAX_USERS_DEFAULT", 20)),
        },
        "spawn": {"min": 1, "max": 200, "step": 1, "default": 2},
        "duration": {
            "min": 5,
            "max": 7200,
            "step": 5,
            "default": int(os.getenv("TEST_DURATION_DEFAULT", 30)),
        },
        "wait": {"min": 0.0, "max": 5.0, "step": 0.1, "default": 1.0},
    }


# ================= main =================
if __name__ == "__main__":
    multiprocessing.freeze_support()
    uvicorn.run(app, host=BACKEND_HOST, port=BACKEND_PORT)
