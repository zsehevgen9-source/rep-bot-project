#!/usr/bin/env bash
# Обновление бота на сервере новой версией кода (архив mayachok-dlya-github.zip в домашней папке).
# Запуск: bash ~/mayachok/scripts/server_update.sh ~/mayachok-dlya-github.zip
set -e
ZIP="$(realpath "${1:-$HOME/mayachok-dlya-github.zip}")"
cd "$HOME"
unzip -o -q "$ZIP"        # .env, backups и данные в Docker не трогаются — их в архиве нет
cd mayachok
docker compose up -d --build
docker compose ps
echo "Обновлено! Логи: docker compose logs -f bot"
