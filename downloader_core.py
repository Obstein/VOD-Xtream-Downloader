# downloader_core.py

import os
import json
import threading
import queue
import subprocess
import time

# --- Konfiguracja plików stanu ---
QUEUE_FILE = "queue.json"
COMPLETED_FILE = "completed.json"
DOWNLOAD_LOG_FILE = "downloads.log"

# --- Inicjalizacja danych stanu ---
# Globalne listy, które będą modyfikowane
queue_data = []
completed_data = []

# Ładowanie istniejącego stanu z plików
if os.path.exists(QUEUE_FILE):
    try:
        with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
            queue_data = json.load(f)
    except json.JSONDecodeError:
        print(f"Błąd: Plik {QUEUE_FILE} jest uszkodzony lub pusty. Inicjalizuję pustą kolejkę.")
        queue_data = []
else:
    print(f"Plik {QUEUE_FILE} nie istnieje. Tworzę pustą kolejkę.")


if os.path.exists(COMPLETED_FILE):
    try:
        with open(COMPLETED_FILE, 'r', encoding='utf-8') as f:
            completed_data = json.load(f)
    except json.JSONDecodeError:
        print(f"Błąd: Plik {COMPLETED_FILE} jest uszkodzony lub pusty. Inicjalizuję pustą listę ukończonych.")
        completed_data = []
else:
    print(f"Plik {COMPLETED_FILE} nie istnieje. Tworzę pustą listę ukończonych.")


# --- Kolejki i statusy w pamięci ---
download_queue = queue.Queue()
# Statusy pobierania w toku {item_id: "⏳"/"✅"/"❌"}
# Będzie przechowywać string ID, bo XTream API używa stringów dla seriali i filmów
download_status = {}

# Wypełnienie aktywnej kolejki z pliku po starcie (jeśli były niedokończone zadania)
# Oznacz zadania w kolejce jako "w toku" (⏳) przy starcie aplikacji
for job in queue_data:
    item_id = str(job.get("item_id")) # Upewnij się, że ID jest stringiem
    if item_id:
        download_queue.put(job)
        download_status[item_id] = "⏳" # Oznacz jako w toku na start
    else:
        print(f"Ostrzeżenie: Zadanie w queue.json bez item_id, pomijam: {job}")

print(f"Załadowano {len(queue_data)} zadań do kolejki z {QUEUE_FILE}.")
print(f"Załadowano {len(completed_data)} ukończonych pozycji z {COMPLETED_FILE}.")


# --- Funkcje zapisu stanu ---
def save_queue():
    try:
        with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
            json.dump(queue_data, f, indent=4)
    except Exception as e:
        print(f"Błąd zapisu pliku {QUEUE_FILE}: {e}")

def save_completed():
    try:
        with open(COMPLETED_FILE, 'w', encoding='utf-8') as f:
            json.dump(completed_data, f, indent=4)
    except Exception as e:
        print(f"Błąd zapisu pliku {COMPLETED_FILE}: {e}")

# --- Główny worker pobierania ---
def download_worker():
    global queue_data, completed_data # Potrzebujemy modyfikować globalne listy
    while True:
        job = download_queue.get()
        if job is None: # Sygnał do zakończenia workera
            break

        # Upewnij się, że item_id jest stringiem, aby pasował do kluczy w completed_data
        item_id = str(job.get("item_id"))
        file_name = job.get("file")
        cmd = job.get("cmd")
        item_title = job.get("title", "Nieznany tytuł") # Użyj ogólnego 'title'
        item_type = job.get("item_type", "unknown")

        if not item_id or not file_name or not cmd:
            print(f"Błąd: Niekompletne zadanie w kolejce: {job}")
            download_queue.task_done()
            continue

        try:
            download_status[item_id] = "⏳"
            print(f"Start pobierania {item_type}: {item_title} (ID: {item_id})")
            with open(DOWNLOAD_LOG_FILE, "a", encoding='utf-8') as logf:
                logf.write(f"\n=== Pobieranie {item_type}: {item_title} (ID: {item_id}) ===\n")
                process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
                for line in process.stdout:
                    logf.write(line)
                process.wait()
                if process.returncode != 0:
                   raise subprocess.CalledProcessError(process.returncode, cmd)

            status = "✅"
            if item_id not in completed_data:
                completed_data.append(item_id)
                save_completed()

            # Usuń zadanie z queue_data po pomyślnym ukończeniu
            # Odśwież globalną zmienną queue_data
            queue_data[:] = [item for item in queue_data if str(item.get('item_id')) != item_id]
            save_queue()
            print(f"Ukończono pobieranie dla {item_title} z ID {item_id} ze statusem: {status}")

        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            with open(DOWNLOAD_LOG_FILE, "a", encoding='utf-8') as logf:
                logf.write(f"❌ Błąd pobierania {item_type}: {item_title} (ID: {item_id}) - {e}\n")
            status = "❌"
            print(f"Błąd pobierania dla {item_title} z ID {item_id}: {e}")
        finally:
            download_status[item_id] = status
            download_queue.task_done()


# --- Uruchamianie workera w osobnym wątku ---
download_worker_thread = threading.Thread(target=download_worker, daemon=True)
download_worker_thread.start()
print("Worker pobierania został uruchomiony.")


# Funkcja do dodawania zadań do kolejki z zewnątrz
def add_to_download_queue(job_details):
    # Sprawdź, czy zadanie już istnieje w kolejce lub zostało zakończone
    item_id = str(job_details.get("item_id")) # Upewnij się, że ID jest stringiem
    if not item_id:
        print("Błąd: Próba dodania zadania bez item_id do kolejki.")
        return False

    if item_id in completed_data:
        print(f"Zadanie o ID {item_id} już zostało zakończone. Nie dodaję do kolejki.")
        return False

    # Sprawdź, czy zadanie już jest w 'queue_data' (np. czeka na pobranie lub jest w trakcie)
    if any(str(q_job.get('item_id')) == item_id for q_job in queue_data):
        print(f"Zadanie o ID {item_id} już jest w kolejce. Nie dodaję duplikatu.")
        return False

    download_queue.put(job_details)
    download_status[item_id] = "⏳" # Oznacz jako w toku
    queue_data.append(job_details)
    save_queue()
    print(f"Dodano zadanie o ID {item_id} do kolejki.")
    return True

# Funkcje zarządzania kolejką (przeniesione z seriale.py)
def get_queue_status():
    return download_status

def get_full_queue_data():
    return queue_data

def remove_from_queue(item_id):
    global queue_data
    item_id = str(item_id) # Upewnij się, że ID jest stringiem
    # Usuń z głównej listy danych kolejki
    queue_data[:] = [item for item in queue_data if str(item.get('item_id')) != item_id]
    save_queue()
    # Usuń ze statusu, jeśli istnieje
    download_status.pop(item_id, None)
    print(f"Usunięto zadanie o ID {item_id} z kolejki.")

def reorder_queue(order_list):
    global queue_data
    # Upewnij się, że wszystkie ID w order_list są stringami
    order_list_str = [str(x) for x in order_list]
    order_map = {item_id: i for i, item_id in enumerate(order_list_str)}
    
    # Sortowanie 'queue_data' na podstawie mapy
    # Upewnij się, że item.get('item_id') jest konwertowany na string do porównania
    queue_data.sort(key=lambda x: order_map.get(str(x['item_id']), len(order_list_str)))
    save_queue()
    
    # Odświeżenie kolejki w workerze: opróżniamy i dodajemy ponownie
    with download_queue.mutex: # Zabezpiecz dostęp do wewnętrznej listy kolejki
        # Upewnij się, że worker nie jest w trakcie pobierania czegoś, co zaraz usuniemy
        # lub że poradzi sobie z pustą kolejką i ponownym dodaniem
        # Prostsze jest po prostu opróżnienie i dodanie
        while not download_queue.empty():
            try:
                download_queue.get_nowait() # Nie blokuj, jeśli pusta
            except queue.Empty:
                break
    for job in queue_data:
        download_queue.put(job)
    print("Kolejka została przestawiona.")

def get_completed_items():
    return completed_data