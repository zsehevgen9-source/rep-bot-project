import asyncio
import html
import json
import logging
from typing import Any, Dict, Optional

import aiohttp

from app.core.config import settings
from app.services.formatting import MAX_TEXT, to_html

logger = logging.getLogger(__name__)


class MaxApiError(Exception):
    """Ошибка ответа MAX API (HTTP-статус != 2xx). Текст ошибки содержит тело ответа."""

    def __init__(self, status: int, data: Any):
        self.status = status
        self.data = data
        super().__init__(f"MAX API error {status}: {data}")


class MaxApiClient:
    def __init__(
        self,
        token: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        # Токен берём ТОЛЬКО из настроек (.env), в коде его хранить нельзя
        self.token = token or settings.MAX_BOT_TOKEN.get_secret_value()
        self.base_url = str(base_url or settings.MAX_API_URL).rstrip("/")
        # None — стандартная проверка сертификата, False — проверка отключена (MAX_SSL_VERIFY=false)
        self._ssl: Optional[bool] = None if settings.MAX_SSL_VERIFY else False
        if self._ssl is False:
            logger.warning("Проверка TLS-сертификата MAX отключена (MAX_SSL_VERIFY=false). Токен передаётся без проверки сервера!")
        self._session: Optional[aiohttp.ClientSession] = None
        self.pretty = getattr(settings, "PRETTY_MESSAGES", True)   # жирные заголовки и т. п. (HTML-разметка MAX)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # Long polling держит соединение до 30 сек, поэтому общий таймаут с запасом
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
        return self._session

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        session = await self._get_session()
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = {"Authorization": self.token, "Content-Type": "application/json"}

        async with session.request(
            method, url, params=params, json=json_body, headers=headers, ssl=self._ssl
        ) as response:
            raw = await response.text()
            try:
                data = json.loads(raw) if raw else {}
            except ValueError:
                data = {"raw": raw}

            if not response.ok:
                raise MaxApiError(response.status, data)
            return data

    async def get_updates(self, marker: Optional[int] = None, timeout: int = 30, limit: int = 100) -> Dict[str, Any]:
        """Long polling. Возвращает {"updates": [...], "marker": int}.

        marker из прошлого ответа нужно передавать в следующий запрос, иначе
        одни и те же события могут приходить повторно.
        """
        params: Dict[str, Any] = {"timeout": timeout, "limit": limit}
        if marker is not None:
            params["marker"] = marker
        return await self._request("GET", "updates", params=params)

    async def get_me(self) -> Dict[str, Any]:
        """Сведения о боте: user_id, name, username (нужно для ссылки https://max.ru/<username>)."""
        return await self._request("GET", "me")

    async def send_message(
        self,
        chat_id: Optional[int] = None,
        user_id: Optional[int] = None,
        text: str = "",
        keyboard: Optional[Dict[str, Any]] = None,
        attachments: Optional[list] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if chat_id:
            params["chat_id"] = chat_id
        elif user_id:
            params["user_id"] = user_id

        payload: Dict[str, Any] = {"text": text}
        if text and self.pretty:
            pretty = to_html(text)
            if pretty != html.escape(text, quote=False) and len(pretty) <= MAX_TEXT:
                payload = {"text": pretty, "format": "html"}
        all_attachments = list(attachments or [])
        if keyboard:
            all_attachments.append({"type": "inline_keyboard", "payload": keyboard})
        if all_attachments:
            payload["attachments"] = all_attachments

        if not attachments:
            return await self._post_message(params, payload, text)

        # Загруженной картинке нужно время на обработку на стороне MAX
        for attempt in range(6):
            try:
                return await self._post_message(params, payload, text)
            except MaxApiError as e:
                if "attachment.not.ready" in str(e) and attempt < 5:
                    await asyncio.sleep(1 + attempt * 0.5)
                    continue
                raise
        return {}

    async def _post_message(self, params: Dict[str, Any], payload: Dict[str, Any], text: str) -> Dict[str, Any]:
        """Отправка с разметкой; если MAX разметку не принял — то же сообщение обычным текстом."""
        try:
            return await self._request("POST", "messages", params=params, json_body=payload)
        except MaxApiError as e:
            if payload.get("format") and e.status == 400 and "attachment.not.ready" not in str(e):
                logger.warning("MAX не принял HTML-разметку (%s) — отправляю обычным текстом", e)
                plain = {k: v for k, v in payload.items() if k != "format"}
                plain["text"] = text
                return await self._request("POST", "messages", params=params, json_body=plain)
            raise

    async def send_card(self, user_id: int, png: bytes, text: str = "", keyboard: Optional[Dict[str, Any]] = None,
                        filename: str = "card.png") -> bool:
        """Картинка-карточка (PNG) отдельным сообщением. False — если загрузить не удалось."""
        token = await self.upload_image(png, filename=filename)
        if not token:
            return False
        await self.send_message(user_id=user_id, text=text, keyboard=keyboard,
                                attachments=[{"type": "image", "payload": {"token": token}}])
        return True

    async def upload_image(self, image_bytes: bytes, filename: str = "image.png") -> Optional[str]:
        """Загружает картинку в MAX и возвращает токен для вложения (или None при ошибке).

        Порядок по документации MAX: POST /uploads?type=image -> url; файл отправляется на url;
        в ответе {"photos": {"<id>": {"token": "..."}}}.
        """
        try:
            upload_info = await self._request("POST", "uploads", params={"type": "image"})
            upload_url = upload_info.get("url")
            if not upload_url:
                logger.error("MAX не выдал URL для загрузки картинки: %s", upload_info)
                return None

            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
            content_type = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                            "gif": "image/gif", "webp": "image/webp"}.get(ext, "application/octet-stream")
            form = aiohttp.FormData()
            form.add_field("data", image_bytes, filename=filename, content_type=content_type)

            session = await self._get_session()
            async with session.post(
                upload_url, data=form, headers={"Authorization": self.token}, ssl=self._ssl
            ) as response:
                raw = await response.text()
                if response.status != 200:
                    logger.error("Ошибка загрузки картинки в MAX: %s - %s", response.status, raw[:300])
                    return None

            data = json.loads(raw) if raw else {}
            if data.get("token"):
                return data["token"]
            for photo in (data.get("photos") or {}).values():
                if isinstance(photo, dict) and photo.get("token"):
                    return photo["token"]
            logger.error("В ответе MAX нет токена картинки: %s", raw[:300])
            return None
        except Exception as e:
            logger.error("Ошибка в upload_image: %s", e)
            return None

    async def send_file(self, user_id: int, data: bytes, filename: str, text: str = "",
                        keyboard: Optional[Dict[str, Any]] = None) -> bool:
        """Файл (например, таблица Excel) отдельным сообщением. False — если загрузить не удалось."""
        try:
            upload_info = await self._request("POST", "uploads", params={"type": "file"})
            upload_url = upload_info.get("url")
            if not upload_url:
                logger.error("MAX не выдал URL для загрузки файла: %s", upload_info)
                return False
            form = aiohttp.FormData()
            form.add_field("data", data, filename=filename, content_type="application/octet-stream")
            session = await self._get_session()
            async with session.post(upload_url, data=form, headers={"Authorization": self.token}, ssl=self._ssl) as response:
                raw = await response.text()
                if response.status != 200:
                    logger.error("Ошибка загрузки файла в MAX: %s - %s", response.status, raw[:300])
                    return False
            answer = json.loads(raw) if raw and raw.strip().startswith("{") else {}
            token = answer.get("token") or upload_info.get("token")
            if not token:
                logger.error("В ответе MAX нет токена файла: %s", raw[:300])
                return False
            await self.send_message(user_id=user_id, text=text, keyboard=keyboard,
                                    attachments=[{"type": "file", "payload": {"token": token}}])
            return True
        except Exception as e:
            logger.error("Ошибка в send_file: %s", e)
            return False

    async def send_action(self, chat_id: int, action: str = "typing_on") -> None:
        """Действие в чате: «печатает…» (typing_on). Ошибки не важны — это только подсказка для пользователя."""
        try:
            await self._request("POST", f"chats/{chat_id}/actions", json_body={"action": action})
        except Exception as e:
            logger.debug("Не удалось показать «печатает…»: %s", e)

    async def send_voice(self, chat_id: int, audio_bytes: bytes) -> Dict[str, Any]:
        try:
            logger.info("Запрашиваю URL и токен для загрузки аудио в MAX...")
            upload_info = await self._request("POST", "uploads", params={"type": "audio"})

            upload_url = upload_info.get("url")
            audio_token = upload_info.get("token")

            if not upload_url or not audio_token:
                logger.error("MAX не выдал URL или токен для загрузки. Ответ: %s", upload_info)
                return {}

            if not upload_url.startswith("http"):
                upload_url = f"{self.base_url}{upload_url}"

            session = await self._get_session()
            form = aiohttp.FormData()
            form.add_field("data", audio_bytes, filename="voice.ogg", content_type="audio/ogg")

            async with session.post(upload_url, data=form, ssl=self._ssl) as response:
                if response.status != 200:
                    error = await response.text()
                    logger.error("Ошибка загрузки в MAX: %s - %s", response.status, error)
                    return {}

            # Файлу нужно время на обработку на стороне MAX
            await asyncio.sleep(1.5)

            params = {"chat_id": chat_id}
            payload = {"attachments": [{"type": "audio", "payload": {"token": audio_token}}]}

            for attempt in range(6):
                try:
                    return await self._request("POST", "messages", params=params, json_body=payload)
                except MaxApiError as e:
                    if "attachment.not.ready" in str(e):
                        wait_time = 1 + attempt * 0.5
                        logger.warning("Аудио ещё не готово, жду %.1f сек (попытка %d)", wait_time, attempt + 1)
                        await asyncio.sleep(wait_time)
                    else:
                        raise

            logger.error("Не удалось отправить аудио после 6 попыток.")
            return {}

        except Exception as e:
            logger.error("Ошибка в send_voice: %s", e)
            return {}

    async def download_file(self, url: str, max_bytes: int = 10 * 1024 * 1024) -> Optional[bytes]:
        """Скачивает файл (например, голосовое сообщение ученика) по ссылке из вложения."""
        session = await self._get_session()
        # Ссылки на файлы MAX обычно открываются без токена; если нет — пробуем с ним
        for headers in ({}, {"Authorization": self.token}):
            try:
                async with session.get(url, headers=headers, ssl=self._ssl) as response:
                    if response.status in (401, 403):
                        continue
                    if response.status != 200:
                        logger.error("Не удалось скачать файл: HTTP %s", response.status)
                        return None
                    data = await self._read_all(response, max_bytes)
                    if data is None:
                        logger.error("Файл слишком большой (больше %d байт)", max_bytes)
                        return None
                    return data
            except Exception as e:
                logger.error("Ошибка скачивания файла: %s", e)
                return None
        logger.error("Нет доступа к файлу (401/403)")
        return None

    @staticmethod
    async def _read_all(response, max_bytes: int) -> Optional[bytes]:
        """Весь ответ целиком. (content.read(n) отдаёт только то, что уже пришло, — файл получался обрезанным.)
        None — если файл больше max_bytes."""
        chunks, size = [], 0
        async for chunk in response.content.iter_chunked(64 * 1024):
            size += len(chunk)
            if size > max_bytes:
                return None
            chunks.append(chunk)
        return b"".join(chunks)

    async def download_image(self, url: str, max_bytes: int = 20 * 1024 * 1024) -> Optional[bytes]:
        """Скачивает фото из сообщения. Ссылка может отдавать маленькое превью, поэтому пробуем несколько вариантов
        запроса (обычный, «как браузер», с токеном бота) и берём самый большой ответ."""
        from urllib.parse import urlsplit, parse_qsl
        parts = urlsplit(url)
        logger.info("Фото: хост %s, параметры ссылки: %s", parts.netloc, [k for k, _ in parse_qsl(parts.query)])
        browser = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
                   "Accept": "image/jpeg,image/png,image/*;q=0.8,*/*;q=0.5"}
        session = await self._get_session()
        best: Optional[bytes] = None
        for name, headers in (("обычный", {}), ("как браузер", browser),
                              ("с токеном", {**browser, "Authorization": self.token})):
            try:
                async with session.get(url, headers=headers, ssl=self._ssl) as response:
                    data = (await self._read_all(response, max_bytes) or b"") if response.status == 200 else b""
                    logger.info("Фото, запрос «%s»: HTTP %s, %d байт, тип %s", name, response.status, len(data),
                                response.headers.get("Content-Type"))
            except Exception as e:
                logger.error("Фото, запрос «%s»: ошибка %s", name, e)
                continue
            if data and len(data) <= max_bytes and (best is None or len(data) > len(best)):
                best = data
            if best and len(best) > 30_000:
                break   # нормальное фото, а не превью
        return best

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


# Экземпляр клиента для использования в других модулях
max_api = MaxApiClient()
