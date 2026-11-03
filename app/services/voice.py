"""Голос бота: подготовка текста к озвучке, чтобы он звучал как живая речь, и склейка аудио.

Здесь нет обращений к SpeechKit — только обработка текста и звука, поэтому всё легко проверить тестами.
"""
import asyncio
import base64
import json
import logging
import re
import shutil
from typing import List, Optional

logger = logging.getLogger(__name__)

# Голоса Yandex SpeechKit, которые можно выбрать командой /voice (модель, голос, роль, как назвать)
# Маячок — мальчик, поэтому все голоса мужские. Роль good — бодрый, весёлый тон.
VOICE_CHOICES = [
    ("", "alexander", "good", "Александр — бодрый, весёлый"),
    ("", "anton", "good", "Антон — добрый, весёлый"),
    ("", "zahar", "good", "Захар — дружелюбный"),
    ("", "ermil", "good", "Ермил — радостный"),
    ("", "kirill", "good", "Кирилл — уверенный, бодрый"),
    ("livetts", "denis", "", "Денис — живая речь (LiveTTS)"),
    ("livetts", "vasily", "", "Василий — живая речь (LiveTTS)"),
    ("", "filipp", "", "Филипп — спокойный"),
]
LIVETTS_VOICES = {"irina", "sofia", "vera", "denis", "vasily", "sergey"}
MASCOT_VOICE = ("", "alexander", "good")      # голос Маячка по умолчанию
# Женские голоса из прежних настроек: Маячку они не подходят — вместо них говорит MASCOT_VOICE
FEMALE_VOICES = {"irina", "sofia", "vera", "dasha", "masha", "marina", "alena", "jane", "omazh", "julia", "lera",
                 "oksana", "saule_ru", "zamira_ru", "zhanar_ru", "yulduz_ru"}
FALLBACK_VOICE = ("", "ermil", "good")        # если основной голос недоступен

TTS_CHUNK = 240          # столько символов SpeechKit озвучивает за один запрос (без «небезопасного» режима)
MAX_SPEECH_CHARS = 2000  # длиннее не озвучиваем: это уже больше двух минут речи
PAUSE_SECONDS = 0.3      # пауза между кусками внутри одного голосового
SAMPLE_RATE = 22050

_SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
_ABBREVIATIONS = [
    (r"\bт\.\s?е\.", "то есть"), (r"\bт\.\s?д\.", "так далее"), (r"\bт\.\s?п\.", "тому подобное"),
    (r"\bи\s+др\.", "и другие"), (r"\bнапр\.", "например"), (r"\bсм\.", "смотри"), (r"\bстр\.", "страница"),
    (r"\bвв\.", "веков"), (r"\bв\.\s?в\.", "века"), (r"\bдо\s+н\.\s?э\.", "до нашей эры"), (r"\bн\.\s?э\.", "нашей эры"),
]


def speakable(text: str) -> str:
    """Математические знаки — словами, чтобы голос их прочитал, а не пропустил (минус, умножить, корень…)."""
    text = text.translate(_SUBSCRIPTS)
    text = re.sub(r"(\d)\s*/\s*(\d)", r"\1 дробь \2", text)          # 3/7 → 3 дробь 7
    text = re.sub(r"(\d)\s+:\s+(\d)", r"\1 разделить на \2", text)    # 96 : 3
    text = re.sub(r"(\w)\s*\^\s*2\b", r"\1 в квадрате", text)        # x^2
    text = re.sub(r"(\w)\s*\^\s*3\b", r"\1 в кубе", text)
    text = re.sub(r"(\w)\s*\^\s*(\w+)", r"\1 в степени \2", text)
    replacements = [
        ("°C", " градусов"), ("°", " градусов"), ("²", " в квадрате"), ("³", " в кубе"),
        ("√", " корень из "), ("×", " умножить на "), ("·", " умножить на "), ("≈", " примерно "),
        ("−", " минус "), ("→", ", "), ("≥", " больше или равно "), ("≤", " меньше или равно "), ("≠", " не равно "),
        (">", " больше "), ("<", " меньше "), ("π", " пи "), ("+", " плюс "), ("=", " равно "),
        ("%", " процентов"), ("№", " номер "),
    ]
    for sign, word in replacements:
        text = text.replace(sign, word)
    return re.sub(r"[ \t]{2,}", " ", text)


def prepare_for_speech(text: str) -> str:
    """Текст для озвучки: без эмодзи и разметки, с паузами там, где человек сделал бы паузу."""
    lines = []
    for line in str(text or "").splitlines():
        line = line.strip()
        line = re.sub(r"^([•\-–—*#>]+|\d+️⃣)\s*", "", line)                   # маркеры списков
        line = re.sub(r"^(\d+)[.)]\s+", r"\1. ", line)                      # «1) …» → «1. …»
        if not line:
            continue
        lines.append(line)
    text = " ".join(_with_pause(line) for line in lines)

    for pattern, word in _ABBREVIATIONS:
        text = re.sub(pattern, word, text, flags=re.IGNORECASE)
    text = re.sub(r"\s+·\s+", ", ", text)                    # «+2 ⭐ · всего 21» — точка-разделитель, не умножение
    text = re.sub(r"\(([^()а-яА-ЯёЁ]*)\)", r" \1 ", text)   # скобки в формулах просто не читаем: (−3)² → минус 3 в квадрате
    text = speakable(text)
    text = text.replace("(", ", ").replace(")", ", ").replace("«", "").replace("»", "").replace('"', "")
    text = text.replace("*", "").replace("#", "").replace("_", " ")
    # Оставляем буквы, цифры и знаки, по которым голос делает паузы и интонацию; эмодзи и прочее — убираем
    text = re.sub(r"[^\w\s,.\-!?:;—…]", " ", text)
    text = re.sub(r"\s+([,.!?:;…])", r"\1", text)
    text = re.sub(r"([!?])[!?]+", r"\1", text)
    text = re.sub(r",\s*([,.!?:;])", r"\1", text)
    text = re.sub(r"([.!?:;…])\s*,", r"\1", text)
    text = re.sub(r"(^|[.!?]\s*)[,;:]\s*", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,;:")
    return _limit(text, MAX_SPEECH_CHARS)


def _with_pause(line: str) -> str:
    """Конец строки — это пауза: если строка не кончается знаком препинания, ставим точку."""
    stripped = re.sub(r"[\s\U0001F000-\U0001FFFF\u2190-\u2BFF\uFE0F\u200d]+$", "", line)   # эмодзи в конце
    if not stripped:
        return line
    return line if stripped[-1] in ".,!?:;…" else stripped + "."


def _limit(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: end + 1] if end > limit // 2 else cut[: limit - 1].rsplit(" ", 1)[0] + "."


def split_for_tts(text: str, limit: int = TTS_CHUNK) -> List[str]:
    """Режет текст на куски до limit символов по границам предложений (а длинные предложения — по запятым)."""
    pieces: List[str] = []
    for sentence in re.split(r"(?<=[.!?…])\s+", text.strip()):
        if not sentence:
            continue
        if len(sentence) <= limit:
            pieces.append(sentence)
            continue
        part = ""
        for word in re.split(r"(?<=[,;:—])\s+|\s+", sentence):
            if len(part) + len(word) + 1 > limit and part:
                pieces.append(part.strip())
                part = ""
            part += word + " "
        if part.strip():
            pieces.append(part.strip())

    chunks: List[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 1 > limit:
            chunks.append(current)
            current = piece
        else:
            current = f"{current} {piece}".strip()
    if current:
        chunks.append(current)
    return [c for c in chunks if len(c) >= 2]


def parse_tts_stream(raw: bytes) -> bytes:
    """Ответ SpeechKit API v3 — одна или несколько JSON-строк с кусками аудио в base64. Склеиваем их."""
    audio = b""
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return b""
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(text):
        while pos < len(text) and text[pos] in " \r\n\t,[]":
            pos += 1
        if pos >= len(text):
            break
        try:
            obj, pos = decoder.raw_decode(text, pos)
        except ValueError:
            break
        chunk = ((obj.get("result") or obj).get("audioChunk") or {}).get("data") if isinstance(obj, dict) else None
        if chunk:
            audio += base64.b64decode(chunk)
    return audio


def silence(seconds: float = PAUSE_SECONDS, rate: int = SAMPLE_RATE) -> bytes:
    return b"\x00\x00" * int(rate * seconds)


async def pcm_to_ogg(pcm: bytes, rate: int = SAMPLE_RATE) -> Optional[bytes]:
    """Склеенный звук (16 бит, моно) → голосовое OGG/Opus через ffmpeg. None, если ffmpeg нет или ошибка."""
    if not pcm or shutil.which("ffmpeg") is None:
        return None
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0",
            "-c:a", "libopus", "-b:a", "32k", "-application", "voip", "-f", "ogg", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(process.communicate(pcm), timeout=60)
    except Exception as e:
        logger.error("Ошибка склейки голосового: %s", e)
        return None
    if process.returncode != 0 or not out:
        logger.error("ffmpeg не смог собрать голосовое: %s", err.decode(errors="replace")[:300])
        return None
    return out


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None
