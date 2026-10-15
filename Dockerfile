FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
       libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the rembg models so the first Outfit request is fast:
# u2net for objects (shoes, accessories) and u2net_cloth_seg to segment garments off a worn/held photo.
RUN python -c "from rembg import new_session; new_session('u2net'); new_session('u2net_cloth_seg')"

COPY fonts ./fonts
COPY ads ./ads
COPY bot ./bot

ENV PYTHONUNBUFFERED=1 FONTS_DIR=/app/fonts WORK_DIR=/data/work
# YouTube regularly breaks old yt-dlp versions, so update it on every start (keeps working if offline).
CMD ["sh", "-c", "pip install -q -U --no-cache-dir 'yt-dlp[default,deno]' || true; exec python -m bot.main"]
