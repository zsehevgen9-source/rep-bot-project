"""Общее для всех частей обработчика: импорты, предметы, кнопки, помощники."""
import asyncio
import json
import logging
import random
import re
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.handlers.base import BaseHandler
from app.schemas.payloads import NormalizedMessage
from app.services.ai_client import ai_client
from app.services.exam_tasks import detect_subject, get_sdamgia_task
from app.services.profiles import ProfileStore, load_json, weak_topics_for
from app.services import daily as daily_rules
from app.services import progress as topic_progress
from app.services import rewards as rewards_rules
from app.services.task_bank import task_bank
from app.services.task_utils import normalize_answer

logger = logging.getLogger("app.handlers.user")

# Предметы для каждого класса: (название кнопки, id предмета)
SUBJECTS = {
    1: [("Математика", "math"), ("Русский язык", "rus"), ("Литературное чтение", "read"), ("Окружающий мир", "world")],
    2: [("Математика", "math"), ("Русский язык", "rus"), ("Литературное чтение", "read"), ("Окружающий мир", "world"), ("Английский язык", "eng")],
    3: [("Математика", "math"), ("Русский язык", "rus"), ("Литературное чтение", "read"), ("Окружающий мир", "world"), ("Английский язык", "eng")],
    4: [("Математика", "math"), ("Русский язык", "rus"), ("Литературное чтение", "read"), ("Окружающий мир", "world"), ("Английский язык", "eng")],
    5: [("Математика", "math"), ("Русский язык", "rus"), ("Литература", "lit"), ("История", "hist"), ("Биология", "bio"), ("География", "geo"), ("Английский язык", "eng"), ("Информатика", "inf")],
    6: [("Математика", "math"), ("Русский язык", "rus"), ("Литература", "lit"), ("История", "hist"), ("Биология", "bio"), ("География", "geo"), ("Английский язык", "eng"), ("Информатика", "inf")],
    7: [("Алгебра", "math"), ("Геометрия", "geo_math"), ("Русский язык", "rus"), ("Литература", "lit"), ("Физика", "phys"), ("История", "hist"), ("Биология", "bio"), ("География", "geo"), ("Английский язык", "eng"), ("Информатика", "inf")],
    8: [("Алгебра", "math"), ("Геометрия", "geo_math"), ("Русский язык", "rus"), ("Литература", "lit"), ("Физика", "phys"), ("Химия", "chem"), ("История", "hist"), ("Биология", "bio"), ("География", "geo"), ("Английский язык", "eng"), ("Информатика", "inf"), ("ОБЗР", "obzr")],
    9: [("Алгебра", "math"), ("Геометрия", "geo_math"), ("Русский язык", "rus"), ("Литература", "lit"), ("Физика", "phys"), ("Химия", "chem"), ("История", "hist"), ("Биология", "bio"), ("География", "geo"), ("Английский язык", "eng"), ("Обществознание", "soc"), ("Информатика", "inf"), ("ОБЗР", "obzr")],
    10: [("Алгебра", "math"), ("Геометрия", "geo_math"), ("Русский язык", "rus"), ("Литература", "lit"), ("Физика", "phys"), ("Химия", "chem"), ("История", "hist"), ("Биология", "bio"), ("География", "geo"), ("Английский язык", "eng"), ("Обществознание", "soc"), ("Информатика", "inf"), ("ОБЗР", "obzr")],
    11: [("Алгебра", "math"), ("Геометрия", "geo_math"), ("Русский язык", "rus"), ("Литература", "lit"), ("Физика", "phys"), ("Химия", "chem"), ("История", "hist"), ("Биология", "bio"), ("География", "geo"), ("Английский язык", "eng"), ("Обществознание", "soc"), ("Информатика", "inf"), ("ОБЗР", "obzr")],
}

# Названия предметов, если предмета нет в списке выбранного класса (например, ученик ввёл его текстом)
DEFAULT_TITLES = {
    "math": "Математика", "rus": "Русский язык", "read": "Литературное чтение", "world": "Окружающий мир",
    "hist": "История", "bio": "Биология", "geo": "География", "eng": "Английский язык",
    "soc": "Обществознание", "geo_math": "Геометрия", "phys": "Физика", "chem": "Химия",
    "inf": "Информатика", "lit": "Литература", "obzr": "ОБЗР",
}

EXAM_NAMES = {"vpr": "ВПР", "oge": "ОГЭ", "ege": "ЕГЭ"}
# Классы, в которых есть кнопка подготовки к экзамену
EXAM_BY_GRADE = {4: "vpr", 5: "vpr", 6: "vpr", 7: "vpr", 8: "vpr", 9: "oge", 10: "vpr", 11: "ege"}

BACK_TEXT = "◀️ Назад"
MORE_TEXT = "➡️ Ещё задание"
DAY = 86400
FLASHCARDS_FROM_GRADE = 5
GRADE_TTL = 30 * DAY
# Все ключи Redis, которые бот хранит об ученике (нужно для «Удалить мои данные»)
USER_KEY_PREFIXES = [
    "attempts", "awaiting_exam_subject", "awaiting_name", "awaiting_topic", "back", "current_grade", "current_subj",
    "current_subj_id", "current_task", "diag_mode", "diag_state", "exam_type", "grade", "last_task_id", "name",
    "profile", "ref_answer", "task_active", "task_ctx", "task_kind", "options", "daily_plan", "seen", "tut_after", "diag_pick",
    "levels", "stest", "stest_mode", "awaiting_tz", "weektest",
    "lesson", "lesson_opts", "awaiting_schedule", "awaiting_lesson_topic", "awaiting_textbook",
    "sched_pending", "last_file", "awaiting_homework", "hw_tasks",
    "pcode_of", "fcode_of", "is_parent", "awaiting_family_code", "children", "parent_sched_pending", "awaiting_parent_code", "awaiting_parent_sched",
    "voice_on", "apptest", "cert", "consent",
    "my_tclass", "asg_now", "asg_photo", "asg_photo_try", "awaiting_join", "awaiting_tclass", "awaiting_tasg", "t_weak", "t_asgopts", "awaiting_tname", "awaiting_tlink", "tprofile", "tclasses",
    "lh_best", "lh_freeze", "lh_new", "lh_note", "chest", "chest_day", "chest_told", "zap", "zap_best", "interests", "via_ctx", "fc", "fc_sess", "fc_opts", "awaiting_fc_topic", "exvar", "zapapp", "fcapp",
]
# Диагностика в 5–11 классах: обязательные предметы + 2–3 на выбор ученика
DIAG_PICK_FROM_GRADE = 5
DIAG_MANDATORY = ["math", "rus"]
DIAG_PICK_MIN, DIAG_PICK_MAX = 2, 3
# С 7 класса «Математика» в диагностике = алгебра + геометрия, по 2 задания (базовое и повышенное).
# Результаты считаются отдельно по каждому предмету.
MATH_PARTS = ("math", "geo_math")
MATH_PART_LEVELS = (1, 3)
DIAG_TTL = 7 * DAY          # незаконченная диагностика хранится неделю
SECONDS_PER_QUESTION = 35   # для оценки «займёт около N минут» (с учётом озвучки)


# Ширина кнопок в MAX на телефоне. Кнопка во всю ширину вмещает около 24 знаков
# («1️⃣ К счастью, дождь закончился» уже не влезает), две в ряд — по 12, три — по 8, четыре — по 6.
BUTTON_MAX = 24
ROW_LIMITS = {1: 24, 2: 12, 3: 8, 4: 6}
ROW_WIDTH = 28        # (старое название) сумма текстов кнопок в одном ряду
OPTION_MAX = 16       # вариант ответа длиннее — на кнопке только номер, а сам вариант пишем в сообщении
_INVISIBLE = {"\ufe0f", "\u20e3", "\u200d"}

# Короткие названия предметов для кнопок (в тексте сообщений — полные)
BUTTON_TITLES = {
    "Русский язык": "Русский", "Английский язык": "Английский", "Литературное чтение": "Лит. чтение",
    "Окружающий мир": "Окруж. мир", "Обществознание": "Общество",
}


def button_title(title: str) -> str:
    return BUTTON_TITLES.get(title, title)


def _vis_len(text: str) -> int:
    """Сколько места текст займёт на кнопке: эмодзи шире буквы, служебные символы не видны."""
    width, joined = 0, False
    for ch in text:
        if ch == "\u200d":
            joined = True      # эмодзи-«семья» 👨‍👩‍👧 — это один значок, а не три
            continue
        if ch in _INVISIBLE:
            continue
        if joined:
            joined = False
            continue
        width += 2 if ord(ch) >= 0x2190 and not ch.isalnum() else 1
    return width


def _short(text: str, limit: int) -> str:
    """Сокращает текст до limit по границе слова и ставит «…»."""
    text = " ".join(str(text).split())
    if _vis_len(text) <= limit:
        return text
    cut = ""
    for ch in text:
        if _vis_len(cut + ch) > limit - 1:
            break
        cut += ch
    if " " in cut and len(cut.rsplit(" ", 1)[0]) >= len(cut) * 0.6:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:—-") + "…"


def _btn(text: str, payload: str) -> Dict[str, str]:
    return {"type": "callback", "text": _short(text, BUTTON_MAX), "payload": payload}


def _link(text: str, url: str) -> Dict[str, str]:
    """Кнопка-ссылка (например, открыть мини-приложение: https://max.ru/<бот>?startapp=...)."""
    return {"type": "link", "text": _short(text, BUTTON_MAX), "url": url}


async def miniapp_url(redis, screen: str = "home") -> Optional[str]:
    """Ссылка на мини-приложение, если кнопка включена (MINIAPP_BUTTON=true) и имя бота известно."""
    if not getattr(settings, "MINIAPP_BUTTON", False):
        return None
    username = getattr(settings, "MAX_BOT_USERNAME", "") or await redis.get_state("bot_username")
    return f"https://max.ru/{username}?startapp={screen}" if username else None


def _row_fits(buttons: List[Dict[str, str]]) -> bool:
    limit = ROW_LIMITS.get(len(buttons), 0)
    return all(_vis_len(btn["text"]) <= limit for btn in buttons)


def _fit_rows(buttons: List[Dict[str, str]], width: int = 0, max_per_row: int = 3) -> List[List[Dict[str, str]]]:
    """Раскладывает кнопки по рядам так, чтобы текст в каждой влез целиком: короткие — по 2–3 в ряд, длинные — по одной."""
    rows: List[List[Dict[str, str]]] = []
    current: List[Dict[str, str]] = []
    for button in buttons:
        if current and (len(current) >= max_per_row or not _row_fits(current + [button])):
            rows.append(current)
            current = []
        current.append(button)
    if current:
        rows.append(current)
    return rows


def _back(payload: str) -> Dict[str, str]:
    return _btn(BACK_TEXT, payload)


def _kb(rows: List[List[Dict[str, str]]]) -> Dict[str, Any]:
    return {"buttons": rows}


def _in_rows(buttons: List[Dict[str, str]], per_row: int) -> List[List[Dict[str, str]]]:
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


def subject_title(grade: Optional[int], subject_id: str) -> str:
    for name, sid in SUBJECTS.get(grade or 0, []):
        if sid == subject_id:
            return name
    return DEFAULT_TITLES.get(subject_id, "Предмет")


def exam_word(grade: int) -> str:
    """Как называть подготовку: «экзамен» — только в 9 и 11 классах, в остальных — ВПР."""
    exam = EXAM_BY_GRADE.get(grade)
    if exam in ("oge", "ege"):
        return "экзамену"
    return "ВПР" if exam == "vpr" else ""


def exam_subjects(grade: int) -> List[tuple]:
    """Предметы для подготовки к экзамену: математика одна (без разделения на алгебру и геометрию)."""
    result = []
    for name, sid in SUBJECTS.get(grade, []):
        if sid == "geo_math":
            continue
        if sid == "obzr":
            continue  # по ОБЗР нет ни ВПР, ни ОГЭ, ни ЕГЭ
        result.append(("Математика" if sid == "math" else name, sid))
    return result


def _normalize_answer(value: str) -> str:
    return normalize_answer(value)


OPTION_MARKS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
ORDINALS = ["первый", "второй", "третий", "четвёртый"]
# Варианты ответа показываем всегда в 1–4 классах (не все ещё уверенно пишут)
OPTIONS_MAX_GRADE = 4


def _shuffled(options: List[str], seed: Any = None) -> List[str]:
    """Перемешивает варианты, чтобы верный не стоял всегда на одном месте (с seed — одинаково при повторном показе)."""
    items = list(options)
    random.Random(seed).shuffle(items)
    return items


def _option_rows(options: List[str], prefix: str = "ans_") -> List[List[Dict[str, str]]]:
    """Кнопки вариантов ответа.

    Короткие варианты — прямо на кнопках: все в один ряд, если влезают, иначе каждый в своём ряду.
    Если хоть один вариант длинный — на кнопках только номера 1️⃣ 2️⃣ 3️⃣ 4️⃣ в один ряд,
    а сами варианты целиком написаны в сообщении (см. _options_text): так ничего не обрезается.
    """
    if not options:
        return []
    if not _options_fit(options):
        return [[_btn(OPTION_MARKS[i], f"{prefix}{i}") for i in range(len(options))]]
    buttons = [_btn(f"{OPTION_MARKS[i]} {opt}", f"{prefix}{i}") for i, opt in enumerate(options)]
    if _row_fits(buttons):
        return [buttons]
    return [[b] for b in buttons]


def _options_fit(options: List[str]) -> bool:
    return all(_vis_len(" ".join(str(o).split())) <= OPTION_MAX for o in options)


def _options_text(options: List[str]) -> str:
    """Если варианты длинные (на кнопках только номера) — пишем их целиком в самом сообщении."""
    if not options or _options_fit(options):
        return ""
    return "\n\n" + "\n".join(f"{OPTION_MARKS[i]} {o}" for i, o in enumerate(options))


def _options_call(options: List[str]) -> str:
    """Подсказка, как ответить: кнопкой с вариантом или номером варианта."""
    return "Выбери ответ кнопкой 👇" if _options_fit(options) else "Нажми номер верного варианта 👇"


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _spelling_variants(options: List[str]) -> bool:
    """Варианты отличаются одной буквой («жираф | жыраф», «грип | гриб»): вслух они звучат одинаково."""
    words = [str(o).strip().lower().replace("ё", "е") for o in options]
    for i, a in enumerate(words):
        for b in words[i + 1:]:
            if min(len(a), len(b)) < 4 or not (re.fullmatch(r"[а-я\s-]+", a) and re.fullmatch(r"[а-я\s-]+", b)):
                continue      # только русские слова (не числа и не короткие слова вроде «и», «но»)
            if a in b or b in a:
                continue      # «миграция» и «эмиграция» на слух различаются
            if _edit_distance(a, b) == 1:
                return True
                return True
    return False


def _options_voice(options: List[str]) -> str:
    if _spelling_variants(options):
        # Вслух «жираф» и «жыраф» не отличить — не читаем варианты, а просим посмотреть на экран
        return "Посмотри на варианты на экране и выбери, как написано правильно."
    return "Варианты ответа: " + "; ".join(f"{ORDINALS[i]} — {o}" for i, o in enumerate(options)) + "."


def _reference_core(reference: str) -> str:
    """Эталон без пояснения в скобках: «Пушкин (подходит любой ответ…)» -> «Пушкин»."""
    return re.sub(r"\(.*?\)", "", reference or "").strip()

__all__ = ['asyncio', 'json', 'logging', 'random', 're', 'Any', 'Dict', 'List', 'Optional', 'settings', 'BaseHandler', 'NormalizedMessage', 'ai_client', 'detect_subject', 'get_sdamgia_task', 'ProfileStore', 'load_json', 'weak_topics_for', 'task_bank', 'logger', 'SUBJECTS', 'DEFAULT_TITLES', 'EXAM_NAMES', 'EXAM_BY_GRADE', 'BACK_TEXT', 'MORE_TEXT', 'DAY', 'GRADE_TTL', 'DIAG_TTL', 'SECONDS_PER_QUESTION', 'USER_KEY_PREFIXES', '_btn', '_link', 'miniapp_url', '_back', '_kb', '_in_rows', '_fit_rows', 'button_title', '_row_fits', 'BUTTON_MAX', 'DAY', '_short', '_vis_len', '_options_text', '_options_call', '_options_fit', 'subject_title', 'exam_subjects', '_normalize_answer', '_reference_core', 'topic_progress', 'normalize_answer', 'OPTION_MARKS', 'ORDINALS', 'OPTIONS_MAX_GRADE', '_shuffled', '_option_rows', '_options_voice', 'daily_rules', 'rewards_rules', 'DIAG_PICK_FROM_GRADE', 'FLASHCARDS_FROM_GRADE', 'DIAG_MANDATORY', 'DIAG_PICK_MIN', 'DIAG_PICK_MAX', 'MATH_PARTS', 'MATH_PART_LEVELS', 'exam_word']
