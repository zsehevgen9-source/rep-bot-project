"""Школьная программа по классам и предметам: темы по четвертям и учебники (авторы).

Файлы — data/programs/programs_*.json. По дате бот понимает, какие темы уже прошли и какая тема идёт сейчас.
Темп у школ разный, поэтому это ориентир: ученик всегда может написать свою тему.
"""
import glob
import json
import logging
import os
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "programs")

# Четверти: (начало, конец) как (месяц, день); каникулы относим к предыдущей четверти
QUARTERS = [((9, 1), (10, 31)), ((11, 1), (12, 31)), ((1, 1), (3, 31)), ((4, 1), (5, 31))]


class Programs:
    def __init__(self, data_dir: str = DATA_DIR):
        self.data_dir = data_dir
        self.grades: Dict[str, Dict[str, Any]] = {}
        self.authors: Dict[str, Dict[str, List[str]]] = {}   # класс -> предмет -> авторы
        self.loaded = False

    def load(self) -> None:
        self.grades, self.authors = {}, {}
        # programs_school.json (рабочие программы и КТП своей школы) читается последним и заменяет примерную программу
        paths = sorted(glob.glob(os.path.join(self.data_dir, "programs_*.json")), key=lambda p: ("school" in p, p))
        for path in paths:
            try:
                data = json.load(open(path, encoding="utf-8"))
            except (OSError, ValueError) as e:
                logger.error("Не удалось прочитать программу %s: %s", path, e)
                continue
            for grade, subjects in data.get("grades", {}).items():
                for sid, entry in subjects.items():
                    entry = dict(entry)
                    if data.get("school"):
                        entry["school"] = True
                    self.grades.setdefault(grade, {})[sid] = entry
                    authors = entry.get("authors") or data.get("authors", {}).get(sid) or []
                    # Учебник школы — первым, остальные авторы остаются для выбора
                    before = self.authors.setdefault(grade, {}).get(sid, [])
                    self.authors[grade][sid] = list(dict.fromkeys(authors + before)) if data.get("school") else authors
        self.loaded = True
        total = sum(len(s) for s in self.grades.values())
        logger.info("Программы загружены: %d предметов в %d классах", total, len(self.grades))

    def _ensure(self) -> None:
        if not self.loaded:
            self.load()

    def quarters(self, grade: int, subject_id: str) -> Optional[List[List[str]]]:
        self._ensure()
        entry = self.grades.get(str(grade), {}).get(subject_id)
        return entry.get("quarters") if entry else None

    def has(self, grade: int, subject_id: str) -> bool:
        return bool(self.quarters(grade, subject_id))

    def authors_for(self, grade: int, subject_id: str) -> List[str]:
        self._ensure()
        return self.authors.get(str(grade), {}).get(subject_id, [])

    def topics(self, grade: int, subject_id: str) -> List[str]:
        return [t for q in (self.quarters(grade, subject_id) or []) for t in q]

    def is_school(self, grade: int, subject_id: str) -> bool:
        """Программа взята из рабочей программы (КТП) школы, а не примерная."""
        self._ensure()
        return bool((self.grades.get(str(grade), {}).get(subject_id) or {}).get("school"))

    def position(self, grade: int, subject_id: str, today: Optional[date] = None) -> Optional[Tuple[int, int]]:
        """(номер четверти 0–3, номер текущей темы во всей программе) или None, если программы нет."""
        quarters = self.quarters(grade, subject_id)
        if not quarters:
            return None
        today = today or date.today()
        dates = (self.grades.get(str(grade), {}).get(subject_id) or {}).get("dates")
        topics_count = sum(len(q) for q in quarters)
        if dates and len(dates) == topics_count:
            # КТП школы с датами уроков: текущая тема — последняя, чья дата уже наступила
            index = by_date(dates, today)
            bounds, total = [], 0
            for q in quarters:
                total += len(q)
                bounds.append(total)
            return next(i for i, b in enumerate(bounds) if index < b), index
        quarter, fraction = quarter_position(today)
        done_before = sum(len(q) for q in quarters[:quarter])
        in_quarter = len(quarters[quarter])
        index = done_before + min(in_quarter - 1, int(fraction * in_quarter))
        return quarter, index

    def passed_topics(self, grade: int, subject_id: str, today: Optional[date] = None) -> List[str]:
        """Темы, которые к сегодняшнему дню уже прошли (включая текущую)."""
        pos = self.position(grade, subject_id, today)
        return self.topics(grade, subject_id)[: pos[1] + 1] if pos else []

    def current_topics(self, grade: int, subject_id: str, today: Optional[date] = None, around: int = 1) -> List[str]:
        """Тема, которая по программе идёт сейчас, и соседние (для вопроса «какую тему проходили?»)."""
        pos = self.position(grade, subject_id, today)
        if not pos:
            return []
        topics = self.topics(grade, subject_id)
        i = pos[1]
        return [topics[j] for j in (i, i - 1, i + 1) if 0 <= j < len(topics)][: around * 2 + 1]


def _school_key(month: int, day: int) -> Tuple[int, int, int]:
    """Порядок дней внутри учебного года: сентябрь–декабрь раньше января–августа."""
    return (0 if month >= 9 else 1, month, day)


def by_date(dates: List[Optional[str]], today: date) -> int:
    """Номер последней темы, дата которой («MM-DD») уже наступила. Летом — последняя тема года."""
    if today.month in (6, 7, 8):
        return len(dates) - 1
    now = _school_key(today.month, today.day)
    index = 0
    for i, d in enumerate(dates):
        if not d:
            continue
        month, day = int(d[:2]), int(d[3:])
        if _school_key(month, day) <= now:
            index = i
        else:
            break
    return index


def quarter_position(today: date) -> Tuple[int, float]:
    """(четверть 0–3, какая доля четверти прошла 0..1). Летом — конец 4-й четверти (год пройден)."""
    if today.month in (6, 7, 8):
        return 3, 1.0
    for index, ((m1, d1), (m2, d2)) in enumerate(QUARTERS):
        start, end = date(today.year, m1, d1), date(today.year, m2, d2)
        if start <= today <= end:
            return index, (today - start).days / max(1, (end - start).days)
    return 0, 0.0


programs = Programs()
