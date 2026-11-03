# Сохраняет данные бота с домашнего компьютера для переноса на сервер.
# Запуск (бот должен быть запущен): в папке проекта в PowerShell
#     powershell -ExecutionPolicy Bypass -File scripts\export_data.ps1
# Результат — папка «perenos»: dump.sql (база), redis.json (классы, маяк, альбомы, карточки), .env (настройки).
# В папке персональные данные детей и токены — никому её не отправляйте, только на свой сервер.

function Step($text, $block) {
    Write-Host "→ $text"
    & $block
    if ($LASTEXITCODE -ne 0) { Write-Host "Ошибка на шаге: $text" -ForegroundColor Red; exit 1 }
}

New-Item -ItemType Directory -Force perenos | Out-Null
Step "Копия базы PostgreSQL" { docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists -f /tmp/dump.sql' }
Step "Скачиваю копию базы" { docker compose cp db:/tmp/dump.sql perenos/dump.sql }
Step "Копия Redis" { docker compose exec -T bot python -m app.tools.redis_transfer export /tmp/redis.json }
Step "Скачиваю копию Redis" { docker compose cp bot:/tmp/redis.json perenos/redis.json }
Copy-Item .env perenos/.env -Force
Write-Host "Готово! Всё для переноса лежит в папке perenos" -ForegroundColor Green
