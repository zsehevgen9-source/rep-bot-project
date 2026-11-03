"""Красивое оформление сообщений: обычный текст бота → HTML-разметка MAX.

Правила (без изменения самих текстов):
  • первая строка-заголовок (короткая, начинается с эмодзи) — жирная;
  • короткая строка, которая заканчивается двоеточием («📚 Темы:») — жирная;
  • «Подзаголовок: значение» в начале строки с эмодзи («📗 Учебник: …») — подзаголовок жирный;
  • текст в «ёлочках» после слова «тема/тему/теме» — курсивом;
  • строки «Подсказка…», «Правильный ответ…» — жирное начало.
Всё остальное экранируется, чтобы символы < > & в заданиях (например «3 < 5») не ломали разметку.
"""
import html
import re

MAX_TEXT = 4000

_EMOJI_START = re.compile(r"^[\U0001F000-\U0001FAFF☀-➿⬀-⯿←-⇿⌀-⏿]")
_LABEL = re.compile(r"^([\U0001F000-\U0001FAFF☀-➿⬀-⯿←-⇿⌀-⏿]️?\s?[^:\n]{1,28}):(\s)")
_TOPIC = re.compile(r"(тем[аеуы]\s)«([^«»\n]{1,80})»", re.IGNORECASE)
_LEAD = re.compile(r"^(💡 Подсказка|Подсказка|✅ Правильный ответ|Правильный ответ|Ответ)(:)")


def _line(line: str, first: bool) -> str:
    stripped = line.strip()
    if not stripped:
        return ""
    if (first and _EMOJI_START.match(stripped) and len(stripped) <= 70 and not stripped.endswith("?")) or \
            (stripped.endswith(":") and len(stripped) <= 50 and not stripped.startswith(("•", "-"))):
        return f"<b>{html.escape(line)}</b>"
    out = html.escape(line, quote=False)
    out = _LABEL.sub(lambda m: f"<b>{m.group(1)}:</b>{m.group(2)}", out, count=1)
    out = _LEAD.sub(lambda m: f"<b>{m.group(1)}{m.group(2)}</b>", out, count=1)
    out = _TOPIC.sub(lambda m: f"{m.group(1)}«<i>{m.group(2)}</i>»", out)
    return out


def to_html(text: str) -> str:
    """Текст бота → HTML для MAX. Пустой текст остаётся пустым."""
    if not text:
        return text
    lines = text.split("\n")
    first_index = next((i for i, l in enumerate(lines) if l.strip()), 0)
    return "\n".join(_line(l, i == first_index) for i, l in enumerate(lines))


def bar(share: float, width: int = 10) -> str:
    """Полоска прогресса из символов: ▰▰▰▱▱."""
    share = max(0.0, min(1.0, share))
    full = round(share * width)
    return "▰" * full + "▱" * (width - full)
