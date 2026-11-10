# YouTube Downloader

A family website for downloading YouTube videos without lowering their quality. Paste a link or search by name, pick a format, press Download.

| Format | What you get |
|---|---|
| Best quality | Highest resolution (up to 4K/8K), full frame rate, HDR when available. Usually AV1 or VP9 in an MP4; play it in VLC if a player shows no picture. |
| Plays everywhere | The best H.264 + AAC streams (usually up to 1080p) as an MP4 that plays on any phone, TV or computer. |
| Audio | The original AAC audio track as M4A. |

Nothing is ever re-encoded. The chosen streams are downloaded exactly as YouTube serves them and joined by ffmpeg with a stream copy, so no frames are dropped and the audio bitrate stays the same.

**Built for reliability:**
- If a download fails, the server retries with fresh links up to three times.
- If the connection drops, the page keeps reconnecting for about two minutes.
- If the page is closed or reloaded, the download picks up where it was.
- If the browser's file transfer is interrupted, it can resume where it stopped.

**On phones:** open the site and choose *Add to Home Screen* (iPhone: Share > Add to Home Screen; Android: menu > Install app). On Android, the installed app then appears in the YouTube app's **Share** menu, which opens the video straight in the downloader.

## Put it online for free (recommended: Oracle Cloud)

Follow [deploy/oracle/GUIDE.md](deploy/oracle/GUIDE.md). Oracle's Always Free plan gives a server that runs around the clock with 10 TB of downloads a month, at no cost. One script installs everything, sets up a free https address and keeps the downloader up to date every night.

### Alternative: Render (light use only)

Render deploys straight from GitHub (https://render.com/deploy?repo=https://github.com/sorovistworks/ytdownloader), but its free plan includes only about 5 GB of downloads a month (roughly five 4K videos), sleeps after 15 minutes without visitors, and runs on a very small server. The `keep-awake` workflow keeps it awake if you add a `SITE_URL` repository variable.

### If YouTube blocks the server

YouTube sometimes blocks cloud servers ("YouTube is blocking this server right now"). Fixes, in order:

1. **Cookies:** sign in to YouTube with a spare Google account (not your main one) and export its cookies as `cookies.txt`. On Oracle, copy the file to `deploy/oracle/secrets/cookies.txt` on the server (the guide shows how); on Render, paste its contents into the `YTDLP_COOKIES` setting.
2. **Proxy:** put a residential proxy address (`http://user:pass@host:port`) in `YTDLP_PROXY`. Most reliable, usually a few dollars a month.

### Settings (environment variables)

| Name | Purpose |
|---|---|
| `APP_PASSWORD` | Family password. Empty means anyone with the link can use it. |
| `SECRET_KEY` | Signs the login cookie. Render generates it. |
| `YTDLP_COOKIES` | Optional cookies.txt contents, only if YouTube blocks the server. |
| `YTDLP_COOKIES_FILE` | Optional path to a cookies.txt file (the Oracle setup uses `/secrets/cookies.txt`). |
| `YTDLP_PROXY` | Optional proxy, only if YouTube blocks the server. |
| `MAX_ACTIVE` | Optional cap on simultaneous downloads. `0` (default) means no cap. |
| `KEEP_FILES_FOR` | Seconds a finished file stays on the server for "Save again". Default 1800. |

## Run on your own computer

Needs [uv](https://docs.astral.sh/uv/), ffmpeg and Node.js on PATH. Double-click `start.bat`, or run `uv run app.py`, then open http://127.0.0.1:8765.

If downloads start failing after a YouTube change, update the downloader: `uv lock --upgrade-package yt-dlp && uv sync`, then push.
