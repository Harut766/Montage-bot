#!/bin/sh
# Pulls the latest code, rebuilds and restarts the bot, then frees the space taken by old versions.
# Usage: sh scripts/update.sh   (run when the bot is idle: a restart interrupts the current video)
cd "$(dirname "$0")/.." || exit 1

git pull || exit 1
docker compose up -d --build || exit 1

# Old image versions left by the rebuild and the build cache; running containers are not affected.
docker image prune -f
docker builder prune -f
docker system df
