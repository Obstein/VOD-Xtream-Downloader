from flask import Blueprint, request, jsonify, render_template_string
import os
import requests
import subprocess
from urllib.parse import quote
import json
import queue

filmy_bp = Blueprint('filmy', __name__)

XTREAM_HOST = os.getenv("XTREAM_HOST")
XTREAM_PORT = os.getenv("XTREAM_PORT")
XTREAM_USERNAME = os.getenv("XTREAM_USERNAME")
XTREAM_PASSWORD = os.getenv("XTREAM_PASSWORD")
DOWNLOAD_PATH_MOVIES = os.getenv("DOWNLOAD_PATH_MOVIES", "/downloads/Filmy")
QUEUE_FILE = "queue.json"

BASE_API = f"{XTREAM_HOST}:{XTREAM_PORT}/player_api.php?username={XTREAM_USERNAME}&password={XTREAM_PASSWORD}"

# Wspólna kolejka i statusy
from Seriale_Module import download_queue, download_status, queue_data, save_queue

@filmy_bp.route("/filmy")
def film_list():
    response = requests.get(f"{BASE_API}&action=get_vod")
    filmy = response.json()
    html = """
    <h1>Filmy</h1>
    {% for f in filmy %}
        <div>
            <img src="{{ f['stream_icon'] }}" width="100" />
            <form method="post" action="/download/film" style="display:inline" onsubmit="event.preventDefault(); downloadFilm(this, '{{ f['stream_id'] }}');">
                <input type="hidden" name="id" value="{{ f['stream_id'] }}">
                <input type="hidden" name="title" value="{{ f['name'] }}">
                <button id="btn-{{ f['stream_id'] }}">{{ download_status.get(f['stream_id'], '📥') }}</button>
            </form>
        </div>
    {% endfor %}
    <script>
    function downloadFilm(form, id) {
        const btn = document.getElementById('btn-' + id);
        btn.textContent = '⏳';
        fetch(form.action, {
            method: 'POST',
            body: new FormData(form)
        }).then(resp => {
            if (!resp.ok) {
                btn.textContent = '❌';
            } else {
                btn.textContent = '⏳';
            }
        }).catch(() => {
            btn.textContent = '❌';
        });
    }

    setInterval(() => {
        fetch('/queue/status')
            .then(resp => resp.json())
            .then(data => {
                for (const [id, status] of Object.entries(data)) {
                    const btn = document.getElementById('btn-' + id);
                    if (btn) btn.textContent = status;
                }
            });
    }, 5000);
    </script>
    """
    return render_template_string(html, filmy=filmy)

@filmy_bp.route("/download/film", methods=["POST"])
def download_film():
    film_id = request.form['id']
    title = request.form['title'].replace('/', '_')
    path = os.path.join(DOWNLOAD_PATH_MOVIES)
    os.makedirs(path, exist_ok=True)

    ext = "mp4"
    file_name = f"{title}.{ext}"
    file_path = os.path.join(path, quote(file_name.replace(' ', '_')))

    url = f"{XTREAM_HOST}:{XTREAM_PORT}/movie/{XTREAM_USERNAME}/{XTREAM_PASSWORD}/{film_id}.{ext}"

    job = {"cmd": ["wget", "-O", file_path, url], "file": file_name, "episode_id": film_id, "series": "Film", "title": title}
    download_queue.put(job)
    download_status[film_id] = "⏳"
    queue_data.append(job)
    save_queue()
    return '', 202
