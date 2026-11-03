"""«Мой маяк»: серия дней занятий, заморозка и этапы стройки маяка.

Серия считается в учебных днях: выходные её не прерывают (см. daily.update_streak).
Заморозка 🧊: раз в неделю спасает серию, если пропущен ОДИН учебный день (заболел, уехал).
Здесь только правила — без MAX, базы и Redis. Картинку рисует cards.lighthouse_card.
"""
from datetime import date
from typing import Optional, Tuple

from app.services.daily import school_days_between

# (сколько дней подряд, что появляется на маяке)
MILESTONES = [
    (1, "фундамент"),
    (2, "стены маяка"),
    (3, "окна и дверь"),
    (5, "флаг на крыше"),
    (7, "огонь маяка"),
    (10, "чайки"),
    (14, "лодка у причала"),
    (21, "звёздное небо"),
    (30, "праздничный салют"),
]


def stage(streak: int) -> int:
    """Сколько этапов уже построено (0 — только скалы)."""
    return sum(1 for days, _ in MILESTONES if streak >= days)


def next_milestone(streak: int) -> Optional[Tuple[int, str]]:
    for days, title in MILESTONES:
        if streak < days:
            return days, title
    return None


def reached(old: int, new: int) -> Optional[str]:
    """Что достроили, если серия перешагнула этап (иначе None)."""
    got = [title for days, title in MILESTONES if old < days <= new]
    return got[-1] if got else None


def week_key(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def freeze_ready(used_week: Optional[str], today: date) -> bool:
    return used_week != week_key(today)


def missed_days(last_study_date: Optional[str], today: date) -> int:
    """Сколько учебных дней пропущено между последним занятием и сегодня."""
    if not last_study_date:
        return 0
    last = date.fromisoformat(last_study_date)
    return school_days_between(last, today) if last < today else 0


def next_streak(last_study_date: Optional[str], streak: int, today: date,
                used_week: Optional[str]) -> Tuple[int, bool]:
    """Серия после занятия сегодня и (True), если для этого сработала заморозка."""
    if not last_study_date:
        return 1, False
    last = date.fromisoformat(last_study_date)
    if last >= today:
        return max(streak, 1), False
    gap = school_days_between(last, today)
    if gap == 0:
        return streak + 1, False
    if gap == 1 and streak > 0 and freeze_ready(used_week, today):
        return streak + 1, True
    return 1, False


def current_streak(last_study_date: Optional[str], streak: int, today: date, used_week: Optional[str]) -> int:
    """Серия «на сегодня» для экрана: если пропущено слишком много — она уже сгорела."""
    gap = missed_days(last_study_date, today)
    if gap == 0:
        return streak or 0
    if gap == 1 and freeze_ready(used_week, today):
        return streak or 0    # ещё можно спасти заморозкой — позанимайся сегодня
    return 0
