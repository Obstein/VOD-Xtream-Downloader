# seriale.py (zmodyfikowany)

from flask import Blueprint, request, jsonify, render_template, send_file
import os
import requests
import subprocess
from urllib.parse import quote
import json
import sys
import re
from datetime import datetime

# Importuj wspólne komponenty z nowo utworzonego pliku
from downloader_core import (
    add_to_download_queue,
    get_queue_status,
    get_full_queue_data,
    remove_from_queue,
    reorder_queue,
    get_completed_items
)

seriale_bp = Blueprint('seriale', __name__, url_prefix='/seriale')

# --- Konfiguracja (bez zmian, ale bez zmiennych kolejki) ---
XTREAM_HOST = os.getenv("XTREAM_HOST")
XTREAM_PORT = os.getenv("XTREAM_PORT")
XTREAM_USERNAME = os.getenv("XTREAM_USERNAME")
XTREAM_PASSWORD = os.getenv("XTREAM_PASSWORD")
DOWNLOAD_PATH_SERIES = os.getenv("DOWNLOAD_PATH_SERIES", "/downloads/Seriale")
RETRY_COUNT = int(os.getenv("RETRY_COUNT", 3)) # Już nie używane bezpośrednio, ale zostawiamy dla spójności
TMDB_API_KEY = "cfdfac787bf2a6e2c521b93a0309ff2c"
BASE_API = f"{XTREAM_HOST}:{XTREAM_PORT}/player_api.php?username={XTREAM_USERNAME}&password={XTREAM_PASSWORD}"
FAVORITES_FILE = "favorites.json"

# --- Funkcja pomocnicza sanitize_filename ---
def sanitize_filename(name):
    """Usuwa nieprawidłowe znaki z nazwy pliku/folderu, aby zapewnić kompatybilność."""
    s = re.sub(r'[^\w\s\-\._()]', '', name)
    s = re.sub(r'\s+', ' ', s).strip()
    return s



def load_favorites():
    """Ładuje ulubione seriale z pliku JSON. Zwraca pustą listę, jeśli plik nie istnieje lub jest pusty/uszkodzony."""
    if not os.path.exists(FAVORITES_FILE):
        return [] # Zmieniono z {} na []
    try:
        with open(FAVORITES_FILE, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content:
                return [] # Zmieniono z {} na [] (Plik jest pusty)
            return json.loads(content)
    except json.JSONDecodeError:
        print(f"Ostrzeżenie: Plik {FAVORITES_FILE} jest uszkodzony lub zawiera niepoprawny JSON. Zwracam pustą listę.")
        return [] # Zmieniono z {} na []
    except Exception as e:
        print(f"Błąd podczas ładowania ulubionych z pliku {FAVORITES_FILE}: {e}")
        return [] # Zmieniono z {} na []

def save_favorites(favorites):
    """Zapisuje ulubione seriale do pliku JSON."""
    try:
        with open(FAVORITES_FILE, 'w', encoding='utf-8') as f:
            json.dump(favorites, f, indent=4)
    except Exception as e:
        print(f"Błąd podczas zapisywania ulubionych do pliku {FAVORITES_FILE}: {e}")



@seriale_bp.route('/favorites/toggle/<int:series_id>', methods=['POST'])
def toggle_favorite(series_id):
    favorites = load_favorites()
    if series_id in favorites:
        favorites.remove(series_id)
        message = "Serial usunięty z ulubionych."
    else:
        favorites.append(series_id)
        message = "Serial dodany do ulubionych!"
    save_favorites(favorites)
    return jsonify({"status": "success", "message": message, "is_favorite": series_id in favorites})

# Opcjonalnie: trasa do sprawdzenia, czy dany serial jest ulubiony
@seriale_bp.route('/favorites/status/<int:series_id>', methods=['GET'])
def get_favorite_status(series_id):
    favorites = load_favorites()
    is_favorite = series_id in favorites
    return jsonify({"is_favorite": is_favorite})

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

# --- TRASA NFO (bez zmian) ---
@seriale_bp.route("/nfo/<int:series_id>/<int:season>/<int:episode>")
def download_nfo(series_id, season, episode):
    try:
        response = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}")
        response.raise_for_status()
        info = response.json()
    except requests.exceptions.RequestException as e:
        return f"Błąd komunikacji z API: {e}", 500
    except ValueError:
        return "Błąd: Nieprawidłowa odpowiedź JSON z API.", 500

    series_info = info.get('info', {})
    series_name_raw = series_info.get('name', f"serial_{series_id}")

    prefix_pattern = r"^(?:[pP][lL]|[eE][nN]|[aA]\+|[dD]\+)\s*-\s*"
    series_name_cleaned = re.sub(prefix_pattern, "", series_name_raw).strip()
    
    release_date_str = series_info.get('releaseDate', '')
    year_str = ''
    if release_date_str:
        try:
            year_str = f"({datetime.strptime(release_date_str, '%Y-%m-%d').year})"
        except ValueError:
            if release_date_str.strip()[:4].isdigit():
                 year_str = f"({release_date_str.strip()[:4]})"
    
    series_folder_name = sanitize_filename(f"{series_name_cleaned} {year_str}".strip())

    episodes_raw = info.get('episodes', {})
    if isinstance(episodes_raw, str):
        try:
            episodes_raw = json.loads(episodes_raw)
        except json.JSONDecodeError:
            return "Błąd: Nieprawidłowy format JSON odcinków z API.", 500
            
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
    
    tmdb_id = search_tmdb_series_id(series_name_cleaned)
    if not tmdb_id:
        return f"Nie znaleziono TMDB ID dla: {series_name_cleaned}", 404
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
    path = os.path.join(DOWNLOAD_PATH_SERIES, series_folder_name, f"Season {season:02d}")
    os.makedirs(path, exist_ok=True)
    file_name_nfo = f"{series_folder_name} - S{season:02d}E{episode:02d} - {ep_title}.nfo"
    file_path = os.path.join(path, file_name_nfo)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(nfo.strip())
    return f"📄 Zapisano plik: {file_path}", 200

# --- Widoki zarządzania kolejką (używają wspólnych funkcji z downloader_core) ---
@seriale_bp.route("/queue/status")
def queue_status():
    return jsonify(get_queue_status())

@seriale_bp.route("/queue/remove", methods=["POST"])
def queue_remove():
    item_id = request.form.get("id") # Zmieniono na item_id
    remove_from_queue(item_id)
    return '', 204
    
@seriale_bp.route("/queue/reorder", methods=["POST"])
def queue_reorder():
    order = request.json.get("order", [])
    reorder_queue(order)
    return '', 204

@seriale_bp.route("/completed")
def completed_episodes():
    return jsonify(get_completed_items())

@seriale_bp.route("/queue/full_data")
def get_full_queue():
    return jsonify(get_full_queue_data())

# --- Widoki seriali ---
@seriale_bp.route("/")
def seriale_list():
    query = request.args.get('query', '').lower() 

    response = requests.get(f"{BASE_API}&action=get_series")
    if response.status_code != 200:
        return "Błąd pobierania listy seriali", 500
    
    all_seriale = response.json()
    
    if query:
        filtered_seriale = []
        for serial in all_seriale:
            if serial.get('name') and query in serial['name'].lower():
                filtered_seriale.append(serial)
        seriale_to_display = filtered_seriale
    else:
        seriale_to_display = all_seriale

    return render_template("seriale_list.html", seriale=seriale_to_display)

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

    sezony = {}
    for season_num_str, episode_list in episodes_raw.items():
        try:
            season_num = int(season_num_str)
            sorted_episodes = sorted(episode_list, key=lambda x: int(x.get('episode_num', 0)))
            sezony[season_num] = sorted_episodes
        except ValueError:
            continue

    sezony = dict(sorted(sezony.items()))
    
    return render_template("serial_detail.html", serial=data, sezony=sezony, series_id=series_id, completed_data=get_completed_items())


# --- ZMODYFIKOWANA TRASA POBIERANIA ODCINKA (używa add_to_download_queue) ---
@seriale_bp.route("/download/episode", methods=["POST"])
def download_episode():
    episode_id = request.form.get("id")
    series_id = request.form.get("series_id")
    season = request.form.get("season")
    episode_num = request.form.get("episode_num")
    title = request.form.get("title")

    if not all([episode_id, series_id, season, episode_num, title]):
        return "Błąd: Brak wymaganych danych do pobrania odcinka.", 400

    try:
        response = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}")
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        return f"Błąd komunikacji z API: {e}", 500
    except ValueError:
        return "Błąd: Nieprawidłowa odpowiedź JSON z API.", 500
    
    series_info = data.get('info', {})
    episodes_raw = data.get('episodes', {})

    current_episode_info = None
    if isinstance(episodes_raw, str):
        try:
            episodes_raw = json.loads(episodes_raw)
        except json.JSONDecodeError:
            return "Błąd: Nieprawidłowy format JSON odcinków z API.", 500
    
    for season_data in episodes_raw.values():
        for ep in season_data:
            if str(ep.get('id')) == str(episode_id):
                current_episode_info = ep
                break
        if current_episode_info:
            break

    if not current_episode_info:
        return f"Błąd: Nie znaleziono informacji o odcinku {episode_id}", 404

    ext = current_episode_info.get("container_extension", "mp4") # Domyślne rozszerzenie
    
    series_name_raw = series_info.get("name", "")
    prefix_pattern = r"^(?:[pP][lL]|[eE][nN]|[aA]\+|[dD]\+)\s*-\s*"
    series_name_cleaned = re.sub(prefix_pattern, "", series_name_raw).strip()

    release_year_str = series_info.get("releaseDate", "").split('-')[0] if series_info.get("releaseDate") else ""

    if release_year_str:
        series_folder_name = sanitize_filename(f"{series_name_cleaned} ({release_year_str})")
    else:
        series_folder_name = sanitize_filename(series_name_cleaned)
    
    path = os.path.join(DOWNLOAD_PATH_SERIES, series_folder_name, f"Season {int(season):02d}")
    os.makedirs(path, exist_ok=True)
    
    cleaned_episode_title = re.sub(r"^(?:[pP][lL]|[nN][fF]|[hH][bB][oO]|\[\s*[pP][lL]\s*\]|\[\s*[nN][fF]\s*\]|\s*\[\s*\d{4}[pP]\s*\])\s*-\s*", "", title).strip()

    pattern_to_remove_series_info = r"^(?:" + re.escape(series_name_cleaned) + r"(?:\s*\(\d+K\))?|\s*\d+K)?\s*-\s*S\d{2}E\d{2}\s*[\s-]*"
    cleaned_episode_title = re.sub(pattern_to_remove_series_info, "", cleaned_episode_title, flags=re.IGNORECASE).strip()

    cleaned_episode_title = re.sub(r"\s*\(\d+K\)\s*|\s*\d+K\s*|\s*\d{3,4}p\s*", "", cleaned_episode_title, flags=re.IGNORECASE).strip()

    if not cleaned_episode_title:
            cleaned_episode_title = f"Odcinek {int(episode_num):02d}"

    episode_title_sanitized = sanitize_filename(cleaned_episode_title)
    file_name = f"{series_folder_name} - S{int(season):02d}E{int(episode_num):02d} - {episode_title_sanitized}.{ext}"
    file_path = os.path.join(path, file_name)

    url = f"{XTREAM_HOST}:{XTREAM_PORT}/series/{XTREAM_USERNAME}/{XTREAM_PASSWORD}/{episode_id}.{ext}"
    
    job = {
        "cmd": ["wget", "-O", file_path, url],
        "file": file_name,
        "item_id": episode_id, # Używamy ogólnego item_id
        "item_type": "serial_episode", # Dodajemy typ elementu
        "series": series_folder_name,
        "title": title
    }
    
    if add_to_download_queue(job): # Dodaj do kolejki poprzez nową funkcję
        return "🕐 Dodano odcinek do kolejki", 202
    else:
        return "Odcinek już w kolejce lub pobrany.", 200


# --- ZMODYFIKOWANA TRASA POBIERANIA SEZONU (używa add_to_download_queue) ---
@seriale_bp.route("/download/season", methods=["POST"])
def download_season():
    series_id = request.form['series_id'].strip()
    season = int(request.form['season'])

    try:
        response = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}")
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        return f"Błąd komunikacji z API: {e}", 500
    except ValueError:
        return "Błąd: Nieprawidłowa odpowiedź JSON z API.", 500

    series_info = data.get('info', {})
    episodes_raw = data.get('episodes', {})

    series_name_raw = series_info.get('name', f"serial_{series_id}")
    series_name_cleaned = re.sub(r"^[pP][lL]\s*-\s*", "", series_name_raw).strip()

    release_date_str = series_info.get('releaseDate', '')
    year_str = ''
    if release_date_str:
        try:
            year_str = f"({datetime.strptime(release_date_str, '%Y-%m-%d').year})"
        except ValueError:
            if release_date_str.strip()[:4].isdigit():
                 year_str = f"({release_date_str.strip()[:4]})"
    
    series_folder_name = sanitize_filename(f"{series_name_cleaned} {year_str}".strip())

    if isinstance(episodes_raw, str):
        try:
            episodes_raw = json.loads(episodes_raw)
        except json.JSONDecodeError:
            return "Błąd: Nieprawidłowy format JSON odcinków z API.", 500
    
    episodes_in_season = [ep for sezon_lista in episodes_raw.values() for ep in sezon_lista if int(ep.get('season', 0)) == season]

    if not episodes_in_season:
        return f"Błąd: Nie znaleziono żadnych odcinków dla sezonu {season}.", 404

    added_count = 0
    for ep in episodes_in_season:
        episode_id = ep['id']
        title = ep['title']
        episode_num = ep['episode_num']
        ext = ep.get("container_extension", "mp4")

        if not all([episode_id, episode_num, title, ext]):
            print(f"Brak pełnych informacji dla odcinka: {ep}. Pomijam.")
            continue

        path = os.path.join(DOWNLOAD_PATH_SERIES, series_folder_name, f"Season {int(season):02d}")
        os.makedirs(path, exist_ok=True)
        
        cleaned_episode_title = re.sub(r"^(?:[pP][lL]|[nN][fF]|[hH][bB][oO]|\[\s*[pP][lL]\s*\]|\[\s*[nN][fF]\s*\]|\s*\[\s*\d{4}[pP]\s*\])\s*-\s*", "", title).strip()

        pattern_to_remove_series_info = r"^(?:" + re.escape(series_name_cleaned) + r"(?:\s*\(\d+K\))?|\s*\d+K)?\s*-\s*S\d{2}E\d{2}\s*[\s-]*"
        cleaned_episode_title = re.sub(pattern_to_remove_series_info, "", cleaned_episode_title, flags=re.IGNORECASE).strip()

        cleaned_episode_title = re.sub(r"\s*\(\d+K\)\s*|\s*\d+K\s*|\s*\d{3,4}p\s*", "", cleaned_episode_title, flags=re.IGNORECASE).strip()

        if not cleaned_episode_title:
             cleaned_episode_title = f"Odcinek {int(episode_num):02d}"

        episode_title_sanitized = sanitize_filename(cleaned_episode_title)
        file_name = f"{series_folder_name} - S{int(season):02d}E{int(episode_num):02d} - {episode_title_sanitized}.{ext}"
        file_path = os.path.join(path, file_name)
        
        url = f"{XTREAM_HOST}:{XTREAM_PORT}/series/{XTREAM_USERNAME}/{XTREAM_PASSWORD}/{episode_id}.{ext}"
        
        job = {
            "cmd": ["wget", "-O", file_path, url],
            "file": file_name,
            "item_id": episode_id, # Używamy ogólnego item_id
            "item_type": "serial_episode", # Dodajemy typ elementu
            "series": series_folder_name,
            "title": title
        }
        
        if add_to_download_queue(job): # Dodaj do kolejki poprzez nową funkcję
            added_count += 1

    if added_count > 0:
        return f"🕐 Dodano {added_count} odcinków sezonu do kolejki", 202
    else:
        return "Wszystkie odcinki sezonu już w kolejce lub pobrane.", 200