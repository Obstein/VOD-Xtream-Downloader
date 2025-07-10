
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
    s = re.sub(r'[^\w\s\-\._()]', '', name) # Usuwa nieprawidłowe znaki
    s = re.sub(r'\s+', ' ', s).strip() # Zastępuje wielokrotne spacje pojedynczą i usuwa białe znaki na końcach
    return s



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
    try:
        response = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}")
        response.raise_for_status() # Sprawdza, czy zapytanie zakończyło się sukcesem
        info = response.json()
    except requests.exceptions.RequestException as e:
        return f"Błąd komunikacji z API: {e}", 500
    except ValueError: # Błąd dekodowania JSON
        return "Błąd: Nieprawidłowa odpowiedź JSON z API.", 500

    series_info = info.get('info', {})
    series_name_raw = series_info.get('name', f"serial_{series_id}")

    # === LOGIKA NAZEWNICTWA PLEXA DLA FOLDERU SERIALU ===
    # 1. Usuń prefiks "PL - " z nazwy serialu
    series_name_cleaned = re.sub(r"^[pP][lL]\s*-\s*", "", series_name_raw).strip()
    
    # 2. Wydobądź rok z 'releaseDate'
    release_date_str = series_info.get('releaseDate', '')
    year_str = ''
    if release_date_str:
        try:
            year_str = f"({datetime.strptime(release_date_str, '%Y-%m-%d').year})"
        except ValueError:
            # Jeśli format daty jest inny, spróbuj pobrać pierwsze 4 cyfry
            if release_date_str.strip()[:4].isdigit():
                 year_str = f"({release_date_str.strip()[:4]})"
    
    # 3. Zbuduj nazwę folderu serialu: "Nazwa Serialu (Rok)"
    series_folder_name = sanitize_filename(f"{series_name_cleaned} {year_str}".strip())
    # ====================================================

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
    
    # Przekazujemy series_name_cleaned do search_tmdb_series_id dla lepszych wyników
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
    # Tworzenie ścieżki i nazwy pliku .nfo zgodnej z plikiem wideo dla Plexa
    path = os.path.join(DOWNLOAD_PATH_SERIES, series_folder_name, f"Season {season:02d}")
    os.makedirs(path, exist_ok=True)
    # Nazwa pliku NFO: "Nazwa Serialu (Rok) - SXXEYY - Tytuł Odcinka.nfo"
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

# Nowy endpoint do pobierania pełnych danych kolejki (nazwy plików, ID, statusy itp.)
@seriale_bp.route("/queue/full_data")
def get_full_queue():
    # Zwracamy pełną listę zadań w kolejce
    return jsonify(queue_data)

# --- Funkcja do pobierania informacji o serialu i odcinkach ---
def get_series_info(series_id):
    """
    Pobiera szczegółowe informacje o serialu i jego odcinkach z API.
    Zwraca (series_info, episodes_raw) lub (None, None) w przypadku błędu.
    """
    
    # Pobierz szczegóły serialu
    series_info_url = f"{BASE_API}&action=get_series_info&series_id={series_id}"
    series_info = None
    try:
        response_info = requests.get(series_info_url)
        response_info.raise_for_status() # Wyrzuca wyjątek dla błędów HTTP (4xx lub 5xx)
        series_info_data = response_info.json()
        if 'info' in series_info_data:
            series_info = series_info_data['info']
        else:
            # Niektóre API mogą zwracać info bezpośrednio, a nie w zagnieżdżonym 'info'
            # To jest przypadek dla argontv.ru, gdzie info jest na głównym poziomie,
            # a odcinki są w 'episodes'.
            series_info = series_info_data 
            
    except (requests.exceptions.RequestException, ValueError, KeyError) as e:
        print(f"Błąd pobierania informacji o serialu {series_id} z URL {series_info_url}: {e}")
        return None, None # Zwróć None, jeśli nie można pobrać informacji o serialu

    # Pobierz odcinki dla serialu
    episodes_url = f"{BASE_API}&action=get_series_episodes&series_id={series_id}"
    episodes_raw = {}
    try:
        response_episodes = requests.get(episodes_url)
        response_episodes.raise_for_status()
        episodes_data = response_episodes.json()
        if 'episodes' in episodes_data:
            # API zwraca odcinki pod kluczem 'episodes'
            # Struktura 'episodes' to często słownik, gdzie kluczami są numery sezonów,
            # a wartościami listy odcinków w danym sezonie.
            episodes_raw = episodes_data['episodes']
        else:
            # Jeśli API zwraca odcinki bezpośrednio jako słownik {episode_id: data}
            # lub inną strukturę, może być konieczna adaptacja.
            # Przyjmujemy, że API zwraca słownik sezonów, a w nich listę odcinków
            episodes_raw = episodes_data 

    except (requests.exceptions.RequestException, ValueError, KeyError) as e:
        print(f"Błąd pobierania odcinków dla serialu {series_id} z URL {episodes_url}: {e}")
        # Nawet jeśli odcinki się nie załadują, zwróć informacje o serialu, jeśli są dostępne
        return series_info, {} # Zwróć pusty słownik dla odcinków, jeśli nie można ich pobrać

    return series_info, episodes_raw

# --- Widoki i reszta tras (zmiana w seriale_list) ---
@seriale_bp.route("/")
def seriale_list():
    # Pobierz parametr 'query' z zapytania URL, domyślnie pusty ciąg
    # Konwertuj na małe litery, aby wyszukiwanie było niewrażliwe na wielkość liter
    query = request.args.get('query', '').lower() 

    response = requests.get(f"{BASE_API}&action=get_series")
    if response.status_code != 200:
        return "Błąd pobierania listy seriali", 500
    
    all_seriale = response.json()
    
    # Jeśli podano zapytanie, filtruj seriale
    if query:
        filtered_seriale = []
        for serial in all_seriale:
            # Sprawdź, czy nazwa serialu istnieje i czy zawiera zapytanie (niewrażliwe na wielkość liter)
            if serial.get('name') and query in serial['name'].lower():
                filtered_seriale.append(serial)
        seriale_to_display = filtered_seriale
    else:
        # Jeśli brak zapytania, wyświetl wszystkie seriale
        seriale_to_display = all_seriale

    return render_template("seriale_list.html", seriale=seriale_to_display)

@seriale_bp.route("/<int:series_id>")
def serial_detail(series_id):
    series_info, episodes_raw = get_series_info(series_id)
    if not series_info:
        return "Serial nie znaleziony", 404

    episodes_by_season = {}
    
    # === KLUCZOWA ZMIANA TUTAJ: Iterujemy przez sezony, a potem przez odcinki w każdym sezonie ===
    # episodes_raw powinno być słownikiem, np. {"1": [ep1_data, ep2_data], "2": [ep3_data]}
    for season_num_str, episodes_list_for_season in episodes_raw.items():
        try:
            season_num = int(season_num_str) # Konwertuj numer sezonu na int dla sortowania
        except ValueError:
            print(f"Ostrzeżenie: Nieprawidłowy numer sezonu '{season_num_str}' dla serialu {series_id}. Pomijam.")
            continue # Pomiń ten sezon, jeśli numer jest nieprawidłowy

        episodes_by_season[season_num] = []
        
        for episode_data in episodes_list_for_season:
            # Sprawdź, czy 'id' i 'episode_num' istnieją w danych odcinka
            if 'id' in episode_data and 'episode_num' in episode_data:
                episodes_by_season[season_num].append(episode_data)
            else:
                print(f"Ostrzeżenie: Brak klucza 'id' lub 'episode_num' w danych odcinka: {episode_data}")

    # Sortuj odcinki w każdym sezonie według numeru odcinka
    for season in episodes_by_season:
        episodes_by_season[season].sort(key=lambda x: int(x['episode_num']))

    # Przygotuj zestaw ID pobranych odcinków dla szybkiego sprawdzania
    # Upewnij się, że completed_data jest załadowane
    completed_episode_ids = {str(item['episode_id']) for item in completed_data if 'episode_id' in item}

    # Dodaj status do każdego odcinka
    for season, episodes in episodes_by_season.items():
        for episode in episodes:
            # Użyj 'id' jako klucza ID odcinka z API
            episode_id_str = str(episode['id']) 

            if episode_id_str in download_status:
                episode['status'] = download_status[episode_id_str]
            elif episode_id_str in completed_episode_ids:
                episode['status'] = "✅ Pobrano"
            else:
                episode['status'] = "" 
    # ... (pozostała część funkcji serial_detail w seriale.py) ...

    # Dodaj poniższe linie w funkcji serial_detail, tuż przed render_template
    print(f"DEBUG: series_info: {series_info}")
    print(f"DEBUG: episodes_raw (po get_series_info): {episodes_raw}")
    print(f"DEBUG: episodes_by_season (po przetworzeniu): {episodes_by_season}")
        
    return render_template("serial_detail.html", series_info=series_info, episodes_by_season=episodes_by_season)



# --- ZMODYFIKOWANA TRASA POBIERANIA ODCINKA ---
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
    
    # === LOGIKA NAZEWNICTWA PLEXA DLA FOLDERU SERIALU I PLIKU ODCINKA ===
    # 1. Usuń prefiks "PL - " z nazwy serialu
    series_name_raw = series_info.get("name", "")
    series_name_cleaned = re.sub(r"^[pP][lL]\s*-\s*", "", series_name_raw).strip()

    # 2. Wydobądź rok z 'releaseDate'
    release_year_str = series_info.get("releaseDate", "").split('-')[0] if series_info.get("releaseDate") else ""

    # 3. Zbuduj nazwę folderu serialu: "Nazwa Serialu (Rok)"
    if release_year_str:
        series_folder_name = sanitize_filename(f"{series_name_cleaned} ({release_year_str})")
    else:
        series_folder_name = sanitize_filename(series_name_cleaned)
    # ===================================================================

    if episode_id in completed_data:
        print(f"Odcinek {title} (ID: {episode_id}) już pobrany, pomijam.")
        return "Odcinek już pobrany", 200

    path = os.path.join(DOWNLOAD_PATH_SERIES, series_folder_name, f"Season {int(season):02d}")
    os.makedirs(path, exist_ok=True)
    # Oczyść tytuł odcinka z powtarzających się informacji o serialu i prefiksu "PL -"
        # Użyj regex, aby usunąć "PL - Nazwa Serialu - SXXEYY" z początku tytułu odcinka
        # Upewnij się, że używamy series_name_cleaned, a nie series_folder_name, bo ten drugi zawiera rok
    # === KROK 1: Wstępne oczyszczenie tytułu odcinka ===
        # Usuń popularne prefiksy usług streamingowych (PL, NF, HBO itp.) z początku tytułu
        # Użyjemy niemająca grupy przechwytującej (?:...)
    cleaned_episode_title = re.sub(r"^(?:[pP][lL]|[nN][fF]|[hH][bB][oO]|\[\s*[pP][lL]\s*\]|\[\s*[nN][fF]\s*\]|\s*\[\s*\d{4}[pP]\s*\])\s*-\s*", "", title).strip()

        # === KROK 2: Usuń powtarzającą się nazwę serialu i oznaczenie SXXEYY ===
        # Ta część jest kluczowa dla problemu "NF - Biohackers 4K - S01E06"
        # Tworzymy wzorzec do usunięcia, który może zawierać nazwę serialu (series_name_cleaned),
        # opcjonalnie jakość (np. (4K)), i oznaczenie SXXEYY.
        # Użyjemy re.escape() dla series_name_cleaned, aby zabezpieczyć znaki specjalne regex.
        # Dodatkowo, obsłużymy inne warianty oznaczeń jakości np. " 4K" bez nawiasów.
        
        # Wzorzec do dopasowania np. "Biohackers (4K) - S01E06 - " lub "Biohackers 4K - S01E06 - "
        # Zaczynamy od potencjalnie oczyszczonej nazwy serialu
    pattern_to_remove_series_info = r"^(?:" + re.escape(series_name_cleaned) + r"(?:\s*\(\d+K\))?|\s*\d+K)?\s*-\s*S\d{2}E\d{2}\s*[\s-]*"
    cleaned_episode_title = re.sub(pattern_to_remove_series_info, "", cleaned_episode_title, flags=re.IGNORECASE).strip()

        # === KROK 3: Ostateczne usunięcie pozostałych oznaczeń jakości ===
        # Usuń wszelkie pozostałe, samodzielne oznaczenia jakości (np. " (4K)", " 1080p") z dowolnego miejsca w tytule
    cleaned_episode_title = re.sub(r"\s*\(\d+K\)\s*|\s*\d+K\s*|\s*\d{3,4}p\s*", "", cleaned_episode_title, flags=re.IGNORECASE).strip()

        # === KROK 4: Weryfikacja i domyślny tytuł ===
        # Jeśli po wszystkich czyszczeniach tytuł jest pusty, użyj domyślnego
    if not cleaned_episode_title:
            cleaned_episode_title = f"Odcinek {int(episode_num):02d}"

        
    episode_title_sanitized = sanitize_filename(cleaned_episode_title)
    #episode_title_sanitized = sanitize_filename(title)
    # 4. Zbuduj nazwę pliku odcinka: "Nazwa Serialu (Rok) - SXXEYY - Tytuł Odcinka.ext"
    file_name = f"{series_folder_name} - S{int(season):02d}E{int(episode_num):02d} - {episode_title_sanitized}.{ext}"
    file_path = os.path.join(path, file_name)

    url = f"{XTREAM_HOST}:{XTREAM_PORT}/series/{XTREAM_USERNAME}/{XTREAM_PASSWORD}/{episode_id}.{ext}"
    job = {"cmd": ["wget", "-O", file_path, url], "file": file_name, "episode_id": episode_id, "series": series_folder_name, "title": title}
    
    download_queue.put(job)
    download_status[episode_id] = "⏳"
    queue_data.append(job)

    save_queue()
    return "🕐 Dodano odcinek do kolejki", 202

# --- ZMODYFIKOWANA TRASA POBIERANIA SEZONU ---
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

    # === LOGIKA NAZEWNICTWA PLEXA DLA FOLDERU SERIALU ===
    # 1. Usuń prefiks "PL - " z nazwy serialu
    series_name_raw = series_info.get('name', f"serial_{series_id}")
    series_name_cleaned = re.sub(r"^[pP][lL]\s*-\s*", "", series_name_raw).strip()

    # 2. Wydobądź rok z 'releaseDate'
    release_date_str = series_info.get('releaseDate', '')
    year_str = ''
    if release_date_str:
        try:
            year_str = f"({datetime.strptime(release_date_str, '%Y-%m-%d').year})"
        except ValueError:
            if release_date_str.strip()[:4].isdigit():
                 year_str = f"({release_date_str.strip()[:4]})"
    
    # 3. Zbuduj nazwę folderu serialu: "Nazwa Serialu (Rok)"
    series_folder_name = sanitize_filename(f"{series_name_cleaned} {year_str}".strip())
    # ====================================================

    if isinstance(episodes_raw, str):
        try:
            episodes_raw = json.loads(episodes_raw)
        except json.JSONDecodeError:
            return "Błąd: Nieprawidłowy format JSON odcinków z API.", 500
    
    episodes_in_season = [ep for sezon_lista in episodes_raw.values() for ep in sezon_lista if int(ep.get('season', 0)) == season]

    if not episodes_in_season:
        return f"Błąd: Nie znaleziono żadnych odcinków dla sezonu {season}.", 404

    for ep in episodes_in_season:
        episode_id = ep['id']
        title = ep['title']
        episode_num = ep['episode_num']
        ext = ep.get("container_extension", "mp4")

        if not all([episode_id, episode_num, title, ext]):
            print(f"Brak pełnych informacji dla odcinka: {ep}. Pomijam.")
            continue

        if episode_id in completed_data:
            print(f"Odcinek {title} (ID: {episode_id}) już pobrany, pomijam.")
            continue

        path = os.path.join(DOWNLOAD_PATH_SERIES, series_folder_name, f"Season {int(season):02d}")
        os.makedirs(path, exist_ok=True)
        # Oczyść tytuł odcinka z powtarzających się informacji o serialu i prefiksu "PL -"
        # Użyj regex, aby usunąć "PL - Nazwa Serialu - SXXEYY" z początku tytułu odcinka
        # Upewnij się, że używamy series_name_cleaned, a nie series_folder_name, bo ten drugi zawiera rok
        # === KROK 1: Wstępne oczyszczenie tytułu odcinka ===
        # Usuń popularne prefiksy usług streamingowych (PL, NF, HBO itp.) z początku tytułu
        # Użyjemy niemająca grupy przechwytującej (?:...)
        cleaned_episode_title = re.sub(r"^(?:[pP][lL]|[nN][fF]|[hH][bB][oO]|\[\s*[pP][lL]\s*\]|\[\s*[nN][fF]\s*\]|\s*\[\s*\d{4}[pP]\s*\])\s*-\s*", "", title).strip()

        # === KROK 2: Usuń powtarzającą się nazwę serialu i oznaczenie SXXEYY ===
        # Ta część jest kluczowa dla problemu "NF - Biohackers 4K - S01E06"
        # Tworzymy wzorzec do usunięcia, który może zawierać nazwę serialu (series_name_cleaned),
        # opcjonalnie jakość (np. (4K)), i oznaczenie SXXEYY.
        # Użyjemy re.escape() dla series_name_cleaned, aby zabezpieczyć znaki specjalne regex.
        # Dodatkowo, obsłużymy inne warianty oznaczeń jakości np. " 4K" bez nawiasów.
        
        # Wzorzec do dopasowania np. "Biohackers (4K) - S01E06 - " lub "Biohackers 4K - S01E06 - "
        # Zaczynamy od potencjalnie oczyszczonej nazwy serialu
        pattern_to_remove_series_info = r"^(?:" + re.escape(series_name_cleaned) + r"(?:\s*\(\d+K\))?|\s*\d+K)?\s*-\s*S\d{2}E\d{2}\s*[\s-]*"
        cleaned_episode_title = re.sub(pattern_to_remove_series_info, "", cleaned_episode_title, flags=re.IGNORECASE).strip()

        # === KROK 3: Ostateczne usunięcie pozostałych oznaczeń jakości ===
        # Usuń wszelkie pozostałe, samodzielne oznaczenia jakości (np. " (4K)", " 1080p") z dowolnego miejsca w tytule
        cleaned_episode_title = re.sub(r"\s*\(\d+K\)\s*|\s*\d+K\s*|\s*\d{3,4}p\s*", "", cleaned_episode_title, flags=re.IGNORECASE).strip()

        # === KROK 4: Weryfikacja i domyślny tytuł ===
        # Jeśli po wszystkich czyszczeniach tytuł jest pusty, użyj domyślnego
        if not cleaned_episode_title:
             cleaned_episode_title = f"Odcinek {int(episode_num):02d}"

        

        episode_title_sanitized = sanitize_filename(cleaned_episode_title)
        #episode_title_sanitized = sanitize_filename(title)
        # 4. Zbuduj nazwę pliku odcinka: "Nazwa Serialu (Rok) - SXXEYY - Tytuł Odcinka.ext"
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

