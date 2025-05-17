# Katalog VOD na Unraid - Flask + Docker + Xtream Downloader

import os
import requests
from flask import Flask, render_template_string, request, jsonify
import subprocess
from urllib.parse import quote

app = Flask(__name__)

# Zmienne środowiskowe
XTREAM_HOST = os.getenv("XTREAM_HOST")
XTREAM_PORT = os.getenv("XTREAM_PORT")
XTREAM_USERNAME = os.getenv("XTREAM_USERNAME")
XTREAM_PASSWORD = os.getenv("XTREAM_PASSWORD")
DOWNLOAD_PATH_MOVIES = os.getenv("DOWNLOAD_PATH_MOVIES", "/downloads/Filmy")
DOWNLOAD_PATH_SERIES = os.getenv("DOWNLOAD_PATH_SERIES", "/downloads/Seriale")
RETRY_COUNT = int(os.getenv("RETRY_COUNT", 3))

XTREAM_API = f"{XTREAM_HOST}:{XTREAM_PORT}/player_api.php?username={XTREAM_USERNAME}&password={XTREAM_PASSWORD}"

# HTML szablon (bardzo uproszczony)
TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Katalog VOD</title>
    <style>
        body { font-family: sans-serif; }
        .item { margin-bottom: 20px; }
        button { padding: 5px 10px; }
    </style>
</head>
<body>
    <h1>Katalog Filmów</h1>
    {% for movie in movies %}
        <div class="item">
            <strong>{{ movie['name'] }}</strong> ({{ movie.get('rating', 'Brak ocen') }})<br>
            <em>{{ movie.get('genre', 'Brak gatunku') }}</em><br>
            {{ movie.get('plot', '') }}<br>
            <button onclick="download('{{ movie['stream_id'] }}', '{{ movie['name'] }}')">Pobierz</button>
        </div>
    {% endfor %}

    <script>
        function download(id, name) {
            fetch('/download', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: id, name: name })
            }).then(res => res.json()).then(data => alert(data.message))
        }
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    response = requests.get(f"{XTREAM_API}&action=get_vod_streams")
    movies = response.json()
    return render_template_string(TEMPLATE, movies=movies)

@app.route("/download", methods=["POST"])
def download():
    data = request.get_json()
    stream_id = data['id']
    name = data['name']
    stream_url = f"{XTREAM_HOST}:{XTREAM_PORT}/movie/{XTREAM_USERNAME}/{XTREAM_PASSWORD}/{stream_id}.mp4"

    sanitized_name = quote(name.replace(' ', '_'))
    dest_path = os.path.join(DOWNLOAD_PATH_MOVIES, f"{sanitized_name}.mp4")

    success = False
    for attempt in range(RETRY_COUNT):
        try:
            subprocess.run(["wget", "-O", dest_path, stream_url], check=True)
            success = True
            break
        except subprocess.CalledProcessError:
            continue

    if success:
        return jsonify({"message": f"Pobrano: {name}"})
    else:
        return jsonify({"message": f"Nieudane pobieranie: {name}"}), 500

from seriale import seriale_bp
app.register_blueprint(seriale_bp)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)


