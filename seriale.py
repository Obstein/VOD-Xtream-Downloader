from flask import Blueprint, request, jsonify, render_template_string
import os
import requests
import subprocess
from urllib.parse import quote
import json
import sys

seriale_bp = Blueprint('seriale', __name__)

XTREAM_HOST = os.getenv("XTREAM_HOST")
XTREAM_PORT = os.getenv("XTREAM_PORT")
XTREAM_USERNAME = os.getenv("XTREAM_USERNAME")
XTREAM_PASSWORD = os.getenv("XTREAM_PASSWORD")
DOWNLOAD_PATH_SERIES = os.getenv("DOWNLOAD_PATH_SERIES", "/downloads/Seriale")
RETRY_COUNT = int(os.getenv("RETRY_COUNT", 3))

BASE_API = f"{XTREAM_HOST}:{XTREAM_PORT}/player_api.php?username={XTREAM_USERNAME}&password={XTREAM_PASSWORD}"

@seriale_bp.route("/seriale")
def seriale_list():
    response = requests.get(f"{BASE_API}&action=get_series")
    seriale = response.json()
    html = """
    <h1>Seriale</h1>
    {% for s in seriale %}
        <div>
            <img src="{{ s['cover'] }}" width="100" />
            <a href="/seriale/{{ s['num'] }}">{{ s['name'] }}</a>
            <p><strong>ID:</strong> {{ s['series_id'] }} | <strong>Num:</strong> {{ s['num'] }} | <strong>Kategoria:</strong> {{ s['category_id'] }}</p>
        </div>
    {% endfor %}
    """
    return render_template_string(html, seriale=seriale)

@seriale_bp.route("/seriale/<int:series_id>")
def serial_detail(series_id):
    response = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}")
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
        <form method="post" action="/download/season">
            <input type="hidden" name="series_id" value="{{ series_id }}">
            <input type="hidden" name="season" value="{{ sezon }}">
            <button type="submit">📥 Pobierz cały sezon</button>
        </form>
        <ul>
        {% for ep in eps %}
            <li>
                S{{ '%02d' % ep['season'] }}E{{ '%02d' % ep['episode_num'] }} - {{ ep['title'] }}
                <form method="post" action="/download/episode" style="display:inline">
                    <input type="hidden" name="series_id" value="{{ series_id }}">
                    <input type="hidden" name="id" value="{{ ep['id'] }}">
                    <input type="hidden" name="season" value="{{ ep['season'] }}">
                    <input type="hidden" name="episode_num" value="{{ ep['episode_num'] }}">
                    <input type="hidden" name="title" value="{{ ep['title'] }}">
                    <button>📥</button>
                </form>
            </li>
        {% endfor %}
        </ul>
    {% endfor %}
    """
    return render_template_string(html, serial=serial, sezony=sezony, series_id=series_id)

@seriale_bp.route("/download/episode", methods=["POST"])
def download_episode():
    sys.stderr.write("🟢 URUCHOMIONO download_episode()\n")
    data = request.form
    series_id = data['series_id'].strip()
    episode_id = data['id']
    season = data['season']
    title = data['title']
    episode_num = data.get('episode_num', '1')

    response = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}")
    sys.stderr.write(f"🔍 EPISODE API RESPONSE: {response.status_code} {response.text}\n")

    if response.status_code != 200:
        return "Błąd pobierania metadanych", 500

    try:
        data = response.json()
        if 'info' not in data:
            return "Błąd: brak pola 'info' w odpowiedzi API", 500
        info = data['info']
    except Exception as e:
        return f"Błąd dekodowania odpowiedzi API: {e}", 500

    # Diagnostyka pliku: miniatura vs wideo
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

    serial_name = info.get('name', f"serial_{series_id}").replace('/', '_')
    path = os.path.join(DOWNLOAD_PATH_SERIES, serial_name, f"Sezon {season}")
    os.makedirs(path, exist_ok=True)

    file_name = f"S{int(season):02d}E{int(episode_num):02d} - {title}.mp4"
    file_path = os.path.join(path, quote(file_name.replace(' ', '_')))

    url = f"{XTREAM_HOST}:{XTREAM_PORT}/series/{XTREAM_USERNAME}/{XTREAM_PASSWORD}/{episode_id}.mp4"

    success = False
    for _ in range(RETRY_COUNT):
        try:
            subprocess.run(["wget", "-O", file_path, url], check=True)
            success = True
            break
        except subprocess.CalledProcessError:
            continue

    return ("Pobrano" if success else "Błąd przy pobieraniu"), 200 if success else 500

@seriale_bp.route("/download/season", methods=["POST"])
def download_season():
    sys.stderr.write("🟢 URUCHOMIONO download_season()\n")
    series_id = request.form['series_id'].strip()
    season = int(request.form['season'])

    response = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}")
    sys.stderr.write(f"🔍 SEASON API RESPONSE: {response.status_code} {response.text}\n")

    if response.status_code != 200:
        return "Błąd pobierania danych serialu", 500

    try:
        data = response.json()
        if 'info' not in data or 'episodes' not in data:
            return "Błąd: niekompletna odpowiedź z API", 500
        info = data
    except Exception as e:
        return f"Błąd dekodowania JSON: {e}", 500

    serial_name = info['info']['name'].replace('/', '_')

    episodes_raw = info['episodes']
    if isinstance(episodes_raw, str):
        episodes_raw = json.loads(episodes_raw)

    episodes = [ep for sezon_lista in episodes_raw.values() for ep in sezon_lista if int(ep.get('season', 0)) == season]

    for ep in episodes:
        episode_id = ep['id']
        title = ep['title']
        episode_num = ep['episode_num']
        path = os.path.join(DOWNLOAD_PATH_SERIES, serial_name, f"Sezon {season}")
        os.makedirs(path, exist_ok=True)
        file_name = f"S{int(season):02d}E{int(episode_num):02d} - {title}.mp4"
        file_path = os.path.join(path, quote(file_name.replace(' ', '_')))
        url = f"{XTREAM_HOST}:{XTREAM_PORT}/series/{XTREAM_USERNAME}/{XTREAM_PASSWORD}/{episode_id}.mp4"

        success = False
        for _ in range(RETRY_COUNT):
            try:
                subprocess.run(["wget", "-O", file_path, url], check=True)
                success = True
                break
            except subprocess.CalledProcessError:
                continue

    return "Pobrano sezon", 200

@seriale_bp.route("/diagnose/episode", methods=["POST"])
def diagnose_episode():
    series_id = request.form['series_id']
    episode_id = request.form['id']

    response = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}")
    if response.status_code != 200:
        return "❌ Błąd pobierania danych z API", 500

    try:
        data = response.json()
        episodes_raw = data.get("episodes", {})
        if isinstance(episodes_raw, str):
            episodes_raw = json.loads(episodes_raw)
    except Exception as e:
        return f"❌ Błąd dekodowania JSON: {e}", 500

    for sezon_lista in episodes_raw.values():
        for ep in sezon_lista:
            if str(ep['id']) == episode_id:
                video = ep.get("info", {}).get("video", {})
                codec = video.get("codec_name")
                attached = video.get("disposition", {}).get("attached_pic")
                return jsonify({
                    "codec_name": codec,
                    "attached_pic": attached,
                    "diagnosis": "🟩 Prawidłowy plik wideo" if codec != "png" else "🟥 Miniatura zamiast odcinka"
                })

    return "❓ Nie znaleziono odcinka w danych API", 404
