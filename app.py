"""YT Downloader: a small local web app that saves YouTube videos at their maximum quality.

Quality rules:
  * The best video stream (highest resolution, then highest frame rate, then HDR) and the
    best audio stream are downloaded separately, exactly as YouTube serves them.
  * ffmpeg joins them with a stream copy. Nothing is re-encoded, so no frames are
    dropped and the audio keeps its original bitrate.

Run locally:  uv run app.py      then open http://127.0.0.1:8765
In the cloud: see Dockerfile and render.yaml. Settings come from environment variables:
  APP_PASSWORD   family password (leave unset for no password)
  SECRET_KEY     signs the login cookie
  YTDLP_COOKIES  optional cookies.txt contents, for when YouTube blocks the server
"""

from __future__ import annotations

import copy
import hmac
import os
import secrets
import re
import shutil
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from flask import Flask, abort, jsonify, request, send_file, send_from_directory, session
from werkzeug.middleware.proxy_fix import ProxyFix

HOST = "127.0.0.1"
PORT = 8765
ROOT = Path(__file__).resolve().parent
DOWNLOADS = Path(os.environ.get("DOWNLOAD_DIR", ROOT / "downloads"))
STATIC = ROOT / "static"
KEEP_FILES_FOR = int(os.environ.get("KEEP_FILES_FOR", 30 * 60))  # seconds a finished file is kept
INFO_CACHE_FOR = 30 * 60  # seconds a fetched video's details are reused
MAX_ACTIVE = int(os.environ.get("MAX_ACTIVE", 0))  # downloads at the same time; 0 means no limit
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
COOKIES_FILE: str | None = None
if os.environ.get("YTDLP_COOKIES"):
    COOKIES_FILE = str(ROOT / ".cookies.txt")
    # Pasted into a one-line env field, newlines may arrive as a literal "\n".
    Path(COOKIES_FILE).write_text(os.environ["YTDLP_COOKIES"].replace("\\n", "\n"), encoding="utf-8")

YOUTUBE_HOSTS = {"youtube.com", "youtu.be", "youtube-nocookie.com"}

# Highest resolution first, then frame rate, then HDR, then the better codec.
FORMAT_SORT = ["res", "fps", "hdr:12", "vcodec", "acodec", "abr", "asr"]

MODES = {
    # Best video + best audio, joined into MP4 (stream copy).
    "mp4": {"format": "bv*+ba/b", "merge_output_format": "mp4"},
    # Same streams, joined into MKV (stream copy). Holds every codec natively.
    "mkv": {"format": "bv*+ba/b", "merge_output_format": "mkv"},
    # Best audio only, moved out of its container untouched (.opus or .m4a).
    "audio": {
        "format": "ba/b",
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "best"}],
    },
}

app = Flask(__name__, static_folder=None)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SECURE_COOKIES", "1" if os.environ.get("RENDER") else "0") == "1",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 90,
)
DOWNLOADS.mkdir(parents=True, exist_ok=True)
jobs: dict[str, dict] = {}
info_cache: dict[str, tuple[float, dict]] = {}
lock = threading.Lock()


# ---------------------------------------------------------------- helpers

def base_opts() -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format_sort": FORMAT_SORT,
        "js_runtimes": {"node": {}, "deno": {}},
        "windowsfilenames": True,
        "noprogress": True,
        **({"cookiefile": COOKIES_FILE} if COOKIES_FILE else {}),
    }


def is_youtube(url: str) -> bool:
    try:
        host = (urlparse(url.strip()).hostname or "").lower()
    except ValueError:
        return False
    host = host.removeprefix("www.").removeprefix("m.").removeprefix("music.")
    return host in YOUTUBE_HOSTS


def clean_error(err: Exception) -> str:
    msg = str(err).replace("ERROR: ", "").strip()
    if "[youtube]" in msg and ":" in msg:
        msg = msg.split(":", 2)[-1].strip()
    if "not a bot" in msg or "HTTP Error 429" in msg:
        return "YouTube is blocking this server right now. Try again in a little while."
    if "Sign in to confirm your age" in msg:
        return "This video is age-restricted and needs a signed-in account."
    if "Private video" in msg:
        return "This video is private."
    if "Video unavailable" in msg:
        return "This video is unavailable."
    return msg or "Something went wrong."


def size_of(fmt: dict) -> int:
    return int(fmt.get("filesize") or fmt.get("filesize_approx") or 0)


def describe(info: dict) -> dict:
    """Summarise the streams the best-quality selection will download."""
    with yt_dlp.YoutubeDL({**base_opts(), "format": "bv*+ba/b"}) as ydl:
        picked = ydl.process_ie_result(copy.deepcopy(info), download=False)

    parts = picked.get("requested_formats") or [picked]
    video = next((f for f in parts if f.get("vcodec") not in (None, "none")), None)
    audio = next((f for f in parts if f.get("acodec") not in (None, "none")), None)

    def codec(name: str | None) -> str:
        if not name or name == "none":
            return ""
        n = name.lower()
        for key, label in (("av01", "AV1"), ("vp09", "VP9"), ("vp9", "VP9"), ("avc", "H.264"),
                           ("hev", "HEVC"), ("hvc", "HEVC"), ("opus", "Opus"), ("mp4a", "AAC"),
                           ("ec-3", "E-AC-3"), ("ac-3", "AC-3"), ("vorbis", "Vorbis")):
            if n.startswith(key):
                return label
        return name.split(".")[0].upper()

    height = (video or {}).get("height") or 0
    width = (video or {}).get("width") or 0
    short = min(width, height) if width and height else height
    label = {4320: "8K", 2160: "4K", 1440: "1440p", 1080: "1080p", 720: "720p"}.get(short, f"{short}p" if short else "")

    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "is_live": bool(info.get("is_live")),
        "video": video and {
            "label": label,
            "width": width,
            "height": height,
            "fps": video.get("fps"),
            "hdr": (video.get("dynamic_range") or "SDR") != "SDR",
            "codec": codec(video.get("vcodec")),
            "kbps": round(video.get("tbr") or video.get("vbr") or 0),
        },
        "audio": audio and {
            "codec": codec(audio.get("acodec")),
            "kbps": round(audio.get("abr") or audio.get("tbr") or 0),
            "channels": audio.get("audio_channels"),
            "khz": round((audio.get("asr") or 0) / 1000, 1),
        },
        "size": sum(size_of(f) for f in parts),
        "audio_size": size_of(audio) if audio else 0,
    }


def get_info(url: str, fresh: bool = False) -> dict:
    """Extract (or reuse) the full, unprocessed list of formats for a video."""
    now = time.time()
    with lock:
        for key, (ts, _) in list(info_cache.items()):
            if now - ts > INFO_CACHE_FOR:
                info_cache.pop(key, None)
        if not fresh and url in info_cache:
            return copy.deepcopy(info_cache[url][1])

    with yt_dlp.YoutubeDL(base_opts()) as ydl:
        info = ydl.extract_info(url, download=False, process=False)
        if info.get("_type") in ("playlist", "url", "url_transparent") and info.get("url"):
            info = ydl.extract_info(info["url"], download=False, process=False)
        if info.get("_type") == "playlist":
            raise yt_dlp.utils.DownloadError("That link is a playlist. Paste the link of a single video.")
    info = yt_dlp.YoutubeDL.sanitize_info(info, remove_private_keys=True)
    with lock:
        info_cache[url] = (now, info)
    return copy.deepcopy(info)


def sweep_old_files() -> None:
    now = time.time()
    with lock:
        for jid, job in list(jobs.items()):
            if job["status"] in ("done", "error") and now - job["updated"] > KEEP_FILES_FOR:
                jobs.pop(jid, None)
    if DOWNLOADS.exists():
        for folder in DOWNLOADS.iterdir():
            if folder.is_dir() and folder.name not in jobs and now - folder.stat().st_mtime > KEEP_FILES_FOR:
                shutil.rmtree(folder, ignore_errors=True)


# ---------------------------------------------------------------- download worker

def run_job(jid: str, url: str, mode: str) -> None:
    job = jobs[jid]
    folder = DOWNLOADS / jid
    folder.mkdir(parents=True, exist_ok=True)

    def update(**kw):
        with lock:
            job.update(kw, updated=time.time())

    # Streams the current attempt downloads, in order: [[format_id, expected_bytes, is_video], ...]
    plan: list[list] = []

    def on_progress(d: dict) -> None:
        if d["status"] not in ("downloading", "finished") or not plan:
            return
        fid = (d.get("info_dict") or {}).get("format_id")
        index = next((i for i, p in enumerate(plan) if p[0] == fid), 0)
        reported = d.get("total_bytes") or d.get("total_bytes_estimate")
        if reported:
            plan[index][1] = reported
        done = d.get("downloaded_bytes") or 0
        if d["status"] == "finished":
            done = plan[index][1] or done
        before = sum(p[1] for p in plan[:index])
        overall = sum(p[1] for p in plan) or 1
        stage = "Downloading video" if plan[index][2] else "Downloading audio"
        update(stage=stage, percent=round(min(99.0, 100.0 * (before + done) / overall), 1),
               speed=d.get("speed"), eta=d.get("eta"), downloaded=before + done, total=overall,
               kind="video" if plan[index][2] else "audio",
               part_pct=round(min(100.0, 100.0 * done / (plan[index][1] or done or 1)), 1))

    def on_post(d: dict) -> None:
        if d["status"] == "started":
            name = d.get("postprocessor")
            text = {"Merger": "Joining video and audio",
                    "ExtractAudio": "Saving audio"}.get(name)
            if text:
                update(stage=text, percent=99.5, phase="join")

    opts = {
        **base_opts(),
        **copy.deepcopy(MODES[mode]),
        "paths": {"home": str(folder), "temp": str(folder)},
        "outtmpl": "%(title).150B [%(id)s].%(ext)s",
        "progress_hooks": [on_progress],
        "postprocessor_hooks": [on_post],
        "concurrent_fragment_downloads": 4,
        "retries": 10,
        "fragment_retries": 10,
    }

    try:
        update(status="running", stage="Preparing")
        for attempt in (0, 1):
            try:
                info = get_info(url, fresh=attempt == 1)
                with yt_dlp.YoutubeDL(opts) as ydl:
                    picked = ydl.process_ie_result(copy.deepcopy(info), download=False)
                    plan[:] = [[f.get("format_id"), size_of(f), f.get("vcodec") not in (None, "none")]
                               for f in (picked.get("requested_formats") or [picked])]
                    ydl.process_ie_result(info, download=True)
                break
            except yt_dlp.utils.DownloadError:
                if attempt == 1:
                    raise
                for leftover in folder.iterdir():  # retry once with freshly signed links
                    leftover.unlink(missing_ok=True)
                update(stage="Retrying", percent=0)

        files = [p for p in folder.iterdir() if p.is_file()
                 and not p.name.endswith((".part", ".ytdl", ".temp"))
                 and not re.search(r"\.f\d+[\w-]*\.\w+$", p.name)]  # skip unmerged stream parts
        if not files:
            raise RuntimeError("The download finished but no file was produced.")
        final = max(files, key=lambda p: p.stat().st_size)
        update(status="done", stage="Ready", percent=100, file=final.name, size=final.stat().st_size)
    except Exception as err:  # noqa: BLE001 - every failure is reported to the page
        update(status="error", error=clean_error(err))


# ---------------------------------------------------------------- routes

OPEN_PATHS = {"/", "/api/health", "/api/session", "/api/login"}


@app.before_request
def require_password():
    if APP_PASSWORD and request.path not in OPEN_PATHS and not session.get("ok"):
        return jsonify(error="Enter the password first.", locked=True), 401
    return None


@app.get("/api/session")
def api_session():
    return jsonify(required=bool(APP_PASSWORD), ok=bool(not APP_PASSWORD or session.get("ok")))


@app.post("/api/login")
def api_login():
    given = str((request.get_json(silent=True) or {}).get("password", ""))
    if APP_PASSWORD and hmac.compare_digest(given.encode(), APP_PASSWORD.encode()):
        session.permanent = True
        session["ok"] = True
        return jsonify(ok=True)
    time.sleep(1.5)  # slows down guessing
    return jsonify(error="Wrong password."), 401


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.post("/api/info")
def api_info():
    url = (request.get_json(silent=True) or {}).get("url", "").strip()
    if not is_youtube(url):
        return jsonify(error="Paste a YouTube video link (youtube.com or youtu.be)."), 400
    try:
        info = get_info(url)
        return jsonify(describe(info))
    except Exception as err:  # noqa: BLE001
        return jsonify(error=clean_error(err)), 400


@app.post("/api/download")
def api_download():
    body = request.get_json(silent=True) or {}
    url = body.get("url", "").strip()
    mode = body.get("mode", "mp4")
    if not is_youtube(url):
        return jsonify(error="Paste a YouTube video link (youtube.com or youtu.be)."), 400
    if mode not in MODES:
        return jsonify(error="Unknown format."), 400
    sweep_old_files()
    jid = uuid.uuid4().hex[:12]
    with lock:
        if MAX_ACTIVE and sum(j["status"] in ("queued", "running") for j in jobs.values()) >= MAX_ACTIVE:
            return jsonify(error="Another download is running. Try again in a minute."), 429
        jobs[jid] = {"status": "queued", "stage": "Queued", "percent": 0, "updated": time.time()}
    threading.Thread(target=run_job, args=(jid, url, mode), daemon=True).start()
    return jsonify(job=jid)


@app.get("/api/progress/<jid>")
def api_progress(jid: str):
    with lock:
        job = jobs.get(jid)
        if not job:
            return jsonify(error="Unknown download."), 404
        return jsonify({k: v for k, v in job.items() if k != "updated"})


@app.get("/api/file/<jid>")
def api_file(jid: str):
    with lock:
        job = jobs.get(jid)
    if not job or job.get("status") != "done":
        abort(404)
    path = DOWNLOADS / jid / job["file"]
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=path.name)


@app.get("/api/health")
def api_health():
    return jsonify(ffmpeg=bool(shutil.which("ffmpeg")), ytdlp=yt_dlp.version.__version__)


if __name__ == "__main__":
    if not shutil.which("ffmpeg"):
        print("ffmpeg was not found on PATH. Install it first:  winget install Gyan.FFmpeg")
        raise SystemExit(1)
    shutil.rmtree(DOWNLOADS, ignore_errors=True)  # clear files left from a previous run
    DOWNLOADS.mkdir(exist_ok=True)
    url = f"http://{HOST}:{PORT}"
    print(f"YT Downloader running at {url}  (Ctrl+C to stop)")
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=HOST, port=PORT, threaded=True, debug=False)
