"""Прогресс ученика по темам и интервальное повторение.

Прогресс хранится как словарь (JSON в профиле ученика):
    {"4|math|задачи на движение": {"grade": 4, "subject_id": "math", "subject": "Математика",
                                    "topic": "Задачи на движение", "status": "learning", "box": 1, ...}, ...}

Статусы темы:
    weak      — «стоит подтянуть» (ошибка в диагностике);
    learning  — «изучаем»;
    mastered  — «закреплено»: верно с первой попытки на повторениях (уровень 3 и выше);
    known     — «знает»: верно в диагностике, повторяем редко.

Интервальное повторение (система Лейтнера): каждый верный ответ с первой попытки поднимает тему на уровень,
ошибка возвращает её на нулевой. Чем выше уровень, тем реже тема возвращается.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

# Через сколько дней вернуть тему на повторение для каждого уровня (0…4)
INTERVALS_DAYS = [1, 3, 7, 14, 30]
MASTERED_BOX = 3

STATUS_LABELS = {
    "weak": "❗ стоит подтянуть",
    "learning": "🔄 изучаем",
    "mastered": "✅ закреплено",
    "known": "👍 знает",
}


def now() -> datetime:
    """Текущее время (вынесено в функцию, чтобы в тестах можно было «перемотать» время)."""
    return datetime.now(timezone.utc)


def topic_key(grade: int, subject_id: str, topic: str) -> str:
    return f"{grade}|{subject_id}|{topic.strip().lower()}"


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _entry(progress: Dict[str, Any], grade: int, subject_id: str, subject: str, topic: str) -> Dict[str, Any]:
    key = topic_key(grade, subject_id, topic)
    entry = progress.get(key)
    if entry is None:
        entry = {
            "grade": grade, "subject_id": subject_id, "subject": subject, "topic": topic.strip(),
            "status": "learning", "box": 0, "correct": 0, "wrong": 0,
            "was_weak": False, "next_review": None, "last_seen": None,
        }
        progress[key] = entry
    return entry


def _schedule(entry: Dict[str, Any], moment: datetime) -> None:
    box = max(0, min(entry["box"], len(INTERVALS_DAYS) - 1))
    entry["next_review"] = (moment + timedelta(days=INTERVALS_DAYS[box])).isoformat()
    entry["last_seen"] = moment.isoformat()


def record_answer(
    progress: Dict[str, Any], grade: int, subject_id: str, subject: str, topic: str, outcome: str,
    moment: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Учитывает ответ на задание по теме.

    outcome: "correct" — верно с первой попытки, "hinted" — верно после подсказок, "wrong" — первая попытка неверная.
    """
    moment = moment or now()
    entry = _entry(progress, grade, subject_id, subject, topic)
    if outcome == "correct":
        entry["correct"] += 1
        entry["box"] = min(entry["box"] + 1, len(INTERVALS_DAYS) - 1)
    elif outcome == "hinted":
        entry["correct"] += 1  # тема понята не до конца: уровень не растёт, повторим скоро
    else:
        entry["wrong"] += 1
        entry["box"] = 0
    entry["status"] = "mastered" if entry["box"] >= MASTERED_BOX else "learning"
    _schedule(entry, moment)
    return entry


def mark_diagnostic(
    progress: Dict[str, Any], grade: int, results: List[Dict[str, Any]], moment: Optional[datetime] = None
) -> None:
    """Переносит итоги диагностики в прогресс: ошибки — «стоит подтянуть» (на повторение сразу), верные — «знает»."""
    moment = moment or now()
    by_topic: Dict[str, List[str]] = {}
    for r in results:
        if r.get("topic") and r.get("subject_id"):
            by_topic.setdefault(topic_key(grade, r["subject_id"], r["topic"]), []).append(r["status"])

    for r in results:
        if not r.get("topic") or not r.get("subject_id"):
            continue
        key = topic_key(grade, r["subject_id"], r["topic"])
        statuses = by_topic.get(key, [])
        is_new = key not in progress
        entry = _entry(progress, grade, r["subject_id"], r["subject"], r["topic"])
        if any(s != "верно" for s in statuses):
            entry.update(status="weak", box=0, was_weak=True, next_review=moment.isoformat(), last_seen=moment.isoformat())
        elif is_new or entry["status"] not in ("learning", "mastered"):
            # Верно в диагностике: «знает». Тему, которую уже изучали или закрепили, не трогаем
            entry.update(status="known", box=2, last_seen=moment.isoformat())
            entry["next_review"] = (moment + timedelta(days=INTERVALS_DAYS[3])).isoformat()


def due_topics(
    progress: Dict[str, Any], grade: int, subject_id: Optional[str] = None, moment: Optional[datetime] = None
) -> List[Dict[str, Any]]:
    """Темы, которые пора повторить: сначала «стоит подтянуть», потом самые «просроченные»."""
    moment = moment or now()
    due = []
    for entry in progress.values():
        if entry.get("grade") != grade or (subject_id and entry.get("subject_id") != subject_id):
            continue
        review = _parse(entry.get("next_review"))
        if review is not None and review <= moment:
            due.append(entry)
    return sorted(due, key=lambda e: (e.get("status") != "weak", e.get("next_review") or ""))


def summary(progress: Dict[str, Any], grade: int, moment: Optional[datetime] = None) -> Dict[str, Any]:
    """Сводка для экрана «Мой прогресс» и статистики."""
    moment = moment or now()
    entries = [e for e in progress.values() if e.get("grade") == grade]
    by_subject: Dict[str, Dict[str, int]] = {}
    for e in entries:
        counts = by_subject.setdefault(e["subject"], {s: 0 for s in STATUS_LABELS})
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    was_weak = [e for e in entries if e.get("was_weak")]
    return {
        "by_subject": by_subject,
        "due": due_topics(progress, grade, moment=moment),
        "was_weak": len(was_weak),
        "weak_now_mastered": sum(1 for e in was_weak if e["status"] == "mastered"),
        "total": len(entries),
    }
