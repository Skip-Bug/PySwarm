const API_BASE = window.location.origin;
const WS_URL = (window.location.protocol === 'https:' ? 'wss://' : 'ws://') + window.location.host + '/ws/metrics';
// Синхронизация бегунков и полей ввода
const usersRange = document.getElementById('usersRange');
const usersInput = document.getElementById('usersInput');
const spawnRange = document.getElementById('spawnRange');
const spawnInput = document.getElementById('spawnInput');
const durationRange = document.getElementById('durationRange');
const durationInput = document.getElementById('durationInput');
const waitRange = document.getElementById('waitRange');
const waitInput = document.getElementById('waitInput');

// Когда двигаем бегунок пользователей
usersRange.addEventListener('input', () => { usersInput.value = usersRange.value; });
// Когда вводим цифры в поле пользователей
usersInput.addEventListener('input', () => { usersRange.value = usersInput.value; });

// То же самое для скорости подключения
spawnRange.addEventListener('input', () => { spawnInput.value = spawnRange.value; });
spawnInput.addEventListener('input', () => { spawnRange.value = spawnInput.value; });

// Когда вводим цифры в поле время теста
durationRange.addEventListener('input', () => { durationInput.value = durationRange.value; });
durationInput.addEventListener('input', () => { durationRange.value = durationInput.value; });

// Синхронизация задержки
waitRange.addEventListener('input', () => { waitInput.value = waitRange.value; });
waitInput.addEventListener('input', () => { waitRange.value = waitInput.value; });

// Загружаем конфигурацию с бэкенда при старте страницы
async function loadConfig() {
    try {
        const response = await fetch(API_BASE + '/api/config');
        const config = await response.json();
        
        // Применяем полученные настройки к ползункам и полям
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

// Вызываем функцию при загрузке страницы
loadConfig();

// Настройка графиков
const commonOptions = {
    responsive: true,
    animation: false,
    scales: {
        x: { display: false }, // Скрываем ось X для чистоты
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

function startTest() {
    document.getElementById('status').innerText = "Статус: Запуск Locust...";
    
    // Берем значения из полей ввода
    const users = parseInt(usersInput.value);
    const spawnRate = parseInt(spawnInput.value);
    const duration = parseInt(durationInput.value); 
    const wait_time = parseFloat(waitInput.value);
    const token = document.getElementById('tokenInput').value.trim();
    const host = document.getElementById('hostInput').value.trim();
    //  Стучимся в наш API, чтобы запустить Locust
    fetch(API_BASE + '/api/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
            users: users, 
            spawn_rate: spawnRate, 
            host: host, 
            duration: duration,
            wait_time: wait_time,
            token: token 
        })
    })
    .then(response => response.json())
    .then(data => {
        document.getElementById('status').innerText = `Статус: Тест идет (${duration} сек)...`;               
        
        // Переключаем кнопки
        document.getElementById('startBtn').style.display = 'none';
        document.getElementById('stopBtn').style.display = 'inline-block';                
        
        // Очищаем старые данные на графиках
        rpsChart.data.labels = [];
        rpsChart.data.datasets[0].data = [];
        usersChart.data.labels = [];
        usersChart.data.datasets[0].data = [];
        usersChart.data.datasets[1].data = [];
        rpsChart.update();
        usersChart.update();

        connectWebSocket();
        // Таймер завершения (duration секунд + 2 секунды запаса на запись последних логов)
        setTimeout(() => {
            document.getElementById('status').innerText = "Статус: Тест завершен";
            // Возвращаем кнопки
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
        
        document.getElementById('status').innerText = "Статус: Тест остановлен";
        
        // Возвращаем кнопки в исходное состояние
        document.getElementById('startBtn').style.display = 'inline-block';
        document.getElementById('stopBtn').style.display = 'none';
        
        // Закрываем веб-сокет
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
        
        // Добавляем новые данные на график
        rpsChart.data.labels.push(data.timestamp);
        rpsChart.data.datasets[0].data.push(parseFloat(data.total_rps) || 0);
        
        usersChart.data.labels.push(data.timestamp);
        usersChart.data.datasets[0].data.push(parseInt(data.user_count) || 0);
        usersChart.data.datasets[1].data.push(parseInt(data.fails) || 0);

        // Ограничиваем графики 30 точками (чтобы не уезжали за экран)
        if (rpsChart.data.labels.length > 30) {
            rpsChart.data.labels.shift();
            rpsChart.data.datasets[0].data.shift();
            usersChart.data.labels.shift();
            usersChart.data.datasets[0].data.shift();
            usersChart.data.datasets[1].data.shift();
        }

        rpsChart.update();
        usersChart.update();
    };

    ws.onclose = () => {
        console.log("WebSocket закрыт. Код:", event.code, "Причина:", event.reason);
        document.getElementById('status').innerText = "Статус: Тест завершен";
        
        document.getElementById('startBtn').style.display = 'inline-block';
        document.getElementById('stopBtn').style.display = 'none';
    };
    ws.onerror = (error) => {
        console.log("Ошибка WebSocket:", error);
    }
}

// Обработка загрузки папки с JSON-файлами
document.getElementById('folderInput').addEventListener('change', async (event) => {
    const files = Array.from(event.target.files);
    if (!files.length) return;

    // Оставляем только .json файлы (на клиенте, чтобы не гонять лишнее по сети)
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
            
            // Выводим в консоль детали по каждому файлу (для отладки)
            console.log("Сводка по файлам:", data.files);
        } else {
            statusEl.innerText = `❌ Ошибка: ${data.detail || 'неизвестная'}`;
            statusEl.style.color = "#F44336";
        }
    } catch (error) {
        statusEl.innerText = `❌ Ошибка соединения`;
        statusEl.style.color = "#F44336";
    }
});