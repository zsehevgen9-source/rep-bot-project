#!/usr/bin/env bash
# Обновление бота на сервере.
#   • Если папка mayachok связана с GitHub (см. docs/SERVER.md, «Как обновлять бота») — берёт новую версию оттуда:
#         bash ~/mayachok/scripts/server_update.sh
#   • Иначе — из архива mayachok-dlya-github.zip в домашней папке:
#         bash ~/mayachok/scripts/server_update.sh ~/mayachok-dlya-github.zip
# .env, база, Redis и копии базы не затрагиваются.
set -e
cd "$HOME/mayachok"
if [ -d .git ]; then
  echo "→ Беру новую версию с GitHub"
  git pull --ff-only
else
  ZIP="$(realpath "${1:-$HOME/mayachok-dlya-github.zip}")"
  echo "→ Распаковываю $ZIP"
  cd "$HOME" && unzip -o -q "$ZIP" && cd mayachok
fi
echo "→ Пересобираю и перезапускаю бота"
docker compose up -d --build
docker compose ps
echo "Обновлено! Логи: docker compose logs -f bot"
