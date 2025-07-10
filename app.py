# app.py (zmodyfikowany)

import os
from flask import Flask, render_template, redirect, url_for

# Importuj blueprinty
from seriale import seriale_bp
from filmy import filmy_bp

# Możesz zaimportować też downloader_core, jeśli potrzebujesz dostępu do jego funkcji tutaj,
# ale same blueprinty już go importują i używają.
# from downloader_core import download_worker_thread_start, save_queue, save_completed # itp.

app = Flask(__name__)

# Rejestracja blueprintów
app.register_blueprint(seriale_bp)
app.register_blueprint(filmy_bp)

# Trasa główna przekierowuje na listę seriali
@app.route("/")
def index():
    return redirect(url_for('seriale.seriale_list')) # Domyślnie przekieruj na seriale

# Możesz dodać osobne linki do filmów i seriali w menu nawigacyjnym w HTML.
# Na przykład, jeśli chcesz mieć /filmy jako osobną stronę główną dla filmów.
@app.route("/filmy_glowna")
def filmy_glowna():
    return redirect(url_for('filmy.filmy_list'))

if __name__ == '__main__':
    # Upewnij się, że wszystkie zmienne środowiskowe są ustawione,
    # np. w pliku .env lub bezpośrednio w środowisku.
    # FLASK_APP=app.py
    # FLASK_ENV=development
    # XTREAM_HOST=twoj_host
    # XTREAM_PORT=twoj_port
    # XTREAM_USERNAME=twoj_login
    # XTREAM_PASSWORD=twoje_haslo
    # DOWNLOAD_PATH_SERIES=/sciezka/do/seriali
    # DOWNLOAD_PATH_MOVIES=/sciezka/do/filmow

    app.run(host='0.0.0.0', port=5000, debug=True)