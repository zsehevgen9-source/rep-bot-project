import csv
import glob
import io
import json
import logging
import os
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.services.task_utils import needs_picture, normalize_answer, parse_options
from app.services.exam_tasks import detect_subject

logger = logging.getLogger(__name__)

SUBJECT_IDS = {"math", "rus", "read", "world", "hist", "bio", "geo", "eng", "soc", "geo_math", "phys", "chem", "inf", "lit"}

# Русские заголовки столбцов таблицы -> поля задания
CSV_HEADERS = {
    "тип": "kind", "экзамен": "exam", "класс": "grade", "предмет": "subject", "тема": "topic",
    "задание": "text", "ответ": "answer", "источник": "source", "ссылка": "url", "id": "id",
    "картинка": "image", "уровень": "level", "варианты": "options",
}
KIND_VALUES = {
    "экзамен": "exam", "exam": "exam", "обычное": "regular", "обычная": "regular", "regular": "regular",
    "диагностика": "diagnostic", "diagnostic": "diagnostic",
}
EXAM_VALUES = {"впр": "vpr", "огэ": "oge", "егэ": "ege", "vpr": "vpr", "oge": "oge", "ege": "ege"}


@dataclass
class BankTask:
    id: str
    kind: str          # "regular" — обычная учёба, "exam" — подготовка к ВПР/ОГЭ/ЕГЭ, "diagnostic" — диагностика
    grade: int
    subject: str       # id предмета: math, rus, read, world, hist, bio, geo, eng, soc, geo_math, phys, chem, inf, lit
    text: str
    answer: str = ""
    topic: str = ""
    exam: str = ""     # для kind="exam": vpr / oge / ege
    source: str = ""
    url: str = ""
    image: str = ""    # полный путь к картинке (файл из data/images), если она есть
    level: int = 0     # для диагностики: 1 — лёгкий, 2 — средний, 3 — сложный
    options: List[str] = field(default_factory=list)  # варианты ответа (кнопки), если есть


def _subject_id(value: str) -> str:
    value = value.strip()
    if value in SUBJECT_IDS:
        return value
    if "геометр" in value.lower():
        return "geo_math"
    subject = detect_subject(value)
    if not subject:
        raise ValueError(f"неизвестный предмет «{value}»")
    return subject


def _make_task(item: Dict[str, str], default_id: str, images_dir: str = "data/images") -> BankTask:
    kind = KIND_VALUES.get(str(item.get("kind", "")).strip().lower())
    if not kind:
        raise ValueError(f"тип должен быть «экзамен» или «обычное», а не «{item.get('kind')}»")
    exam = EXAM_VALUES.get(str(item.get("exam") or "").strip().lower(), "")
    if kind == "exam" and not exam:
        raise ValueError("для экзаменационного задания укажите экзамен: ВПР, ОГЭ или ЕГЭ")
    text = str(item.get("text") or "").strip()
    if not text:
        raise ValueError("пустой текст задания")
    grade = int(str(item.get("grade", "")).strip())
    if not 1 <= grade <= 11:
        raise ValueError(f"класс должен быть от 1 до 11, а не {grade}")

    image_name = str(item.get("image") or "").strip()
    image_path = ""
    if image_name:
        image_path = os.path.join(images_dir, os.path.basename(image_name))
        if not os.path.isfile(image_path):
            raise ValueError(f"нет файла картинки {image_path}")
    elif needs_picture(text):
        raise ValueError(
            "задание ссылается на рисунок/схему, а картинки нет: положите файл в data/images "
            "и укажите его имя в столбце «картинка» (или уберите задание)"
        )

    return BankTask(
        id=str(item.get("id") or default_id),
        kind=kind,
        grade=grade,
        subject=_subject_id(str(item.get("subject", ""))),
        text=text,
        answer=str(item.get("answer") or "").strip(),
        topic=str(item.get("topic") or "").strip(),
        exam=exam,
        source=str(item.get("source") or "").strip(),
        url=str(item.get("url") or "").strip(),
        image=image_path,
        level=_level(item.get("level")),
        options=_options(item.get("options"), str(item.get("answer") or "")),
    )


def _options(value: Any, answer: str) -> List[str]:
    options = parse_options(value)
    if not options:
        return []
    if not 2 <= len(options) <= 4:
        raise ValueError(f"вариантов ответа должно быть от 2 до 4, а не {len(options)}")
    if answer and normalize_answer(answer) not in [normalize_answer(o) for o in options]:
        raise ValueError(f"ответ «{answer}» должен совпадать с одним из вариантов")
    return options


def _level(value: Any) -> int:
    value = str(value or "").strip()
    if not value:
        return 0
    level = int(value)
    if not 1 <= level <= 3:
        raise ValueError(f"уровень должен быть 1, 2 или 3, а не {level}")
    return level


def _read_csv_rows(path: str) -> List[Dict[str, str]]:
    """Читает таблицу, сохранённую из Excel: кодировка UTF-8 или Windows-1251, разделитель «;» или «,»."""
    with open(path, "rb") as f:
        raw = f.read()
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = raw.decode("cp1251")
    first_line = content.splitlines()[0] if content else ""
    delimiter = ";" if first_line.count(";") >= first_line.count(",") else ","

    rows = []
    for row in csv.DictReader(io.StringIO(content), delimiter=delimiter):
        mapped = {}
        for key, value in row.items():
            if key is None:
                continue
            field = CSV_HEADERS.get(key.strip().lower(), key.strip().lower())
            mapped[field] = (value or "").strip()
        if any(mapped.values()):
            rows.append(mapped)
    return rows


class TaskBank:
    """Банк заданий: data/tasks.json и/или таблица data/tasks.csv. Файлы перечитываются, когда их меняют."""

    def __init__(self, path: Optional[str] = None, csv_path: Optional[str] = None):
        self.path = path or settings.TASKS_FILE
        self.csv_path = csv_path or os.path.join(os.path.dirname(self.path) or ".", "tasks.csv")
        self.images_dir = os.path.join(os.path.dirname(self.path) or ".", "images")
        self._tasks: List[BankTask] = []
        self._mtimes: Optional[Dict[str, Optional[float]]] = None

    def _csv_files(self) -> List[str]:
        data_dir = os.path.dirname(self.csv_path) or "."
        return [self.csv_path] + sorted(glob.glob(os.path.join(data_dir, "diagnostic*.csv")))

    def _current_mtimes(self) -> Dict[str, Optional[float]]:
        result = {}
        for p in [self.path] + self._csv_files():
            try:
                result[p] = os.path.getmtime(p)
            except OSError:
                result[p] = None
        return result

    def _load_if_changed(self) -> None:
        mtimes = self._current_mtimes()
        if mtimes == self._mtimes:
            return
        self._mtimes = mtimes

        tasks: List[BankTask] = []
        if mtimes[self.path] is not None:
            try:
                with open(self.path, encoding="utf-8") as f:
                    raw = json.load(f)
                for i, item in enumerate(raw if isinstance(raw, list) else []):
                    try:
                        tasks.append(_make_task(item, f"json-{i + 1}", self.images_dir))
                    except (ValueError, TypeError, AttributeError) as e:
                        logger.warning("tasks.json, задание №%d пропущено: %s", i + 1, e)
            except (OSError, ValueError) as e:
                logger.error("Не удалось прочитать %s: %s", self.path, e)

        for csv_file in self._csv_files():
            if mtimes.get(csv_file) is None:
                continue
            short = os.path.basename(csv_file)
            try:
                for i, row in enumerate(_read_csv_rows(csv_file)):
                    try:
                        tasks.append(_make_task(row, f"{short}-{i + 2}", self.images_dir))
                    except (ValueError, TypeError) as e:
                        logger.warning("%s, строка %d пропущена: %s", short, i + 2, e)
            except (OSError, csv.Error) as e:
                logger.error("Не удалось прочитать %s: %s", csv_file, e)

        self._tasks = tasks
        counts: Dict[str, int] = {}
        for t in tasks:
            if t.kind == "exam":
                key = {"vpr": "ВПР", "oge": "ОГЭ", "ege": "ЕГЭ"}.get(t.exam, t.exam)
            else:
                key = "диагностика" if t.kind == "diagnostic" else "обычные"
            counts[key] = counts.get(key, 0) + 1
        details = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "пусто"
        logger.info("Банк заданий загружен: %d шт. (%s)", len(tasks), details)

    def pick(
        self,
        kind: str,
        grade: int,
        subject: str,
        exam: Optional[str] = None,
        topic: Optional[str] = None,
        exclude_id: Optional[str] = None,
    ) -> Optional[BankTask]:
        """Случайное подходящее задание или None, если в банке такого нет."""
        self._load_if_changed()
        topic_l = topic.strip().lower() if topic else None

        candidates = [
            t for t in self._tasks
            if t.kind == kind and t.grade == grade and t.subject == subject
            and (kind != "exam" or t.exam == exam)
            and (not topic_l or topic_l in t.topic.lower() or topic_l in t.text.lower())
        ]
        # Не выдаём то же задание подряд, если есть другое
        others = [t for t in candidates if t.id != exclude_id]
        pool = others or candidates
        return random.choice(pool) if pool else None

    def diagnostic_questions(self, grade: int, subject_order: List[str]) -> List[BankTask]:
        """Вопросы диагностики для класса: по предметам в порядке кнопок, внутри предмета — от лёгкого к сложному."""
        self._load_if_changed()
        order = {sid: i for i, sid in enumerate(subject_order)}
        questions = [t for t in self._tasks if t.kind == "diagnostic" and t.grade == grade]
        return sorted(questions, key=lambda t: (order.get(t.subject, len(order)), t.level))

    def get(self, task_id: str) -> Optional[BankTask]:
        self._load_if_changed()
        return next((t for t in self._tasks if t.id == task_id), None)

    def load(self) -> None:
        """Загрузка при старте бота, чтобы сразу увидеть в логе, сколько заданий в банке."""
        self._mtimes = None
        self._load_if_changed()


task_bank = TaskBank()
