"""Пробный вариант ОГЭ/ЕГЭ: прогноз оценки или балла по доле верных ответов.

Это ОРИЕНТИР, а не официальный пересчёт: настоящие шкалы ФИПИ свои для каждого предмета и года,
а в боте — только задания с выбором ответа. Бот так и пишет ученику.
"""
from typing import Any, Dict, List, Optional

VARIANT_SIZE = 10
HISTORY = 10


def oge_mark(share: float) -> int:
    """Доля верных → оценка ОГЭ (примерно как в типичных шкалах ФИПИ: ~35% — «3», ~60% — «4», ~85% — «5»)."""
    if share >= 0.85:
        return 5
    if share >= 0.6:
        return 4
    if share >= 0.35:
        return 3
    return 2


def ege_score(share: float) -> int:
    """Доля верных → примерный тестовый балл ЕГЭ. Шкала выпуклая, как настоящая: первые баллы «дороже»."""
    share = max(0.0, min(1.0, share))
    return round(100 * (1 - (1 - share) ** 1.4))


def forecast(exam: str, right: int, total: int) -> Dict[str, Any]:
    share = right / total if total else 0
    if exam == "ege":
        score = ege_score(share)
        return {"share": share, "value": score, "text": f"≈ {score} баллов из 100",
                "low": share < 0.3}
    mark = oge_mark(share)
    return {"share": share, "value": mark, "text": f"оценка «{mark}»", "low": mark == 2}


def add_history(history: Dict[str, List[Dict[str, Any]]], subject_id: str, entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Сохраняет результат варианта, возвращает прошлый результат по этому предмету (для «было → стало»)."""
    items = history.setdefault(subject_id, [])
    prev = items[-1] if items else None
    items.append(entry)
    del items[:-HISTORY]
    return prev


def duration_text(seconds: float) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    return f"{m} мин {s:02d} с" if m else f"{s} с"
