from flask import Blueprint, request, jsonify, render_template_string
import os
import requests
import subprocess
import json
from urllib.parse import quote

seriale_bp = Blueprint('seriale', __name__)

# Zmienne środowiskowe
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
            <a href="/seriale/{{ s['series_id'] }}">{{ s['name'] }}</a>
        </div>
    {% endfor %}
    """
    return render_template_string(html, seriale=seriale)

@seriale_bp.route("/seriale/<int:series_id>")
def serial_detail(series_id):
    info = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}").json()
    serial = info['info']

    episodes_raw = info['episodes']
    if isinstance(episodes_raw, str):
        episodes_dict = json.loads(episodes_raw)
    else:
        episodes_dict = episodes_raw

    all_episodes = []
    for sezon_lista in episodes_dict.values():
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
            <input type="hidden" name="series_id" value="{{ serial['series_id'] }}">
            <input type="hidden" name="season" value="{{ sezon }}">
            <button type="submit">📥 Pobierz cały sezon</button>
        </form>
        <ul>
        {% for ep in eps %}
            <li>
                S{{ '%02d' % ep['season'] }}E{{ '%02d' % ep['episode_num'] }} - {{ ep['title'] }}
                <form method="post" action="/download/episode" style="display:inline">
                    <input type="hidden" name="series_id" value="{{ serial['series_id'] }}">
                    <input type="hidden" name="id" value="{{ ep['id'] }}">
                    <input type="hidden" name="season" value="{{ ep['season'] }}">
                    <input type="hidden" name="title" value="{{ ep['title'] }}">
                    <input type="hidden" name="episode_num" value="{{ ep['episode_num'] }}">
                    <button>📥</button>
                </form>
            </li>
        {% endfor %}
        </ul>
    {% endfor %}
    """
    return render_template_string(html, serial=serial, sezony=sezony)

@seriale_bp.route("/download/episode", methods=["POST"])
def download_episode():
    data = request.form
    series_id = data['series_id']
    episode_id = data['id']
    season = data['season']
    title = data['title']
    episode_num = data.get('episode_num', 1)

    info = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}").json()['info']
    serial_name = info['name'].replace('/', '_')

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
    series_id = request.form['series_id']
    season = request.form['season']

    info = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}").json()
    serial_name = info['info']['name'].replace('/', '_')

    episodes_raw = info['episodes']
    if isinstance(episodes_raw, str):
        episodes_dict = json.loads(episodes_raw)
    else:
        episodes_dict = episodes_raw

    episodes = []
    for sezon_lista in episodes_dict.values():
        episodes.extend(sezon_lista)

    for ep in [ep for ep in episodes if str(ep['season']) == season]:
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
