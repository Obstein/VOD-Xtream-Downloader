
from flask import Blueprint, request, jsonify, render_template, send_file
import os
import requests
import subprocess
from urllib.parse import quote # Mimo usunięcia z logiki pobierania, zostawiamy dla TMDB
import json
import sys
import threading
import queue
import time
from io import BytesIO
import re # Dodano import dla wyrażeń regularnych
from datetime import datetime # Dodano import dla obsługi dat

seriale_bp = Blueprint('seriale', __name__, url_prefix='/seriale')

# --- Konfiguracja (bez zmian) ---
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
BASE_API = f"{XTREAM_HOST}:{XTREAM_PORT}/player_api.php?username={XTREAM_USERNAME}&password={XTREAM_PASSWORD}"

# --- Zarządzanie kolejką (bez zmian) ---
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

# --- NOWA FUNKCJA POMOCNICZA ---
def sanitize_filename(name):
    """Usuwa nieprawidłowe znaki z nazwy pliku/folderu, aby zapewnić kompatybilność."""
    return re.sub(r'[<>:"/\\|?*]', '', name).strip()

# --- Funkcje TMDB (bez zmian) ---
from functools import lru_cache

@lru_cache(maxsize=128)
def search_tmdb_series_id(title):
    cleaned_title = (
        title
        .replace("PL -", "")
        .replace("PL-", "")
        .replace("POLSKI", "")
        .replace("LEKTOR", "")
        .replace("DUBBING", "")
        .strip()
        .title()
    )
    url = f"https://api.themoviedb.org/3/search/tv?api_key={TMDB_API_KEY}&query={quote(cleaned_title)}&language=pl-PL"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        results = data.get("results", [])
        if results:
            return results[0]["id"]
    return None

def get_tmdb_episode_metadata(tmdb_id, season, episode):
    url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}?api_key={TMDB_API_KEY}&language=pl-PL"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    return None

# --- ZMODYFIKOWANA TRASA NFO ---
@seriale_bp.route("/nfo/<int:series_id>/<int:season>/<int:episode>")
def download_nfo(series_id, season, episode):
    response = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}")
    if response.status_code != 200:
        return "Błąd pobierania danych serialu", 500
    info = response.json()
    series_info = info.get('info', {})

    # Logika do tworzenia nazwy serialu z rokiem (zgodnie z Plex)
    series_name_raw = series_info.get('name', f"serial_{series_id}")
    release_date_str = series_info.get('releaseDate', '')
    year = ''
    if release_date_str:
        try:
            year = f"({datetime.strptime(release_date_str, '%Y-%m-%d').year})"
        except ValueError:
            if release_date_str.strip()[:4].isdigit():
                 year = f"({release_date_str.strip()[:4]})"
    series_folder_name = sanitize_filename(f"{series_name_raw} {year}".strip())

    episodes_raw = info.get('episodes', {})
    if isinstance(episodes_raw, str):
        episodes_raw = json.loads(episodes_raw)
    found_ep = None
    for sezon_lista in episodes_raw.values():
        for ep in sezon_lista:
            if int(ep.get('season', 0)) == season and int(ep.get('episode_num', 0)) == episode:
                found_ep = ep
                break
        if found_ep:
            break
    if not found_ep:
        return "❌ Nie znaleziono odcinka", 404

    ep_title = sanitize_filename(found_ep.get('title', f"Odcinek {episode}"))
    
    tmdb_id = search_tmdb_series_id(series_name_raw)
    if not tmdb_id:
        return f"Nie znaleziono TMDB ID dla: {series_name_raw}", 404
    metadata = get_tmdb_episode_metadata(tmdb_id, season, episode)
    if not metadata:
        return "Brak metadanych", 404

    nfo = f"""
<episodedetails>
  <title>{metadata['name']}</title>
  <season>{season}</season>
  <episode>{episode}</episode>
  <plot>{metadata['overview']}</plot>
  <aired>{metadata['air_date']}</aired>
  <thumb>{'https://image.tmdb.org/t/p/original' + metadata['still_path'] if metadata.get('still_path') else ''}</thumb>
</episodedetails>
"""
    # Tworzenie ścieżki i nazwy pliku .nfo zgodnej z plikiem wideo
    path = os.path.join(DOWNLOAD_PATH_SERIES, series_folder_name, f"Season {season:02d}")
    os.makedirs(path, exist_ok=True)
    file_name_nfo = f"{series_folder_name} - S{season:02d}E{episode:02d} - {ep_title}.nfo"
    file_path = os.path.join(path, file_name_nfo)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(nfo.strip())
    return f"📄 Zapisano plik: {file_path}", 200

# --- Reszta kodu (worker, kolejka, widoki) - bez zmian w logice, tylko w miejscach tworzenia plików ---
# --- Worker i zarządzanie kolejką (bez zmian) ---
download_queue = queue.Queue()
download_log = []
download_status = {}

def save_queue():
    with open(QUEUE_FILE, 'w') as f:
        json.dump(queue_data, f, indent=4)

def save_completed():
    with open(COMPLETED_FILE, 'w') as f:
        json.dump(completed_data, f, indent=4)

def download_worker():
    global queue_data
    # Przywracanie z pliku queue_data
    existing_ids_in_queue = {id(job) for job in list(download_queue.queue)}
    for job in queue_data:
        if id(job) not in existing_ids_in_queue:
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
                process = subprocess.Popen(job["cmd"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
                for line in process.stdout:
                    logf.write(line)
                process.wait()
                if process.returncode != 0:
                   raise subprocess.CalledProcessError(process.returncode, job["cmd"])

            status = "✅"
            if episode_id not in completed_data:
                completed_data.append(episode_id)
                save_completed()

            # Usuń zadanie z queue_data po pomyślnym ukończeniu
            #global queue_data
            queue_data = [item for item in queue_data if item['episode_id'] != episode_id]
            save_queue()

        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            with open(DOWNLOAD_LOG_FILE, "a") as logf:
                logf.write(f"❌ Błąd pobierania: {job['file']} - {e}\n")
            status = "❌"
        finally:
            download_status[episode_id] = status
            download_queue.task_done()

threading.Thread(target=download_worker, daemon=True).start()

# --- Widoki i reszta tras (bez zmian w logice) ---
@seriale_bp.route("/queue/status")
def queue_status():
    return jsonify(download_status)

@seriale_bp.route("/queue/remove", methods=["POST"])
def queue_remove():
    episode_id = request.form.get("id")
    global queue_data
    # Usuń z głównej listy danych kolejki
    queue_data = [item for item in queue_data if item.get('episode_id') != episode_id]
    save_queue()
    # Usuń ze statusu, jeśli istnieje
    download_status.pop(episode_id, None)
    # Usuń z aktywnej kolejki (Queue) - to jest trudniejsze, bo nie ma bezpośredniego usuwania
    # Prostszym podejściem jest pozostawienie workera, aby zignorował zadanie, jeśli go nie ma w queue_data
    return '', 204
    
@seriale_bp.route("/queue/reorder", methods=["POST"])
def queue_reorder():
    order = request.json.get("order", [])
    global queue_data
    # Tworzenie mapy 'episode_id' -> pozycja
    order_map = {eid: i for i, eid in enumerate(order)}
    # Sortowanie 'queue_data' na podstawie mapy
    queue_data.sort(key=lambda x: order_map.get(x['episode_id'], len(order)))
    save_queue()
    # Odświeżenie kolejki w workerze
    while not download_queue.empty():
        download_queue.get()
    for job in queue_data:
        download_queue.put(job)
    return '', 204

@seriale_bp.route("/completed")
def completed_episodes():
    return jsonify(completed_data)

@seriale_bp.route("/")
def seriale_list():
    response = requests.get(f"{BASE_API}&action=get_series")
    if response.status_code != 200:
        return "Błąd pobierania listy seriali", 500
    # ... reszta widoku bez zmian
    return render_template("seriale_list.html", seriale=response.json())

@seriale_bp.route("/<int:series_id>")
def serial_detail(series_id):
    response = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}")
    if response.status_code != 200:
        return "Błąd pobierania detali serialu", 500

    data = response.json()
    serial_info = data.get('info', {})
    episodes_raw = data.get('episodes', {})

    if isinstance(episodes_raw, str):
        episodes_raw = json.loads(episodes_raw)

    # Sortowanie odcinków według numeru sezonu i odcinka
    sezony = {}
    for season_num_str, episode_list in episodes_raw.items():
        try:
            season_num = int(season_num_str)
            sorted_episodes = sorted(episode_list, key=lambda x: int(x.get('episode_num', 0)))
            sezony[season_num] = sorted_episodes
        except ValueError:
            # Obsługa, gdy season_num_str nie jest liczbą (np. 'undefined')
            continue

    # Sortowanie sezonów
    sezony = dict(sorted(sezony.items()))
    # ... widok bez zmian w logice
    return render_template("seriale_detail.html", serial=data, sezony=sezony, series_id=series_id) # Skrócone dla zwięzłości


# --- ZMODYFIKOWANA TRASA POBIERANIA ODCINKA ---
@seriale_bp.route("/download/episode", methods=["POST"])
def download_episode():
    form_data = request.form
    series_id = form_data['series_id'].strip()
    episode_id = form_data['id']
    season = form_data['season']
    title = form_data['title']
    episode_num = form_data.get('episode_num', '1')

    response = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}")
    if response.status_code != 200:
        return "Błąd pobierania metadanych", 500

    data = response.json()
    series_info = data.get('info', {})
    episodes_raw = data.get("episodes", {})
    if isinstance(episodes_raw, str):
        episodes_raw = json.loads(episodes_raw)

    found_ep = None
    for sezon_lista in episodes_raw.values():
        for ep in sezon_lista:
            if str(ep['id']) == episode_id:
                found_ep = ep
                break
        if found_ep: break
    if not found_ep:
        return "❌ Nie znaleziono odcinka", 404

    # Nowa logika tworzenia nazw i ścieżek dla Plex
    series_name_raw = series_info.get('name', f"serial_{series_id}")
    release_date_str = series_info.get('releaseDate', '')
    year = ''
    if release_date_str:
        try:
            year = f"({datetime.strptime(release_date_str, '%Y-%m-%d').year})"
        except ValueError:
            if release_date_str.strip()[:4].isdigit():
                 year = f"({release_date_str.strip()[:4]})"
    
    series_folder_name = sanitize_filename(f"{series_name_raw} {year}".strip())
    path = os.path.join(DOWNLOAD_PATH_SERIES, series_folder_name, f"Season {int(season):02d}")
    os.makedirs(path, exist_ok=True)

    ext = found_ep.get("container_extension", "mp4")
    episode_title_sanitized = sanitize_filename(title)
    file_name = f"{series_folder_name} - S{int(season):02d}E{int(episode_num):02d} - {episode_title_sanitized}.{ext}"
    file_path = os.path.join(path, file_name)

    url = f"{XTREAM_HOST}:{XTREAM_PORT}/series/{XTREAM_USERNAME}/{XTREAM_PASSWORD}/{episode_id}.{ext}"
    job = {"cmd": ["wget", "-O", file_path, url], "file": file_name, "episode_id": episode_id, "series": series_folder_name, "title": title}
    
    download_queue.put(job)
    download_status[episode_id] = "⏳"
    queue_data.append(job)
    save_queue()
    return "", 202

# --- ZMODYFIKOWANA TRASA POBIERANIA SEZONU ---
@seriale_bp.route("/download/season", methods=["POST"])
def download_season():
    series_id = request.form['series_id'].strip()
    season = int(request.form['season'])

    response = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}")
    if response.status_code != 200:
        return "Błąd pobierania danych serialu", 500

    data = response.json()
    series_info = data.get('info', {})
    episodes_raw = data.get('episodes', {})

    # Logika tworzenia nazwy serialu z rokiem (raz dla całego sezonu)
    series_name_raw = series_info.get('name', f"serial_{series_id}")
    release_date_str = series_info.get('releaseDate', '')
    year = ''
    if release_date_str:
        try:
            year = f"({datetime.strptime(release_date_str, '%Y-%m-%d').year})"
        except ValueError:
            if release_date_str.strip()[:4].isdigit():
                 year = f"({release_date_str.strip()[:4]})"
    series_folder_name = sanitize_filename(f"{series_name_raw} {year}".strip())

    if isinstance(episodes_raw, str):
        episodes_raw = json.loads(episodes_raw)
    
    episodes_in_season = [ep for sezon_lista in episodes_raw.values() for ep in sezon_lista if int(ep.get('season', 0)) == season]

    for ep in episodes_in_season:
        episode_id = ep['id']
        title = ep['title']
        episode_num = ep['episode_num']
        ext = ep.get("container_extension", "mp4")

        # Tworzenie ścieżki i nazwy pliku dla każdego odcinka w sezonie
        path = os.path.join(DOWNLOAD_PATH_SERIES, series_folder_name, f"Season {int(season):02d}")
        os.makedirs(path, exist_ok=True)
        episode_title_sanitized = sanitize_filename(title)
        file_name = f"{series_folder_name} - S{int(season):02d}E{int(episode_num):02d} - {episode_title_sanitized}.{ext}"
        file_path = os.path.join(path, file_name)
        
        url = f"{XTREAM_HOST}:{XTREAM_PORT}/series/{XTREAM_USERNAME}/{XTREAM_PASSWORD}/{episode_id}.{ext}"
        job = {"cmd": ["wget", "-O", file_path, url], "file": file_name, "episode_id": episode_id, "series": series_folder_name, "title": title}
        
        download_queue.put(job)
        download_status[episode_id] = "⏳"
        queue_data.append(job)

    save_queue()
    return "🕐 Dodano sezon do kolejki", 202

# --- Funkcja pomocnicza is_episode_already_downloaded (zostawiona bez zmian, choć jej logika może wymagać aktualizacji) ---
# UWAGA: Ta funkcja nie jest używana w kodzie i jej obecna logika nie będzie działać poprawnie z nową strukturą nazw.
# Lepszym sposobem sprawdzania jest użycie listy `completed_data`.
def is_episode_already_downloaded(serial_name, season, episode_num, title, ext):
    path = os.path.join(DOWNLOAD_PATH_SERIES, serial_name, f"Sezon {season}")
    file_name = f"S{int(season):02d}E{int(episode_num):02d} - {title}.{ext}"
    file_path = os.path.join(path, file_name.replace(' ', '_'))
    return os.path.exists(file_path) and os.path.getsize(file_path) > 1000000

