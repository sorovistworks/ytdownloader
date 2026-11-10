# YouTube Downloader

A small website that downloads a YouTube video at its maximum quality: the highest resolution (up to 4K or 8K), the full frame rate (60 fps stays 60 fps), HDR when the video has it, and the original audio.

## How quality is kept

YouTube stores the best video and the best audio as separate streams. The app downloads both exactly as YouTube serves them, then ffmpeg joins them by **stream copy**. Nothing is re-encoded, so no frames are dropped and the audio bitrate is not reduced.

| Option | What you get |
|---|---|
| MP4 | Best video + best audio in an `.mp4` (stream copy). |
| MKV | The same streams in an `.mkv`. Best for VLC and video editors. |
| Audio only | The best audio track, untouched (`.opus` or `.m4a`). |

4K and 8K videos are usually AV1 or VP9. If a player shows no picture, use VLC.

## Put it online (Render)

1. Open https://render.com/deploy?repo=https://github.com/sorovistworks/ytdownloader and sign in with GitHub.
2. Type a family password in **APP_PASSWORD** (leave it empty for no password), then press **Deploy**.
3. After the build (about 5 minutes), Render shows the site's address, like `https://youtube-downloader-xxxx.onrender.com`. Share that and the password with your family.

Every push to `main` redeploys the site.

**Free plan:** the site sleeps after 15 minutes without visitors and takes about a minute to wake up on the next visit. Render's paid Starter plan keeps it awake.

**If downloads fail with "YouTube is blocking this server":** YouTube sometimes blocks cloud servers. Export the cookies of a spare Google account as `cookies.txt` (for example with the "Get cookies.txt LOCALLY" browser extension) and paste the file's contents into a Render environment variable named `YTDLP_COOKIES`. Don't use your main account.

### Settings (environment variables)

| Name | Purpose |
|---|---|
| `APP_PASSWORD` | Family password. Empty means anyone with the link can use it. |
| `SECRET_KEY` | Signs the login cookie. Render generates it. |
| `YTDLP_COOKIES` | Optional cookies.txt contents, only if YouTube blocks the server. |
| `MAX_ACTIVE` | Optional cap on simultaneous downloads. `0` (default) means no cap. |
| `KEEP_FILES_FOR` | Seconds a finished file stays on the server for "Save again". Default 1800. |

## Run on your own computer

Needs [uv](https://docs.astral.sh/uv/), ffmpeg and Node.js on PATH. Double-click `start.bat`, or run `uv run app.py`, then open http://127.0.0.1:8765.

If downloads start failing after a YouTube change, update the downloader: `uv lock --upgrade-package yt-dlp && uv sync`, then push.
