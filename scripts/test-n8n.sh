#!/bin/sh
# Sends a tiny transcript to the n8n webhook from .env and prints the answer.
# Usage: sh scripts/test-n8n.sh
cd "$(dirname "$0")/.." || exit 1

URL=$(grep '^N8N_WEBHOOK_URL=' .env | cut -d= -f2-)
SECRET=$(grep '^N8N_SECRET=' .env | cut -d= -f2- | tr -d '\r\n')

echo "Адрес: $URL"
echo "Секрет в .env: ${#SECRET} символов, начинается с $(printf %s "$SECRET" | cut -c1-4)…"
echo "---"

curl -sS -w '\nHTTP %{http_code}\n' -X POST "$URL" \
  -H "X-Montage-Secret: $SECRET" \
  -H 'Content-Type: application/json' \
  -d '{"language":"ru","target_seconds":60,"min_seconds":48,"max_seconds":75,"video_duration":120,
       "transcript":[{"start":0,"end":30,"text":"Привет, сегодня я расскажу удивительную историю."},
                     {"start":30,"end":65,"text":"Это было в горах, и мы чуть не потерялись."},
                     {"start":65,"end":120,"text":"В итоге нас спасла собака."}]}'
