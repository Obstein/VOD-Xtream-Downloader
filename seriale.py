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

@seriale_bp.route("/<int:series_id>")
def serial_detail(series_id):
    response = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}")
    if response.status_code != 200:
        return "Błąd pobierania danych serialu", 500
    info = response.json()
    serial = info['info']
    episodes_raw = info['episodes']

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
    <h1>{{ serial['name'] }}</h1>
    <p>{{ serial['plot'] }}</p>
    {% for sezon, eps in sezony.items() %}
        <h3>Sezon {{ sezon }}</h3>
        <ul>
        {% for ep in eps %}
            <li>
                S{{ '%02d' % ep['season'] }}E{{ '%02d' % ep['episode_num'] }} - {{ ep['title'] }}
                <button id="btn-{{ ep['id'] }}" onclick="downloadEpisode('{{ ep['id'] }}', '{{ series_id }}', '{{ ep['season'] }}', '{{ ep['episode_num'] }}', '{{ ep['title'] }}')">📥</button>
            </li>
        {% endfor %}
        </ul>
    {% endfor %}
    <script>
    function downloadEpisode(id, series_id, season, episode_num, title) {
        const btn = document.getElementById('btn-' + id);
        btn.textContent = '⏳';
        fetch('/seriale/download/episode', {
            method: 'POST',
            body: new URLSearchParams({
                id,
                series_id,
                season,
                episode_num,
                title
            })
        }).then(resp => {
            if (!resp.ok) {
                btn.textContent = '❌';
            } else {
                btn.textContent = '⏳';
            }
        }).catch(() => {
            btn.textContent = '❌';
        });
    }

    setInterval(() => {
        fetch('/seriale/queue/status')
            .then(resp => resp.json())
            .then(data => {
                for (const [id, status] of Object.entries(data)) {
                    const btn = document.getElementById('btn-' + id);
                    if (btn) btn.textContent = status;
                }
            });
    }, 5000);
    </script>
    """
    return render_template_string(html, serial=serial, sezony=sezony, series_id=series_id)

@seriale_bp.route("/download/episode", methods=["POST"])
def download_episode():
    data = request.form
    series_id = data['series_id'].strip()
    episode_id = data['id']
    season = data['season']
    title = data['title']
    episode_num = data.get('episode_num', '1')

    response = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}")
    if response.status_code != 200:
        return "Błąd pobierania metadanych", 500

    data = response.json()
    episodes_raw = data.get("episodes", {})
    if isinstance(episodes_raw, str):
        episodes_raw = json.loads(episodes_raw)

    found_ep = None
    for sezon_lista in episodes_raw.values():
        for ep in sezon_lista:
            if str(ep['id']) == episode_id:
                found_ep = ep
                break
        if found_ep:
            break

    if not found_ep:
        return "❌ Nie znaleziono odcinka", 404

    video_info = found_ep.get("info", {}).get("video", {})
    codec = video_info.get("codec_name")
    attached = video_info.get("disposition", {}).get("attached_pic", 0)

    if codec == "png" and attached == 1:
        return "❌ To nie jest plik wideo, tylko miniatura (cover)", 400

    serial_name = data.get('info', {}).get('name', f"serial_{series_id}").replace('/', '_')
    path = os.path.join(DOWNLOAD_PATH_SERIES, serial_name, f"Sezon {season}")
    os.makedirs(path, exist_ok=True)

    ext = found_ep.get("container_extension", "mp4")
    file_name = f"S{int(season):02d}E{int(episode_num):02d} - {title}.{ext}"
    file_path = os.path.join(path, quote(file_name.replace(' ', '_')))

    url = f"{XTREAM_HOST}:{XTREAM_PORT}/series/{XTREAM_USERNAME}/{XTREAM_PASSWORD}/{episode_id}.{ext}"

    job = {"cmd": ["wget", "-O", file_path, url], "file": file_name, "episode_id": episode_id, "series": serial_name, "title": title}
    download_queue.put(job)
    download_status[episode_id] = "⏳"
    queue_data.append(job)
    save_queue()
    return "", 202

@seriale_bp.route("/download/season", methods=["POST"])
def download_season():
    series_id = request.form['series_id'].strip()
    season = int(request.form['season'])

    response = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}")
    if response.status_code != 200:
        return "Błąd pobierania danych serialu", 500

    data = response.json()
    serial_name = data['info']['name'].replace('/', '_')
    episodes_raw = data['episodes']
    if isinstance(episodes_raw, str):
        episodes_raw = json.loads(episodes_raw)

    episodes = [ep for sezon_lista in episodes_raw.values() for ep in sezon_lista if int(ep.get('season', 0)) == season]

    for ep in episodes:
        episode_id = ep['id']
        title = ep['title']
        episode_num = ep['episode_num']
        ext = ep.get("container_extension", "mp4")
        path = os.path.join(DOWNLOAD_PATH_SERIES, serial_name, f"Sezon {season}")
        os.makedirs(path, exist_ok=True)
        file_name = f"S{int(season):02d}E{int(episode_num):02d} - {title}.{ext}"
        file_path = os.path.join(path, quote(file_name.replace(' ', '_')))
        url = f"{XTREAM_HOST}:{XTREAM_PORT}/series/{XTREAM_USERNAME}/{XTREAM_PASSWORD}/{episode_id}.{ext}"

        job = {"cmd": ["wget", "-O", file_path, url], "file": file_name, "episode_id": episode_id, "series": serial_name, "title": title}
        download_queue.put(job)
        download_status[episode_id] = "⏳"
        queue_data.append(job)

    save_queue()
    return "🕐 Dodano sezon do kolejki", 202

def is_episode_already_downloaded(serial_name, season, episode_num, title, ext):
    path = os.path.join(DOWNLOAD_PATH_SERIES, serial_name, f"Sezon {season}")
    file_name = f"S{int(season):02d}E{int(episode_num):02d} - {title}.{ext}"
    file_path = os.path.join(path, file_name.replace(' ', '_'))
    return os.path.exists(file_path) and os.path.getsize(file_path) > 1000000  # większe niż 1MB
