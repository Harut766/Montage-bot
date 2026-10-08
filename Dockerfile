FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fonts ./fonts
COPY ads ./ads
COPY bot ./bot

ENV PYTHONUNBUFFERED=1 FONTS_DIR=/app/fonts WORK_DIR=/data/work
# YouTube regularly breaks old yt-dlp versions, so update it on every start (keeps working if offline).
CMD ["sh", "-c", "pip install -q -U --no-cache-dir 'yt-dlp[default,deno]' || true; exec python -m bot.main"]
