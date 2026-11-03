"""Перенос данных Redis (классы учителей, маяк, альбомы, карточки, настройки) на другой компьютер или сервер.

    python -m app.tools.redis_transfer export /tmp/redis.json   — сохранить все ключи бота в файл
    python -m app.tools.redis_transfer import /tmp/redis.json   — загрузить их обратно (с теми же сроками жизни)

Бот хранит в Redis только строки (set_state), поэтому переносим строки вместе с оставшимся временем жизни (TTL).
"""
import asyncio
import json
import sys

import redis.asyncio as redis

from app.core.config import settings


async def export(path: str) -> None:
    r = redis.from_url(str(settings.REDIS_URL), decode_responses=True)
    data, skipped = {}, 0
    async for key in r.scan_iter(count=500):
        if await r.type(key) != "string":
            skipped += 1
            continue
        value = await r.get(key)
        if value is None:
            continue
        ttl = await r.ttl(key)
        data[key] = [value, ttl if ttl and ttl > 0 else None]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    await r.aclose()
    print(f"Сохранено ключей: {len(data)}" + (f" (пропущено не-строк: {skipped})" if skipped else "") + f" → {path}")


async def load(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    r = redis.from_url(str(settings.REDIS_URL), decode_responses=True)
    for key, (value, ttl) in data.items():
        await r.set(key, value, ex=ttl or None)
    await r.aclose()
    print(f"Загружено ключей: {len(data)}")


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in ("export", "import"):
        print(__doc__)
        sys.exit(1)
    asyncio.run(export(sys.argv[2]) if sys.argv[1] == "export" else load(sys.argv[2]))


if __name__ == "__main__":
    main()
