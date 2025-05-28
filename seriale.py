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
COMPLETED_FILE = "completed.json"

TMDB_API_KEY = "cfdfac787bf2a6e2c521b93a0309ff2c"
TMDB_LANGUAGE = "pl"

BASE_API = f"{XTREAM_HOST}:{XTREAM_PORT}/player_api.php?username={XTREAM_USERNAME}&password={XTREAM_PASSWORD}"

if os.path.exists(QUEUE_FILE):
    with open(QUEUE_FILE) as f:
        queue_data = json.load(f)
else:
    queue_data = []

if os.path.exists(COMPLETED_FILE):
    with open(COMPLETED_FILE) as f:
        completed_data = json.load(f)
else:
    completed_data = []

download_queue = queue.Queue()
download_log = []
download_status = {}

def save_queue():
    with open(QUEUE_FILE, 'w') as f:
        json.dump(queue_data, f)

def save_completed():
    with open(COMPLETED_FILE, 'w') as f:
        json.dump(completed_data, f)

def search_tmdb_series(title):
    url = "https://api.themoviedb.org/3/search/tv"
    params = {"api_key": TMDB_API_KEY, "query": title, "language": TMDB_LANGUAGE}
    r = requests.get(url, params=params)
    r.raise_for_status()
    results = r.json().get("results", [])
    return results[0] if results else None

def get_tmdb_season_details(tmdb_id, season_number):
    url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season_number}"
    params = {"api_key": TMDB_API_KEY, "language": TMDB_LANGUAGE}
    r = requests.get(url, params=params)
    r.raise_for_status()
    return r.json()

def save_metadata_to_nfo(path, metadata):
    nfo_path = os.path.splitext(path)[0] + ".nfo"
    with open(nfo_path, "w", encoding="utf-8") as f:
        f.write("<episodedetails>\n")
        for key, value in metadata.items():
            f.write(f"  <{key}>{value}</{key}>\n")
        f.write("</episodedetails>\n")

def download_worker():
    global queue_data
    for job in queue_data:
        if job["episode_id"] not in completed_data:
            download_queue.put(job)

    while True:
        job = download_queue.get()
        if job is None:
            break
        episode_id = job.get("episode_id")
        try:
            download_status[episode_id] = "⏳"
            with open(DOWNLOAD_LOG_FILE, "a") as logf:
                logf.write(f"\n=== Pobieranie: {job['file']} ===\n")
                result = subprocess.run(job["cmd"], check=True, stdout=logf, stderr=logf)
            status = "✅"
            if episode_id not in completed_data:
                completed_data.append(episode_id)
                save_completed()
        except subprocess.CalledProcessError:
            with open(DOWNLOAD_LOG_FILE, "a") as logf:
                logf.write(f"❌ Błąd pobierania: {job['file']}\n")
            status = "❌"
        download_status[episode_id] = status
        download_log.append({"file": job["file"], "status": status})
        download_queue.task_done()
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

@seriale_bp.route("/completed")
def completed_episodes():
    return jsonify(completed_data)

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
    all_episodes = [ep for sezon_lista in episodes_raw.values() for ep in sezon_lista]
    sezony = {}
    for ep in all_episodes:
        sez = ep.get('season', 1)
        sezony.setdefault(sez, []).append(ep)
    html = """
    <h1>{{ serial['name'] }}</h1>
    {% for sezon, eps in sezony.items() %}
        <h3>Sezon {{ sezon }}</h3>
        <ul>
        {% for ep in eps %}
            <li>
                S{{ '%02d' % ep['season'] }}E{{ '%02d' % ep['episode_num'] }} - {{ ep['title'] }}
                <form action="/seriale/download/episode" method="post" style="display:inline">
                    <input type="hidden" name="id" value="{{ ep['id'] }}">
                    <input type="hidden" name="series_id" value="{{ series_id }}">
                    <input type="hidden" name="season" value="{{ ep['season'] }}">
                    <input type="hidden" name="episode_num" value="{{ ep['episode_num'] }}">
                    <input type="hidden" name="title" value="{{ ep['title'] }}">
                    <button type="submit">📥</button>
                </form>
            </li>
        {% endfor %}
        </ul>
    {% endfor %}
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

    serial_name = data.get('info', {}).get('name', f"serial_{series_id}").replace('/', '_')
    path = os.path.join(DOWNLOAD_PATH_SERIES, serial_name, f"Sezon {season}")
    os.makedirs(path, exist_ok=True)

    ext = found_ep.get("container_extension", "mp4")
    file_name = f"S{int(season):02d}E{int(episode_num):02d} - {title}.{ext}"
    file_path = os.path.join(path, quote(file_name.replace(' ', '_')))
    url = f"{XTREAM_HOST}:{XTREAM_PORT}/series/{XTREAM_USERNAME}/{XTREAM_PASSWORD}/{episode_id}.{ext}"

    tmdb_match = search_tmdb_series(serial_name)
    tmdb_id = tmdb_match["id"] if tmdb_match else None
    season_details = get_tmdb_season_details(tmdb_id, int(season)) if tmdb_id else {}
    tmdb_episode = next((e for e in season_details.get("episodes", []) if str(e["episode_number"]) == episode_num), None)
    metadata = {
        "title": title,
        "season": season,
        "episode": episode_num,
        "plot": tmdb_episode.get("overview", "") if tmdb_episode else found_ep.get("plot", ""),
        "aired": tmdb_episode.get("air_date", "") if tmdb_episode else found_ep.get("release_date", ""),
        "runtime": tmdb_episode.get("runtime", ""),
        "showtitle": serial_name,
    }
    save_metadata_to_nfo(file_path, metadata)

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

    tmdb_match = search_tmdb_series(serial_name)
    tmdb_id = tmdb_match["id"] if tmdb_match else None
    season_details = get_tmdb_season_details(tmdb_id, season) if tmdb_id else {}

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

        tmdb_episode = next((e for e in season_details.get("episodes", []) if str(e["episode_number"]) == str(ep["episode_num"])), None)
        metadata = {
            "title": title,
            "season": season,
            "episode": episode_num,
            "plot": tmdb_episode.get("overview", "") if tmdb_episode else ep.get("plot", ""),
            "aired": tmdb_episode.get("air_date", "") if tmdb_episode else ep.get("release_date", ""),
            "runtime": tmdb_episode.get("runtime", ""),
            "showtitle": serial_name,
        }
        save_metadata_to_nfo(file_path, metadata)

        job = {"cmd": ["wget", "-O", file_path, url], "file": file_name, "episode_id": episode_id, "series": serial_name, "title": title}
        download_queue.put(job)
        download_status[episode_id] = "⏳"
        queue_data.append(job)

    save_queue()
    return "🕐 Dodano sezon do kolejki", 202
