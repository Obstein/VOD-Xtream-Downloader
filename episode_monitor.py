import os
import requests
import json
import time
from datetime import datetime
import re
import sys

# --- Konfiguracja ---
XTREAM_HOST = os.getenv("XTREAM_HOST")
XTREAM_PORT = os.getenv("XTREAM_PORT")
XTREAM_USERNAME = os.getenv("XTREAM_USERNAME")
XTREAM_PASSWORD = os.getenv("XTREAM_PASSWORD")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

BASE_API = f"{XTREAM_HOST}:{XTREAM_PORT}/player_api.php?username={XTREAM_USERNAME}&password={XTREAM_PASSWORD}"

FAVORITES_FILE = "favorites.json"
MONITORED_STATE_FILE = "monitored_series_state.json"

# --- Funkcje pomocnicze do ładowania/zapisywania JSON ---
def load_json_file(filepath, default_value):
    if not os.path.exists(filepath):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Plik {filepath} nie istnieje. Zwracam pustą wartość.")
        return default_value
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Plik {filepath} jest pusty. Zwracam pustą wartość.")
                return default_value
            return json.loads(content)
    except json.JSONDecodeError:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Błąd parsowania JSON w pliku {filepath}. Plik uszkodzony. Zwracam pustą wartość.")
        return default_value
    except Exception as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Nieoczekiwany błąd podczas ładowania pliku {filepath}: {e}")
        return default_value

def save_json_file(filepath, data):
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Błąd podczas zapisywania pliku {filepath}: {e}")

# --- Funkcja do sanitizacji nazw ---
def sanitize_filename(name):
    return re.sub(r'[<>:"/\\|?*]', '', name).strip()

# --- Funkcja do pobierania szczegółów serialu z Xtream ---
def get_xtream_series_details(series_id):
    try:
        response = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}")
        response.raise_for_status()
        series_info = response.json()
        
        if not series_info or 'info' not in series_info or 'episodes' not in series_info:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Błąd: Niekompletne dane serialu {series_id} z Xtream.")
            return None

        episodes_by_season = {}
        for season_num_str, episodes_list in series_info['episodes'].items():
            episodes_by_season[str(season_num_str)] = []
            for ep in episodes_list:
                if 'id' in ep and 'episode_num' in ep:
                    episodes_by_season[str(season_num_str)].append({
                        'id': ep['id'],
                        'episode_num': ep['episode_num'],
                        'title': ep.get('title', f'Odcinek {ep["episode_num"]}'),
                        'ext': ep.get('container_extension', 'mp4')
                    })
        
        return {
            'name': series_info['info'].get('name', f'Nieznany Serial {series_id}'),
            'episodes_by_season': episodes_by_season,
            'cover_url': series_info['info'].get('cover') # Dodaj cover_url, jeśli istnieje
        }

    except requests.exceptions.RequestException as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Błąd połączenia z Xtream API dla serialu {series_id}: {e}")
        return None
    except Exception as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Nieoczekiwany błąd podczas pobierania detali serialu {series_id}: {e}")
        return None

# --- Funkcja do wysyłania powiadomień Discord Webhook ---
def send_discord_notification(title, description, color=0x00FF00, image_url=None):
    if not DISCORD_WEBHOOK_URL:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Ostrzeżenie: Brak skonfigurowanego DISCORD_WEBHOOK_URL. Nie wysyłam powiadomienia.")
        return

    headers = {
        "Content-Type": "application/json"
    }
    
    embed = {
        "title": title,
        "description": description,
        "color": color,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "footer": {
            "text": "Monitor Seriali (Xtream)"
        }
    }
    if image_url:
        embed["thumbnail"] = {"url": image_url}

    payload = {
        "embeds": [embed]
    }

    try:
        response = requests.post(DISCORD_WEBHOOK_URL, headers=headers, data=json.dumps(payload))
        response.raise_for_status()
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Powiadomienie Discord wysłane pomyślnie.")
    except requests.exceptions.RequestException as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Błąd wysyłania powiadomienia Discord: {e}")
    except Exception as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Nieoczekiwany błąd podczas przygotowywania/wysyłania powiadomienia Discord: {e}")

# --- Główna logika monitorowania ---
def monitor_new_episodes():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Rozpoczynam monitorowanie nowych odcinków...")

    if not all([XTREAM_HOST, XTREAM_PORT, XTREAM_USERNAME, XTREAM_PASSWORD]):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Błąd: Brakujące zmienne środowiskowe dla Xtream API. Sprawdź XTREAM_HOST, XTREAM_PORT, XTREAM_USERNAME, XTREAM_PASSWORD.")
        return
    
    favorites = load_json_file(FAVORITES_FILE, [])
    monitored_series_state = load_json_file(MONITORED_STATE_FILE, {})

    new_episodes_found_overall = False

    for series_id in favorites:
        series_id_str = str(series_id)
        
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Sprawdzam serial ID: {series_id_str}")

        xtream_details = get_xtream_series_details(series_id)
        if not xtream_details:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Pomięto serial {series_id_str} z powodu problemów z pobieraniem danych z Xtream.")
            continue
        
        current_episodes_by_season = xtream_details['episodes_by_season']
        series_name_for_log = xtream_details['name']
        series_cover_url = xtream_details.get('cover_url') 

        if series_id_str not in monitored_series_state:
            monitored_series_state[series_id_str] = {
                'name': series_name_for_log,
                'monitored_seasons': {}
            }
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Dodano nowy serial '{series_name_for_log}' do monitorowania.")
            # Jeśli nie chcesz powiadamiać o wszystkich istniejących odcinkach przy pierwszym dodaniu,
            # odkomentuj poniższe linie, aby "oznaczyć jako widoczne"
            # monitored_series_state[series_id_str]['monitored_seasons'] = {
            #     s_num: [ep['episode_num'] for ep in ep_list] 
            #     for s_num, ep_list in current_episodes_by_season.items()
            # }
            # continue 


        saved_seasons_data = monitored_series_state[series_id_str].get('monitored_seasons', {})
        
        new_episodes_for_this_series = False

        for season_num_str, current_ep_objects in current_episodes_by_season.items():
            saved_ep_nums = saved_seasons_data.get(season_num_str, [])
            
            for episode_obj in current_ep_objects:
                ep_num = episode_obj['episode_num']
                
                if ep_num not in saved_ep_nums:
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]   >>> ZNALEZIONO NOWY ODCINEK: {series_name_for_log} S{int(season_num_str):02d}E{int(ep_num):02d} ({episode_obj['title']})")
                    
                    notification_title = f"Nowy odcinek serialu! 🔔"
                    notification_description = (
                        f"**{series_name_for_log}**\n"
                        f"Sezon: {int(season_num_str)}\n"
                        f"Odcinek: {int(ep_num)}\n"
                        f"Tytuł: {episode_obj['title']}"
                    )
                    
                    send_discord_notification(
                        title=notification_title,
                        description=notification_description,
                        color=0x3498DB,
                        image_url=series_cover_url
                    )
                    
                    saved_ep_nums.append(ep_num)
                    new_episodes_found_overall = True
                    new_episodes_for_this_series = True
            
            monitored_series_state[series_id_str]['monitored_seasons'][season_num_str] = sorted(list(set(saved_ep_nums)))

        monitored_series_state[series_id_str]['last_checked'] = datetime.now().isoformat()
        
        if new_episodes_for_this_series:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Zakończono sprawdzanie serialu '{series_name_for_log}'. Znaleziono nowe odcinki.")
        else:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Zakończono sprawdzanie serialu '{series_name_for_log}'. Brak nowych odcinków.")

    if new_episodes_found_overall:
        save_json_file(MONITORED_STATE_FILE, monitored_series_state)
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Zaktualizowano stan monitorowanych seriali.")
    else:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Nie znaleziono żadnych nowych odcinków dla monitorowanych seriali.")

# Usunięto if __name__ == "__main__": monitor_new_episodes()
# aby funkcja była importowalna i wywoływalna z app.py/seriale.py