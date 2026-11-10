FROM python:3.12-slim

# ffmpeg joins the video and audio streams; Node.js lets yt-dlp solve YouTube's
# JavaScript checks, which unlock the 4K/60fps streams.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# YouTube changes often. Passing a new YTDLP_STAMP (e.g. today's date) rebuilds this
# step and pulls the newest yt-dlp; leaving it empty keeps the locked version.
ARG YTDLP_STAMP=""
RUN if [ -n "$YTDLP_STAMP" ]; then uv pip install --python /app/.venv/bin/python --upgrade "yt-dlp[default]"; fi
COPY app.py ./
COPY static ./static

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DOWNLOAD_DIR=/tmp/downloads \
    PORT=10000

# One process keeps all downloads in one place; threads serve many people at once.
CMD ["sh", "-c", "exec gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 32 --timeout 0"]
