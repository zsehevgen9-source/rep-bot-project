"""Текст из присланных файлов (Word, Excel, текст) — без внешних библиотек.

Фото и PDF распознаёт Yandex Vision OCR (см. ai_client.recognize_image_text), здесь — только определение формата.
"""
import asyncio
import io
import logging
import re
import shutil
import zipfile
from typing import Optional
from xml.etree import ElementTree

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
MAX_TEXT = 20_000
logger = logging.getLogger(__name__)


def image_mime(data: bytes) -> Optional[str]:
    """MIME для OCR: JPEG, PNG или PDF (по первым байтам файла)."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:5] == b"%PDF-":
        return "application/pdf"
    return None


def docx_text(data: bytes) -> str:
    """Текст документа Word: абзацы — строками, ячейки таблиц — через « | »."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    lines = []
    body = root.find(f"{_W}body")
    for block in (body if body is not None else []):
        if block.tag == f"{_W}p":
            lines.append("".join(t.text or "" for t in block.iter(f"{_W}t")))
        elif block.tag == f"{_W}tbl":
            for row in block.iter(f"{_W}tr"):
                cells = ["".join(t.text or "" for t in cell.iter(f"{_W}t")).strip() for cell in row.iter(f"{_W}tc")]
                lines.append(" | ".join(cells))
    return "\n".join(line for line in lines if line.strip())[:MAX_TEXT]


def xlsx_text(data: bytes) -> str:
    """Текст первого листа Excel: строка таблицы — строка текста, ячейки через « | »."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ElementTree.fromstring(z.read("xl/sharedStrings.xml")).iter(f"{_S}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{_S}t")))
        sheets = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        if not sheets:
            return ""
        root = ElementTree.fromstring(z.read(sheets[0]))
    lines = []
    for row in root.iter(f"{_S}row"):
        cells = []
        for c in row.iter(f"{_S}c"):
            value = c.find(f"{_S}v")
            inline = c.find(f"{_S}is")
            if c.get("t") == "s" and value is not None and value.text and value.text.isdigit():
                index = int(value.text)
                cells.append(shared[index] if index < len(shared) else "")
            elif inline is not None:
                cells.append("".join(t.text or "" for t in inline.iter(f"{_S}t")))
            elif value is not None:
                cells.append(value.text or "")
        if any(cell.strip() for cell in cells):
            lines.append(" | ".join(cell.strip() for cell in cells))
    return "\n".join(lines)[:MAX_TEXT]


def document_text(data: bytes, filename: str) -> Optional[str]:
    """Текст файла или None, если формат не поддерживается (тогда — OCR или просьба прислать фото)."""
    name = (filename or "").lower()
    try:
        if name.endswith(".docx") or (data[:2] == b"PK" and b"word/" in data[:2000]):
            return docx_text(data)
        if name.endswith(".xlsx") or (data[:2] == b"PK" and b"xl/" in data[:2000]):
            return xlsx_text(data)
        if name.endswith((".txt", ".csv")):
            for encoding in ("utf-8-sig", "cp1251"):
                try:
                    return data.decode(encoding)[:MAX_TEXT]
                except UnicodeDecodeError:
                    continue
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError):
        return None
    return None


def describe_bytes(data: Optional[bytes]) -> str:
    """Для лога: размер и формат файла (без содержимого)."""
    if not data:
        return "пусто"
    kind = image_mime(data) or ("webp" if data[8:12] == b"WEBP" else "zip" if data[:2] == b"PK"
                                else "html" if data.lstrip()[:1] == b"<" else "неизвестный")
    return f"{len(data)} байт, формат: {kind}"


async def to_jpeg(data: bytes, max_side: int = 2500) -> Optional[bytes]:
    """Любая картинка (WEBP, PNG, огромное фото) → JPEG не больше max_side по стороне, через ffmpeg.
    None — если ffmpeg нет или это не картинка."""
    if shutil.which("ffmpeg") is None:
        return None
    scale = f"scale='min({max_side},iw)':'min({max_side},ih)':force_original_aspect_ratio=decrease"
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-vf", scale, "-frames:v", "1",
            "-f", "image2", "-c:v", "mjpeg", "-q:v", "3", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(process.communicate(data), timeout=30)
    except Exception as e:
        logger.error("Не удалось перекодировать картинку: %s", e)
        return None
    if process.returncode != 0 or not out:
        logger.error("ffmpeg не смог прочитать картинку: %s", err.decode(errors="replace")[:200])
        return None
    return out
