#!/usr/bin/env bash
# Мини-приложение по HTTPS на сервере — без покупки домена.
# Адрес вида 51-250-12-34.sslip.io (бесплатный сервис: имя = IP сервера), сертификат Let's Encrypt — сам.
# Запуск из папки проекта: bash scripts/server_https.sh            (или со своим доменом: bash scripts/server_https.sh mayak.example.ru)
set -e
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "Нет файла .env — сначала запустите бота (scripts/server_import.sh)"; exit 1; }
DOMAIN="$1"
if [ -z "$DOMAIN" ]; then
  IP="$(curl -s -H Metadata-Flavor:Google http://169.254.169.254/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip || true)"
  [ -n "$IP" ] || IP="$(curl -s https://ifconfig.me)"
  [ -n "$IP" ] || { echo "Не удалось узнать IP сервера — запустите так: bash scripts/server_https.sh <IP>.sslip.io"; exit 1; }
  DOMAIN="$(echo "$IP" | tr '.' '-').sslip.io"
fi
set_env() {   # заменить строку KEY=… в .env или добавить её
  if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}
set_env MINIAPP_DOMAIN "$DOMAIN"
set_env COMPOSE_PROFILES https
echo "→ Открываю порты 80 и 443"
sudo ufw allow 80/tcp >/dev/null && sudo ufw allow 443/tcp >/dev/null
echo "→ Запускаю HTTPS (Caddy)"
docker compose up -d
echo "→ Жду сертификат (до минуты)…"
for i in $(seq 1 30); do
  if curl -fsS -o /dev/null "https://$DOMAIN/"; then
    echo
    echo "Готово! Мини-приложение: https://$DOMAIN/"
    echo "Эту ссылку отправьте организаторам для привязки к боту."
    exit 0
  fi
  sleep 3
done
echo "Сертификат пока не получен. Проверьте, что в Yandex Cloud у сервера открыты порты 80 и 443,"
echo "и посмотрите журнал: docker compose logs caddy"
exit 1
