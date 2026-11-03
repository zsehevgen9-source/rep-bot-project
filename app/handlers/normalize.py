import logging
from typing import Any, Dict, Optional

from app.schemas.payloads import NormalizedMessage


logger = logging.getLogger(__name__)


DOCUMENT_EXTENSIONS = (".docx", ".xlsx", ".pdf", ".txt", ".csv")

# Значения этих полей безопасно показывать в логе как есть
_SAFE_KEYS = {"update_type", "type", "chat_type", "is_bot", "status", "filename", "duration"}


def update_skeleton(obj, key=None, depth=0):
    """Схема события для лога: названия полей и типы значений, без самих данных (имён, ссылок, токенов)."""
    if depth > 6:
        return "…"
    if isinstance(obj, dict):
        return {k: update_skeleton(v, k, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [update_skeleton(obj[0], key, depth + 1), f"всего {len(obj)}"] if obj else []
    if key in _SAFE_KEYS:
        return obj
    if isinstance(obj, str):
        return f"строка({len(obj)})"
    if isinstance(obj, bool) or obj is None:
        return obj
    return type(obj).__name__


def normalize_update(update: Dict[str, Any]) -> Optional[NormalizedMessage]:
    """Приводит сырое событие MAX к NormalizedMessage.

    Тип события лежит в корне JSON в поле update_type (см. docs/MAX_API_Real_Payloads_2026.md).
    Возвращает None для событий, которые бот не обрабатывает.
    """
    update_type = update.get("update_type")

    if update_type == "message_created":
        message = update.get("message") or {}
        recipient = message.get("recipient") or {}
        sender = message.get("sender") or {}
        body = message.get("body") or {}

        chat_id = recipient.get("chat_id")
        user_id = sender.get("user_id")

        # Без ID отправителя обработать сообщение нельзя
        if not user_id:
            return None

        # Свои же сообщения и сообщения других ботов игнорируем
        if sender.get("is_bot"):
            return None

        audio_url = None
        file_url = file_name = file_kind = None
        attachments = body.get("attachments") or []
        for att in attachments:
            payload = att.get("payload") or {}
            filename = str(payload.get("filename") or att.get("filename") or "")
            url = payload.get("url") or payload.get("link") or att.get("url")
            low = filename.lower()
            if att.get("type") in ("image", "photo") and url and not file_url:
                file_url, file_name, file_kind = url, filename or "photo.jpg", "image"
            elif att.get("type") == "file" and url and not file_url:
                if low.endswith((".jpg", ".jpeg", ".png", ".heic", ".webp")):
                    file_url, file_name, file_kind = url, filename, "image"
                elif low.endswith(DOCUMENT_EXTENSIONS):
                    file_url, file_name, file_kind = url, filename, "document"
        for att in attachments:
            payload = att.get("payload") or {}
            filename = str(payload.get("filename") or att.get("filename") or "").lower()
            is_audio = att.get("type") in ("audio", "voice") or (
                att.get("type") == "file" and filename.endswith((".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav"))
            )
            if is_audio:
                audio_url = payload.get("url") or payload.get("link") or att.get("url")
                break
        if attachments:
            # Структура вложений без ссылок и токенов — чтобы было видно, что прислал MAX
            logger.info(
                "Вложения: %s, ссылка на звук: %s, фото/файл: %s",
                [{"type": a.get("type"), "payload": sorted((a.get("payload") or {}).keys())} for a in attachments],
                "есть" if audio_url else "нет", file_kind or "нет",
            )

        return NormalizedMessage(
            chat_id=chat_id,
            user_id=user_id,
            message_id=body.get("mid") or "",
            text=body.get("text"),
            payload=None,
            audio_url=audio_url,
            file_url=file_url,
            file_name=file_name,
            file_kind=file_kind,
        )

    if update_type == "message_callback":
        callback = update.get("callback") or {}
        message = update.get("message") or {}
        recipient = message.get("recipient") or {}
        body = message.get("body") or {}

        # Нажавший кнопку пользователь — callback.user. recipient — запасной вариант
        user_id = (callback.get("user") or {}).get("user_id") or recipient.get("user_id")
        payload = callback.get("payload")

        if not user_id or payload is None:
            return None

        return NormalizedMessage(
            chat_id=recipient.get("chat_id"),
            user_id=user_id,
            message_id=body.get("mid") or "",
            text=None,
            payload=payload,
        )

    if update_type == "bot_started":
        # Пользователь нажал «Начать» при первом открытии бота — считаем это командой /start
        user_id = (update.get("user") or {}).get("user_id")
        if not user_id:
            return None
        return NormalizedMessage(
            chat_id=update.get("chat_id"),
            user_id=user_id,
            message_id="",
            text="/start",
            payload=None,
        )

    return None
