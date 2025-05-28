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

# Modyfikacje znajdziesz w download_episode i download_season:

# W download_episode — dodane po wygenerowaniu file_path:
# tmdb_match = search_tmdb_series(serial_name)
# tmdb_id = tmdb_match["id"] if tmdb_match else None
# season_details = get_tmdb_season_details(tmdb_id, int(season)) if tmdb_id else {}
# tmdb_episode = next((e for e in season_details.get("episodes", []) if str(e["episode_number"]) == episode_num), None)
# metadata = {...}
# save_metadata_to_nfo(file_path, metadata)

# Analogicznie w download_season — dla każdego odcinka

# Gotowe do działania!
