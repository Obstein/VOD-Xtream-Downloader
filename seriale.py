from flask import Blueprint, request, jsonify, render_template_string
import os
import requests
import subprocess
from urllib.parse import quote
import json
import sys
import threading
import queue
import time

seriale_bp = Blueprint('seriale', __name__, url_prefix='/seriale')

XTREAM_HOST = os.getenv("XTREAM_HOST")
XTREAM_PORT = os.getenv("XTREAM_PORT")
XTREAM_USERNAME = os.getenv("XTREAM_USERNAME")
XTREAM_PASSWORD = os.getenv("XTREAM_PASSWORD")
DOWNLOAD_PATH_SERIES = os.getenv("DOWNLOAD_PATH_SERIES", "/downloads/Seriale")
RETRY_COUNT = int(os.getenv("RETRY_COUNT", 3))
QUEUE_FILE = "queue.json"
DOWNLOAD_LOG_FILE = "downloads.log"

BASE_API = f"{XTREAM_HOST}:{XTREAM_PORT}/player_api.php?username={XTREAM_USERNAME}&password={XTREAM_PASSWORD}"

# Kolejka i wątek do pobierania
if os.path.exists(QUEUE_FILE):
    with open(QUEUE_FILE) as f:
        queue_data = json.load(f)
else:
    queue_data = []

download_queue = queue.Queue()
download_log = []
download_status = {}

def save_queue():
    with open(QUEUE_FILE, 'w') as f:
        json.dump(queue_data, f)

def download_worker():
    while True:
        job = download_queue.get()
        if job is None:
            break
        episode_id = job.get("episode_id")
        try:
            download_status[episode_id] = "⏳"
            with open(DOWNLOAD_LOG_FILE, "a") as logf:
                logf.write(f"\n=== Pobieranie: {job['file']} ===\n")
                subprocess.run(job["cmd"], check=True, stdout=logf, stderr=logf)
            status = "✅"
        except subprocess.CalledProcessError:
            with open(DOWNLOAD_LOG_FILE, "a") as logf:
                logf.write(f"❌ Błąd pobierania: {job['file']}\n")
            status = "❌"
        download_status[episode_id] = status
        download_log.append({"file": job["file"], "status": status})
        download_queue.task_done()
        global queue_data
        queue_data = [item for item in queue_data if item['episode_id'] != episode_id]
        save_queue()

threading.Thread(target=download_worker, daemon=True).start()

@seriale_bp.route("/queue/status")
def queue_status():
    return jsonify(download_status)

@seriale_bp.route("/queue/remove", methods=["POST"])
def queue_remove():
    episode_id = request.form.get("id")
    global queue_data
    queue_data = [item for item in queue_data if item['episode_id'] != episode_id]
    download_status.pop(episode_id, None)
    save_queue()
    return '', 204

@seriale_bp.route("/queue/reorder", methods=["POST"])
def queue_reorder():
    order = request.json.get("order", [])
    global queue_data
    queue_data.sort(key=lambda x: order.index(x['episode_id']) if x['episode_id'] in order else len(order))
    save_queue()
    return '', 204

@seriale_bp.route("/")
def seriale_list():
    response = requests.get(f"{BASE_API}&action=get_series")
    if response.status_code != 200:
        return "Błąd pobierania listy seriali", 500
    seriale = response.json()
    html = """
    <h1>Seriale</h1>
    {% for s in seriale %}
        <div>
            <img src="{{ s['cover'] }}" width="100" />
            <a href="/seriale/{{ s['series_id'] }}">{{ s['name'] }}</a>
            <p><strong>ID:</strong> {{ s['series_id'] }} | <strong>Num:</strong> {{ s['num'] }} | <strong>Kategoria:</strong> {{ s['category_id'] }}</p>
        </div>
    {% endfor %}
    """
    return render_template_string(html, seriale=seriale)

def is_episode_already_downloaded(serial_name, season, episode_num, title, ext):
    path = os.path.join(DOWNLOAD_PATH_SERIES, serial_name, f"Sezon {season}")
    file_name = f"S{int(season):02d}E{int(episode_num):02d} - {title}.{ext}"
    file_path = os.path.join(path, file_name.replace(' ', '_'))
    return os.path.exists(file_path) and os.path.getsize(file_path) > 1000000  # większe niż 1MB

@seriale_bp.route("/<int:series_id>")
def serial_detail(series_id):
    response = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}")
    if response.status_code != 200:
        return f"Błąd pobierania szczegółów serialu ID {series_id}", 404
    data = response.json()
    serial = data.get('info', {})
    episodes_raw = data.get('episodes', {})

    if isinstance(episodes_raw, str):
        episodes_raw = json.loads(episodes_raw)

    all_episodes = []
    for sezon_lista in episodes_raw.values():
        all_episodes.extend(sezon_lista)

    sezony = {}
    for ep in all_episodes:
        sez = ep.get('season', 1)
        sezony.setdefault(sez, []).append(ep)

    html = """
    <h1>{{ serial.get('name', 'Brak nazwy') }}</h1>
    <p>{{ serial.get('plot', '') }}</p>
    {% for sezon, eps in sezony.items() %}
        <h3>Sezon {{ sezon }}</h3>
        <ul>
        {% for ep in eps %}
            <li>S{{ '%02d' % ep['season'] }}E{{ '%02d' % ep['episode_num'] }} - {{ ep['title'] }}</li>
        {% endfor %}
        </ul>
    {% endfor %}
    """
    return render_template_string(html, serial=serial, sezony=sezony)
