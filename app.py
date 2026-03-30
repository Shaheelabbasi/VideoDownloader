import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, request, send_file
import yt_dlp


app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Runtime configuration (no external database/queue)
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "2"))
MAX_QUEUE = int(os.getenv("MAX_QUEUE", "50"))
FILE_TTL_SECONDS = int(os.getenv("FILE_TTL_SECONDS", "86400"))
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "").strip()
API_TOKEN = os.getenv("API_TOKEN", "").strip()

# In-memory job store: {job_id: {...}}
JOBS = {}
JOBS_LOCK = threading.Lock()
QUEUE_SEM = threading.BoundedSemaphore(MAX_QUEUE)
EXECUTOR = ThreadPoolExecutor(max_workers=MAX_CONCURRENT)
BACKGROUND_STARTED = False
BACKGROUND_LOCK = threading.Lock()

SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _set_job(job_id, **kwargs):
    with JOBS_LOCK:
        JOBS.setdefault(job_id, {}).update(kwargs)


def _download_worker(job_id, url):
    acquired = False
    try:
        acquired = True
        _set_job(job_id, status="starting", updated_at=time.time())

        def progress_hook(d):
            if d.get("status") == "downloading":
                _set_job(
                    job_id,
                    status="downloading",
                    percent=d.get("_percent_str", "").strip(),
                    speed=d.get("_speed_str", "").strip(),
                    eta=d.get("_eta_str", "").strip(),
                    updated_at=time.time(),
                )
            elif d.get("status") == "finished":
                _set_job(job_id, status="processing", percent="100%", updated_at=time.time())

        ydl_opts = {
            "outtmpl": os.path.join(DOWNLOAD_DIR, f"{job_id}-%(title).80B.%(ext)s"),
            "format": "best",
            "progress_hooks": [progress_hook],
            "noplaylist": True,
            "restrictfilenames": True,
            "retries": 3,
            "fragment_retries": 3,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)
            title = info.get("title") or "download"

        safe_title = SAFE_NAME_RE.sub("_", title).strip("._-")
        if not safe_title:
            safe_title = "download"

        _set_job(
            job_id,
            status="completed",
            file_path=file_path,
            filename=f"{safe_title}{os.path.splitext(file_path)[1]}",
            updated_at=time.time(),
        )
    except Exception as exc:
        _set_job(job_id, status="error", error="Download failed", updated_at=time.time())
        app.logger.exception("Download failed for job_id=%s: %s", job_id, exc)
    finally:
        if acquired:
            QUEUE_SEM.release()


def _cleanup_worker():
    while True:
        now = time.time()
        stale_jobs = []
        with JOBS_LOCK:
            for job_id, job in list(JOBS.items()):
                status = job.get("status")
                updated_at = job.get("updated_at") or job.get("created_at") or now
                if status in {"completed", "error"} and now - updated_at > FILE_TTL_SECONDS:
                    stale_jobs.append((job_id, job))
            for job_id, _ in stale_jobs:
                JOBS.pop(job_id, None)
        for job_id, job in stale_jobs:
            file_path = job.get("file_path")
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    app.logger.warning("Failed to delete file for job_id=%s", job_id)
        time.sleep(60)


def _ensure_background_workers():
    global BACKGROUND_STARTED
    with BACKGROUND_LOCK:
        if BACKGROUND_STARTED:
            return
        cleaner = threading.Thread(target=_cleanup_worker, daemon=True)
        cleaner.start()
        BACKGROUND_STARTED = True


_ensure_background_workers()


def _validate_url(url):
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


@app.before_request
def _require_token():
    if not API_TOKEN:
        return None
    if request.path.startswith("/api/"):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            token = request.headers.get("X-API-Key", "")
        if token != API_TOKEN:
            return jsonify({"error": "Unauthorized"}), 401
    return None


@app.after_request
def _add_cors_headers(response):
    if ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGINS
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-API-Key"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/download")
def api_download():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Missing URL"}), 400
    if not _validate_url(url):
        return jsonify({"error": "Invalid URL"}), 400

    if not QUEUE_SEM.acquire(blocking=False):
        return jsonify({"error": "Server busy, try again later"}), 429

    job_id = str(uuid.uuid4())
    _set_job(
        job_id,
        status="queued",
        url=url,
        created_at=time.time(),
        updated_at=time.time(),
        expires_at=time.time() + FILE_TTL_SECONDS,
    )

    try:
        EXECUTOR.submit(_download_worker, job_id, url)
    except Exception:
        QUEUE_SEM.release()
        _set_job(job_id, status="error", error="Queue failure", updated_at=time.time())
        return jsonify({"error": "Server busy, try again later"}), 503

    return jsonify({"job_id": job_id})


@app.post("/api/preview")
def api_preview():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Missing URL"}), 400
    if not _validate_url(url):
        return jsonify({"error": "Invalid URL"}), 400

    ydl_opts = {
        "skip_download": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        response = {
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "uploader": info.get("uploader"),
            "webpage_url": info.get("webpage_url") or url,
        }
        return jsonify(response)
    except Exception as exc:
        app.logger.exception("Preview failed for url=%s: %s", url, exc)
        return jsonify({"error": "Preview failed"}), 400


@app.get("/api/status/<job_id>")
def api_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    response = {
        "job_id": job_id,
        "status": job.get("status"),
        "percent": job.get("percent"),
        "speed": job.get("speed"),
        "eta": job.get("eta"),
        "error": job.get("error"),
        "expires_at": job.get("expires_at"),
    }

    if job.get("status") == "completed":
        response["download_url"] = f"/download/{job_id}"

    return jsonify(response)


@app.get("/download/<job_id>")
def download_file(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or job.get("status") != "completed":
        return jsonify({"error": "File not ready"}), 404

    file_path = job.get("file_path")
    if not file_path or not os.path.exists(file_path):
        return jsonify({"error": "File missing"}), 404

    return send_file(
        file_path,
        as_attachment=True,
        download_name=job.get("filename") or os.path.basename(file_path),
    )


if __name__ == "__main__":
    _ensure_background_workers()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
