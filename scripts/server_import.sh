#!/usr/bin/env bash
# Первый запуск на сервере с данными с домашнего компьютера (папка perenos из scripts/export_data.ps1).
# Запуск из папки проекта: bash scripts/server_import.sh ~/perenos
set -e
DIR="$(realpath "${1:-$HOME/perenos}")"
cd "$(dirname "$0")/.."
for f in dump.sql redis.json; do
  [ -f "$DIR/$f" ] || { echo "Нет файла $DIR/$f — сначала скопируйте папку perenos на сервер"; exit 1; }
done
[ -f .env ] || cp "$DIR/.env" .env
chmod 600 .env
echo "→ Запускаю базу и Redis"
docker compose up -d --wait db redis
echo "→ Загружаю базу"
docker compose cp "$DIR/dump.sql" db:/tmp/dump.sql
docker compose exec -T db sh -c 'psql -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /tmp/dump.sql' >/dev/null
echo "→ Собираю бота (несколько минут)"
docker compose build bot
echo "→ Загружаю данные Redis"
docker compose run --rm --no-deps -v "$DIR":/perenos:ro bot python -m app.tools.redis_transfer import /perenos/redis.json
echo "→ Запускаю всё"
docker compose up -d
docker compose ps
echo "Готово! Бот работает на сервере. Логи: docker compose logs -f bot"
