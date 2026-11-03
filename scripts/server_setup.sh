#!/usr/bin/env bash
# Подготовка сервера Ubuntu 22.04/24.04: Docker, зеркало Docker Hub, файл подкачки, фаервол.
# Запуск один раз: bash scripts/server_setup.sh
set -e
echo "→ Ставлю Docker"
sudo apt-get update -q
sudo apt-get install -y -q docker.io docker-compose-v2 unzip
# Зеркало Docker Hub: из России Docker Hub иногда недоступен — образы python/postgres/redis возьмём с зеркала
sudo mkdir -p /etc/docker
echo '{"registry-mirrors": ["https://mirror.gcr.io"]}' | sudo tee /etc/docker/daemon.json >/dev/null
sudo systemctl enable --now docker
sudo systemctl restart docker
sudo usermod -aG docker "$USER"
# Файл подкачки 2 ГБ: на сервере с 2 ГБ памяти сборка образа не упадёт
if ! sudo swapon --show | grep -q /swapfile; then
  echo "→ Файл подкачки 2 ГБ"
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi
# Фаервол: снаружи открыт только SSH (бот сам ходит в MAX, входящие подключения ему не нужны)
echo "→ Фаервол: только SSH"
sudo ufw allow OpenSSH >/dev/null && sudo ufw --force enable >/dev/null
echo "Готово! Выйдите (exit) и зайдите на сервер снова — чтобы команда docker работала без sudo."
