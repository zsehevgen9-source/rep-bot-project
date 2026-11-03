"""Расписание уроков ученика и оценки за уроки (только расчёты, без MAX, базы и Redis).

Расписание хранится как {"0": ["math", "rus", "read"], "1": [...], ...}: 0 — понедельник, 5 — суббота.
"""
import re
from typing import Dict, List, Optional, Tuple

DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб"]
DAY_NAMES = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота"]
_DAY_STEMS = [("пон", "пн"), ("вто", "вт"), ("сре", "ср"), ("чет", "чт"), ("пят", "пт"), ("суб", "сб")]

# Основы названий предметов. Порядок важен: «литературное чтение» раньше «литературы», «геометрия» раньше «математики»
_SUBJECT_STEMS: List[Tuple[str, List[str]]] = [
    ("read", ["литературное чтение", "лит. чтение", "лит.чтение", "литчтение", "чтение"]),
    ("lit", ["литератур"]),
    ("world", ["окружающ", "окр. мир", "окр.мир", "окрмир"]),
    ("geo_math", ["геометр"]),
    ("math", ["математ", "алгебр", "матем"]),
    ("rus", ["русск", "рус. яз", "рус.яз", "русяз"]),
    ("eng", ["англ", "иностран", "ин. яз", "ин.яз"]),
    ("hist", ["истор"]),
    ("soc", ["обществ"]),
    ("geo", ["географ"]),
    ("bio", ["биолог"]),
    ("phys", ["физика", "физике", "физики"]),
    ("chem", ["хими"]),
    ("inf", ["информат"]),
    ("obzr", ["обзр", "обж", "основы безопасности"]),
]


def detect_day(text: str) -> Optional[int]:
    word = (text or "").strip().lower().rstrip(":.")
    for index, (stem, short) in enumerate(_DAY_STEMS):
        if word == short or word.startswith(stem):
            return index
    return None


def detect_lesson(text: str, subject_ids: List[str]) -> Optional[str]:
    """id предмета по названию урока (только предметы класса; физкультура, музыка и т. п. — None)."""
    low = re.sub(r"\s+", " ", (text or "").lower().replace("ё", "е")).strip()
    for sid, stems in _SUBJECT_STEMS:
        if any(stem in low for stem in stems):
            if sid == "geo_math" and sid not in subject_ids:
                sid = "math"
            if sid == "lit" and "lit" not in subject_ids and "read" in subject_ids:
                sid = "read"
            return sid if sid in subject_ids else None
    return None


def parse_schedule(text: str, subject_ids: List[str]) -> Tuple[Dict[str, List[str]], List[str]]:
    """Расписание из текста. Понимает «Пн: математика, русский; Вт: …» и столбиком:
    «Понедельник / 1. Математика / 2. Русский». Возвращает (расписание, непонятые уроки)."""
    schedule: Dict[str, List[str]] = {}
    unknown: List[str] = []
    day: Optional[int] = None
    for chunk in re.split(r"[\n;]+", text or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.match(r"^([А-Яа-яЁё]+)\.?\s*[:\-—]?\s*(.*)$", chunk)
        if m and detect_day(m.group(1)) is not None:
            day = detect_day(m.group(1))
            chunk = m.group(2)
        if day is None:
            continue
        for item in re.split(r",", chunk):
            item = re.sub(r"^\s*\d+\s*[.)]?\s*", "", item).strip(" .")
            if not item:
                continue
            sid = detect_lesson(item, subject_ids)
            if sid:
                schedule.setdefault(str(day), []).append(sid)
            elif item.lower() not in unknown:
                unknown.append(item.lower())
    return schedule, unknown


def lessons_on(schedule: Dict[str, List[str]], weekday: int) -> List[str]:
    """Предметы дня без повторов (в порядке уроков)."""
    result: List[str] = []
    for sid in schedule.get(str(weekday), []):
        if sid not in result:
            result.append(sid)
    return result


def mark_for(right: int, total: int) -> int:
    """Оценка за задания урока: 90% и выше — 5, 70% — 4, 50% — 3, меньше — 2."""
    if not total:
        return 2
    ratio = right / total
    if ratio >= 0.9:
        return 5
    if ratio >= 0.7:
        return 4
    if ratio >= 0.5:
        return 3
    return 2
