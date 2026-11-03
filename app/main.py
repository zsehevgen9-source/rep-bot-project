import asyncio
import json
import logging
import sys
from typing import Dict, Optional, Set

from app.core.database import AsyncSessionLocal, engine
from app.handlers.normalize import normalize_update, update_skeleton
from app.handlers.router import route_update
from app.handlers.user import UserHandler
from app.schemas.payloads import NormalizedMessage
from app.services.notifier import run_notifier
from app.services.ai_client import ai_client
from app.services.api_client import max_api
from app.services.redis_client import redis_client
from app.services.task_bank import task_bank
from app.core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# Сколько обновлений обрабатывается одновременно
MAX_CONCURRENT_UPDATES = 20


class UpdateDispatcher:
    """Обрабатывает обновления параллельно, но сообщения одного пользователя — строго по очереди.

    Озвучка ответов занимает десятки секунд, и без этого один пользователь
    блокировал бы бота для всех остальных.
    """

    def __init__(self) -> None:
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_UPDATES)
        self._user_locks: Dict[int, asyncio.Lock] = {}
        self._tasks: Set[asyncio.Task] = set()

    def submit(self, update: dict) -> None:
        task = asyncio.create_task(self._handle(update))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle(self, update: dict) -> None:
        try:
            normalized = normalize_update(update)
            if normalized is None:
                if update.get("update_type") == "message_created":
                    body = (update.get("message") or {}).get("body") or {}
                    logger.warning(
                        "Сообщение пропущено: текст=%s, вложения=%s. Схема события: %s",
                        bool(body.get("text")), [a.get("type") for a in body.get("attachments") or []],
                        json.dumps(update_skeleton(update), ensure_ascii=False),
                    )
                else:
                    logger.info("Пропускаю событие: %s", update.get("update_type"))
                return

            lock = self._user_locks.setdefault(normalized.user_id, asyncio.Lock())
            async with self._semaphore, lock:
                async with AsyncSessionLocal() as session:
                    await route_update(normalized, max_api, session, redis_client)
        except Exception:
            logger.exception("Ошибка при обработке обновления")

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


async def start_polling() -> None:
    """Основной цикл long polling."""
    marker: Optional[int] = None
    dispatcher = UpdateDispatcher()
    logger.info("Запускаю polling...")

    try:
        while True:
            try:
                data = await max_api.get_updates(marker=marker, timeout=30)
                marker = data.get("marker", marker)

                for update in data.get("updates") or []:
                    logger.debug("RAW UPDATE: %s", update)
                    dispatcher.submit(update)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Ошибка в цикле polling: %s", e)
                await asyncio.sleep(5)
    except asyncio.CancelledError:
        logger.info("Polling остановлен.")
        raise
    finally:
        await dispatcher.drain()


def run_migrations() -> None:
    """Применяет миграции Alembic (alembic upgrade head). Запускается в отдельном потоке."""
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    config.attributes["skip_logging_config"] = True
    command.upgrade(config, "head")
    logger.info("Таблицы базы данных в актуальном состоянии.")


async def start_miniapp():
    """Веб-сервер мини-приложения (страница + API). Если не запустился — бот всё равно работает."""
    if not settings.WEBAPP_ENABLED:
        return None
    try:
        from app.webapp.api import MiniAppAPI
        from app.webapp.server import start_webapp

        username = settings.MAX_BOT_USERNAME.strip().lstrip("@")
        if not username:
            try:
                username = (await max_api.get_me()).get("username") or ""
            except Exception as e:
                logger.warning("Не удалось узнать имя бота (GET /me): %s", e)
        if username:
            await redis_client.set_state("bot_username", username)
            logger.info("Ссылка на бота: https://max.ru/%s", username)
        api = MiniAppAPI(lambda session: UserHandler(max_api, session, redis_client), AsyncSessionLocal, username)
        return await start_webapp(api, settings.MAX_BOT_TOKEN.get_secret_value(), settings.WEBAPP_PORT)
    except Exception:
        logger.exception("Мини-приложение не запустилось — бот работает без него")
        return None


async def main() -> None:
    logger.info("YandexGPT: используется модель %s", ai_client.model)
    task_bank.load()
    await redis_client.connect()

    try:
        async with engine.begin():
            logger.info("Подключение к базе данных установлено.")
        # Создаём/обновляем таблицы (профили учеников и т. д.) — вручную ничего запускать не нужно
        await asyncio.to_thread(run_migrations)
    except Exception as e:
        logger.error("Не удалось подготовить базу данных: %s. Профили будут храниться только в Redis.", e)

    webapp = await start_miniapp()
    notifier = asyncio.create_task(run_notifier(
        AsyncSessionLocal,
        lambda session: UserHandler(max_api, session, redis_client),
        lambda p: NormalizedMessage(chat_id=p.get("chat_id"), user_id=p["user_id"], message_id="", text=None, payload=None),
    ))
    try:
        await start_polling()
    finally:
        notifier.cancel()
        if webapp is not None:
            await webapp.cleanup()
        await max_api.close()
        await redis_client.close()
        await engine.dispose()
        logger.info("Сервисы остановлены.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
