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
COPY app.py ./
COPY static ./static

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DOWNLOAD_DIR=/tmp/downloads \
    PORT=10000

# One process keeps all downloads in one place; threads serve many people at once.
CMD ["sh", "-c", "exec gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 32 --timeout 0"]
