import logging
import time
from typing import Dict, Optional, Tuple

import redis.asyncio as redis

from app.core.config import settings

logger = logging.getLogger(__name__)

# Если Redis недоступен, повторная попытка подключения не чаще, чем раз в столько секунд
RECONNECT_COOLDOWN = 30.0


class RedisClient:
    """Redis-клиент с режимом «fail open».

    Если Redis недоступен, состояние временно хранится в памяти процесса (с учётом TTL),
    а к Redis бот пробует вернуться автоматически.
    """

    def __init__(self, redis_url: str | None = None):
        self.redis_url = str(redis_url or settings.REDIS_URL)
        self._redis: Optional[redis.Redis] = None
        # key -> (value, expires_at | None)
        self._local_storage: Dict[str, Tuple[str, Optional[float]]] = {}
        self._is_connected = False
        self._last_attempt = 0.0

    @staticmethod
    def _key(key: int | str) -> str:
        return f"user_state:{key}"

    async def connect(self) -> None:
        """Подключение к Redis. При неудаче бот продолжает работать на локальном хранилище."""
        self._last_attempt = time.monotonic()
        try:
            self._redis = redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=2.0,
                socket_connect_timeout=2.0,
            )
            await self._redis.ping()
            self._is_connected = True
            logger.info("Подключение к Redis установлено.")
        except Exception as e:
            self._is_connected = False
            logger.error("Не удалось подключиться к Redis: %s. Использую хранилище в памяти.", e)

    async def _ensure_connection(self) -> None:
        if self._is_connected:
            return
        if time.monotonic() - self._last_attempt >= RECONNECT_COOLDOWN:
            await self.connect()

    def _local_get(self, key: str) -> Optional[str]:
        item = self._local_storage.get(key)
        if item is None:
            return None
        value, expires_at = item
        if expires_at is not None and expires_at <= time.monotonic():
            del self._local_storage[key]
            return None
        return value

    async def set_state(self, key: int | str, state: str, ex: Optional[int] = None) -> bool:
        """Сохранить значение. key — user_id или любая строка-ключ, ex — TTL в секундах."""
        await self._ensure_connection()
        full_key = self._key(key)

        if self._is_connected and self._redis:
            try:
                await self._redis.set(full_key, state, ex=ex)
                return True
            except Exception as e:
                logger.error("Redis set_state: %s. Перехожу на хранилище в памяти.", e)
                self._is_connected = False

        expires_at = time.monotonic() + ex if ex else None
        self._local_storage[full_key] = (state, expires_at)
        return True

    async def get_state(self, key: int | str) -> Optional[str]:
        await self._ensure_connection()
        full_key = self._key(key)

        if self._is_connected and self._redis:
            try:
                return await self._redis.get(full_key)
            except Exception as e:
                logger.error("Redis get_state: %s. Перехожу на хранилище в памяти.", e)
                self._is_connected = False

        return self._local_get(full_key)

    async def delete_state(self, key: int | str) -> bool:
        await self._ensure_connection()
        full_key = self._key(key)

        success = True
        if self._is_connected and self._redis:
            try:
                await self._redis.delete(full_key)
            except Exception as e:
                logger.error("Redis delete_state: %s", e)
                self._is_connected = False
                success = False

        self._local_storage.pop(full_key, None)
        return success

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._is_connected = False


# Глобальный экземпляр
redis_client = RedisClient()
