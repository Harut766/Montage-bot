FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fonts ./fonts
COPY bot ./bot

ENV PYTHONUNBUFFERED=1 FONTS_DIR=/app/fonts WORK_DIR=/data/work
CMD ["python", "-m", "bot.main"]
