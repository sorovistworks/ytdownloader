"""YouTube Downloader: a small web app that saves YouTube videos without lowering their quality.

How quality is kept:
  * The chosen video stream and audio stream are downloaded exactly as YouTube serves them.
  * ffmpeg joins them with a stream copy. Nothing is re-encoded, so no frames are dropped
    and the audio keeps its original bitrate.

Formats:
  best    highest resolution, then frame rate, then HDR (often AV1 or VP9), as MP4
  compat  the best H.264 + AAC streams (usually up to 1080p), as MP4 that plays everywhere
  audio   the original AAC audio track as M4A (falls back to the best audio track)

Run locally:  uv run app.py      then open http://127.0.0.1:8765
In the cloud: see Dockerfile and render.yaml. Settings come from environment variables:
  APP_PASSWORD   family password (leave unset for no password)
  SECRET_KEY     signs the login cookie
  YTDLP_COOKIES  optional cookies.txt contents, for when YouTube blocks the server
  YTDLP_PROXY    optional proxy (e.g. http://user:pass@host:port), for when YouTube blocks the server
"""

from __future__ import annotations

import copy
import hmac
import os
import re
import secrets
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
PROXY = os.environ.get("YTDLP_PROXY", "").strip()
COOKIES_FILE: str | None = None
if os.environ.get("YTDLP_COOKIES"):
    COOKIES_FILE = str(ROOT / ".cookies.txt")
    # Pasted into a one-line env field, newlines may arrive as a literal "\n".
    Path(COOKIES_FILE).write_text(os.environ["YTDLP_COOKIES"].replace("\\n", "\n"), encoding="utf-8")

YOUTUBE_HOSTS = {"youtube.com", "youtu.be", "youtube-nocookie.com"}
PUBLIC_FILES = {"manifest.webmanifest", "sw.js", "icon.svg", "icon-192.png", "icon-512.png", "apple-touch-icon.png"}

# Highest resolution first, then frame rate, then HDR, then the better codec.
FORMAT_SORT = ["res", "fps", "hdr:12", "vcodec", "acodec", "abr", "asr"]

MODES = {
    "best": {"format": "bv*+ba/b", "merge_output_format": "mp4"},
    "compat": {
        "format": "bv*[vcodec^=avc1]+ba[acodec^=mp4a]/b[vcodec^=avc1][acodec^=mp4a]/bv*[vcodec^=avc1]+ba/b[ext=mp4]/bv*+ba/b",
        "merge_output_format": "mp4",
    },
    "audio": {
        "format": "ba[ext=m4a]/ba/b",
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "best"}],
    },
}

app = Flask(__name__, static_folder=None)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SECURE_COOKIES", "1" if os.environ.get("RENDER") else "0") == "1",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 365,
)
DOWNLOADS.mkdir(parents=True, exist_ok=True)

jobs: dict[str, dict] = {}
info_cache: dict[str, tuple[float, dict]] = {}
lock = threading.Lock()


# ---------------------------------------------------------------- helpers

def base_opts() -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format_sort": FORMAT_SORT,
        "js_runtimes": {"node": {}, "deno": {}},
        "windowsfilenames": True,
        "noprogress": True,
        "socket_timeout": 30,
    }
    if COOKIES_FILE:
        opts["cookiefile"] = COOKIES_FILE
    if PROXY:
        opts["proxy"] = PROXY
    return opts


def is_youtube(url: str) -> bool:
    try:
        host = (urlparse(url.strip()).hostname or "").lower()
    except ValueError:
        return False
    host = host.removeprefix("www.").removeprefix("m.").removeprefix("music.")
    return host in YOUTUBE_HOSTS


def looks_like_url(text: str) -> bool:
    return bool(re.match(r"^(https?://|www\.)\S+$", text.strip(), re.I))


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
    if "Video unavailable" in msg or "This video is not available" in msg:
        return "This video is unavailable."
    if "Requested format is not available" in msg:
        return "This format isn't available for this video. Try another one."
    return msg or "Something went wrong."


def size_of(fmt: dict) -> int:
    return int(fmt.get("filesize") or fmt.get("filesize_approx") or 0)


def codec_name(name: str | None) -> str:
    if not name or name == "none":
        return ""
    n = name.lower()
    for key, label in (("av01", "AV1"), ("vp09", "VP9"), ("vp9", "VP9"), ("avc", "H.264"),
                       ("hev", "HEVC"), ("hvc", "HEVC"), ("opus", "Opus"), ("mp4a", "AAC"),
                       ("ec-3", "E-AC-3"), ("ac-3", "AC-3"), ("vorbis", "Vorbis")):
        if n.startswith(key):
            return label
    return name.split(".")[0].upper()


def pick(info: dict, mode: str) -> dict | None:
    """Describe the streams a mode would download, without downloading anything."""
    try:
        with yt_dlp.YoutubeDL({**base_opts(), "format": MODES[mode]["format"]}) as ydl:
            picked = ydl.process_ie_result(copy.deepcopy(info), download=False)
    except yt_dlp.utils.DownloadError:
        return None
    parts = picked.get("requested_formats") or [picked]
    video = next((f for f in parts if f.get("vcodec") not in (None, "none")), None)
    audio = next((f for f in parts if f.get("acodec") not in (None, "none")), None)
    if mode == "audio":
        video = None
        ext = "m4a" if audio and (audio.get("acodec") or "").startswith("mp4a") else "opus"
        size = size_of(audio) if audio else 0
    else:
        ext = "mp4"
        size = sum(size_of(f) for f in parts)
    return {
        "ext": ext,
        "size": size,
        "video": video and {
            "width": video.get("width"),
            "height": video.get("height"),
            "fps": video.get("fps"),
            "hdr": (video.get("dynamic_range") or "SDR") != "SDR",
            "codec": codec_name(video.get("vcodec")),
        },
        "audio": audio and {
            "codec": codec_name(audio.get("acodec")),
            "kbps": round(audio.get("abr") or audio.get("tbr") or 0),
            "channels": audio.get("audio_channels"),
            "khz": round((audio.get("asr") or 0) / 1000, 1),
        },
    }


def describe(info: dict) -> dict:
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "is_live": info.get("live_status") == "is_live" or bool(info.get("is_live")),
        "modes": {mode: pick(info, mode) for mode in MODES},
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
        if info.get("_type") in ("url", "url_transparent") and info.get("url"):
            info = ydl.extract_info(info["url"], download=False, process=False)
        if info.get("_type") == "playlist":
            raise yt_dlp.utils.DownloadError("That link is a playlist. Paste the link of a single video.")
    info = yt_dlp.YoutubeDL.sanitize_info(info, remove_private_keys=True)
    with lock:
        info_cache[url] = (now, info)
    return copy.deepcopy(info)


def search(query: str, count: int = 8) -> list[dict]:
    opts = {**base_opts(), "extract_flat": "in_playlist", "noplaylist": False}
    with yt_dlp.YoutubeDL(opts) as ydl:
        result = ydl.extract_info(f"ytsearch{count}:{query}", download=False)
    items = []
    for e in result.get("entries") or []:
        vid = e.get("id")
        if not vid or e.get("live_status") == "is_live":
            continue
        thumbs = e.get("thumbnails") or []
        items.append({
            "url": f"https://www.youtube.com/watch?v={vid}",
            "title": e.get("title"),
            "channel": e.get("channel") or e.get("uploader"),
            "duration": e.get("duration"),
            "thumbnail": (thumbs[-1].get("url") if thumbs else None) or f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
        })
    return items


def sweep_old_files() -> None:
    now = time.time()
    with lock:
        for jid, job in list(jobs.items()):
            if job["status"] in ("done", "error") and now - job["updated"] > KEEP_FILES_FOR:
                jobs.pop(jid, None)
        active = set(jobs)
    for folder in DOWNLOADS.iterdir():
        if folder.is_dir() and folder.name not in active and now - folder.stat().st_mtime > KEEP_FILES_FOR:
            shutil.rmtree(folder, ignore_errors=True)


def sweeper() -> None:
    while True:
        time.sleep(300)
        try:
            sweep_old_files()
        except OSError:
            pass


threading.Thread(target=sweeper, daemon=True).start()


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
        is_video = plan[index][2] and mode != "audio"
        update(stage="Downloading video" if is_video else "Downloading audio",
               percent=round(min(99.0, 100.0 * (before + done) / overall), 1),
               speed=d.get("speed"), downloaded=before + done, total=overall,
               kind="video" if is_video else "audio",
               part_pct=round(min(100.0, 100.0 * done / (plan[index][1] or done or 1)), 1))

    def on_post(d: dict) -> None:
        if d["status"] == "started":
            text = {"Merger": "Joining video and audio", "ExtractAudio": "Saving audio"}.get(d.get("postprocessor"))
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
        "retries": 15,
        "fragment_retries": 15,
        "file_access_retries": 5,
    }

    try:
        update(status="running", stage="Preparing")
        for attempt in range(3):
            try:
                info = get_info(url, fresh=attempt > 0)
                with yt_dlp.YoutubeDL(opts) as ydl:
                    picked = ydl.process_ie_result(copy.deepcopy(info), download=False)
                    plan[:] = [[f.get("format_id"), size_of(f), f.get("vcodec") not in (None, "none")]
                               for f in (picked.get("requested_formats") or [picked])]
                    ydl.process_ie_result(info, download=True)
                break
            except yt_dlp.utils.DownloadError as err:
                permanent = any(s in str(err) for s in ("Private video", "Video unavailable", "confirm your age", "playlist"))
                if attempt == 2 or permanent:
                    raise
                for leftover in folder.iterdir():  # try again with freshly signed links
                    leftover.unlink(missing_ok=True)
                update(stage="Retrying", percent=0, part_pct=0)
                time.sleep(3 * (attempt + 1))

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

OPEN_PATHS = {"/", "/api/health", "/api/session", "/api/login"} | {f"/{n}" for n in PUBLIC_FILES}


@app.before_request
def require_password():
    if APP_PASSWORD and request.path not in OPEN_PATHS and not session.get("ok"):
        return jsonify(error="Enter the password first.", locked=True), 401
    return None


@app.get("/")
def index():
    resp = send_from_directory(STATIC, "index.html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/<name>")
def public_file(name: str):
    if name not in PUBLIC_FILES:
        abort(404)
    return send_from_directory(STATIC, name)


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


@app.post("/api/lookup")
def api_lookup():
    """A YouTube link returns the video's details; anything else is a YouTube search."""
    text = str((request.get_json(silent=True) or {}).get("q", "")).strip()
    if not text:
        return jsonify(error="Paste a YouTube link or type what to search for."), 400
    try:
        if is_youtube(text):
            return jsonify(kind="video", video=describe(get_info(text)))
        if looks_like_url(text):
            return jsonify(error="Only YouTube links work here."), 400
        results = search(text[:200])
        if not results:
            return jsonify(error="Nothing found. Try other words."), 404
        return jsonify(kind="results", results=results)
    except Exception as err:  # noqa: BLE001
        return jsonify(error=clean_error(err)), 400


@app.post("/api/download")
def api_download():
    body = request.get_json(silent=True) or {}
    url = str(body.get("url", "")).strip()
    mode = body.get("mode", "best")
    if not is_youtube(url):
        return jsonify(error="Paste a YouTube video link."), 400
    if mode not in MODES:
        return jsonify(error="Unknown format."), 400
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
            return jsonify(error="This download is no longer on the server. Start it again.", gone=True), 404
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
    # Range requests are supported, so an interrupted browser download can resume.
    return send_file(path, as_attachment=True, download_name=path.name, conditional=True)


@app.get("/api/health")
def api_health():
    return jsonify(ok=True, ffmpeg=bool(shutil.which("ffmpeg")), ytdlp=yt_dlp.version.__version__)


if __name__ == "__main__":
    if not shutil.which("ffmpeg"):
        print("ffmpeg was not found on PATH. Install it first:  winget install Gyan.FFmpeg")
        raise SystemExit(1)
    for old in DOWNLOADS.iterdir():  # clear files left from a previous run
        shutil.rmtree(old, ignore_errors=True) if old.is_dir() else old.unlink(missing_ok=True)
    url = f"http://{HOST}:{PORT}"
    print(f"YouTube Downloader running at {url}  (Ctrl+C to stop)")
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=HOST, port=PORT, threaded=True, debug=False)
