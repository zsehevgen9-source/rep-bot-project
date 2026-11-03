"""Подготовка голосовых сообщений к распознаванию в Yandex SpeechKit.

SpeechKit (короткие аудио) принимает OGG/Opus до 30 секунд и до 1 МБ. Голосовые MAX обычно уже в этом формате;
если нет — перекодируем через ffmpeg (он установлен в Docker-образе).
"""
import asyncio
import logging
import shutil
from typing import Optional

logger = logging.getLogger(__name__)

MAX_STT_BYTES = 1_000_000
MAX_STT_SECONDS = 30


def is_ogg_opus(data: bytes) -> bool:
    return data[:4] == b"OggS" and b"OpusHead" in data[:256]


async def to_ogg_opus(data: bytes) -> Optional[bytes]:
    """Возвращает аудио в формате OGG/Opus (не длиннее 30 секунд) или None, если подготовить не удалось."""
    if is_ogg_opus(data) and len(data) <= MAX_STT_BYTES:
        return data
    if shutil.which("ffmpeg") is None:
        logger.error("ffmpeg не установлен — не могу перекодировать голосовое (формат %r)", data[:8])
        return None
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
            "-t", str(MAX_STT_SECONDS), "-ac", "1", "-ar", "48000", "-c:a", "libopus", "-b:a", "24k",
            "-f", "ogg", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(process.communicate(data), timeout=30)
    except Exception as e:
        logger.error("Ошибка перекодирования голосового: %s", e)
        return None
    if process.returncode != 0 or not out:
        logger.error("ffmpeg не смог перекодировать голосовое: %s", err.decode(errors="replace")[:300])
        return None
    return out if len(out) <= MAX_STT_BYTES else None


async def to_mp3(data: bytes) -> Optional[bytes]:
    """Голос Маячка для мини-приложения: MP3 играет в любом браузере (OGG/Opus — не во всех, например не в старых iPhone)."""
    if not data or shutil.which("ffmpeg") is None:
        return None
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
            "-ac", "1", "-ar", "24000", "-c:a", "libmp3lame", "-b:a", "48k", "-f", "mp3", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(process.communicate(data), timeout=30)
    except Exception as e:
        logger.error("Ошибка перекодирования в MP3: %s", e)
        return None
    if process.returncode != 0 or not out:
        logger.error("ffmpeg не смог сделать MP3: %s", err.decode(errors="replace")[:300])
        return None
    return out
