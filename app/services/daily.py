"""Ежедневные занятия: когда писать ученику, что дать на сегодня и как считать активность.

Здесь только расчёты (без MAX, базы и Redis), поэтому логику легко проверить тестами.

Правила уведомлений:
- только по будням (пн–пт) и только с 7:00 до 21:00 по местному времени ученика;
- не больше двух сообщений в день: задание на день и вечерний отчёт;
- если ученик 3 учебных дня не отвечал — вместо задания один мягкий вопрос «Всё в порядке?»,
  и бот замолкает, пока ученик сам не напишет.
"""
import random
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from app.services import progress as topic_progress

DEFAULT_DAILY_TIME = "16:00"
DEFAULT_REPORT_TIME = "19:30"
DEFAULT_TZ_OFFSET = 300            # минуты от UTC; 300 = UTC+5
QUIET_START, QUIET_END = 7, 21     # раньше 7:00 и позже 21:00 не пишем
SILENT_SCHOOL_DAYS = 3             # столько учебных дней без ответа — и бот спрашивает «Всё в порядке?»
MAX_PER_SUBJECT = 2                # в плане на день — не больше двух заданий одного предмета
DAILY_TIME_CHOICES = ["15:00", "16:00", "17:00", "18:00"]
REPORT_TIME_CHOICES = ["19:00", "19:30", "20:00", "20:30"]


def tasks_per_day(grade: int) -> int:
    """Играйка (1–4) — 3 задания, Тренер (5–9) — 5, Стратег (10–11) — 6."""
    if grade <= 4:
        return 3
    if grade <= 9:
        return 5
    return 6


def local_now(now_utc: datetime, tz_offset: Optional[int]) -> datetime:
    return now_utc + timedelta(minutes=DEFAULT_TZ_OFFSET if tz_offset is None else tz_offset)


def school_days_between(start: date, end: date) -> int:
    """Сколько будних дней прошло ПОСЛЕ start и ДО end (не включая оба)."""
    days, current = 0, start + timedelta(days=1)
    while current < end:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def plan_actions(profile: Dict[str, Any], now_utc: datetime) -> List[str]:
    """Что отправить ученику прямо сейчас: "daily", "ping" и/или "report" (или ничего)."""
    if not profile.get("grade") or profile.get("notify_enabled") is False or profile.get("notify_paused"):
        return []
    local = local_now(now_utc, profile.get("tz_offset"))
    if local.weekday() >= 5 or not (QUIET_START <= local.hour < QUIET_END):
        return []

    today = local.date().isoformat()
    hhmm = local.strftime("%H:%M")
    daily_time = profile.get("daily_time") or DEFAULT_DAILY_TIME
    report_time = profile.get("report_time") or DEFAULT_REPORT_TIME
    actions: List[str] = []

    if hhmm >= daily_time and hhmm < report_time and profile.get("last_daily_date") != today:
        last_active = _parse_dt(profile.get("last_active_at"))
        silent = 0
        if last_active is not None:
            silent = school_days_between(local_now(last_active, profile.get("tz_offset")).date(), local.date())
        actions.append("ping" if silent >= SILENT_SCHOOL_DAYS else "daily")

    daily_sent_today = profile.get("last_daily_date") == today or "daily" in actions
    if hhmm >= report_time and profile.get("last_report_date") != today and daily_sent_today:
        actions.append("report")
    return actions


PARENT_REPORT_WEEKDAY, PARENT_REPORT_TIME = 6, "18:00"   # воскресенье, 18:00 по времени ученика


def parent_report_due(profile: Dict[str, Any], now_utc: datetime) -> bool:
    """Пора ли отправить родителям недельный отчёт (воскресенье вечером, раз в неделю)."""
    if not profile.get("parents") or profile.get("parents") in ("[]", []):
        return False
    local = local_now(now_utc, profile.get("tz_offset"))
    if local.weekday() != PARENT_REPORT_WEEKDAY or local.strftime("%H:%M") < PARENT_REPORT_TIME or local.hour >= QUIET_END:
        return False
    return profile.get("last_parent_report") != local.date().isoformat()


def build_daily_plan(
    progress: Dict[str, Any], grade: int, subject_ids: List[str], count: int,
    rnd: Optional[random.Random] = None, moment: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Личный план на день: сначала слабые темы и темы «пора повторить», потом новые задания по разным предметам."""
    rnd = rnd or random.Random()
    plan: List[Dict[str, Any]] = []
    per_subject: Dict[str, int] = {}

    for entry in topic_progress.due_topics(progress, grade, moment=moment):
        if len(plan) >= count:
            break
        if entry["subject_id"] not in subject_ids or per_subject.get(entry["subject_id"], 0) >= MAX_PER_SUBJECT:
            continue
        reason = "weak" if entry["status"] == "weak" else "review"
        plan.append({"subject_id": entry["subject_id"], "topic": entry["topic"], "reason": reason})
        per_subject[entry["subject_id"]] = per_subject.get(entry["subject_id"], 0) + 1

    pool = [s for s in subject_ids if per_subject.get(s, 0) < MAX_PER_SUBJECT]
    rnd.shuffle(pool)
    # Новые задания — по предметам, которых ещё нет в плане, затем по остальным
    pool.sort(key=lambda s: per_subject.get(s, 0))
    while len(plan) < count and pool:
        subject = pool.pop(0)
        plan.append({"subject_id": subject, "topic": None, "reason": "new"})
        per_subject[subject] = per_subject.get(subject, 0) + 1
        if per_subject[subject] < MAX_PER_SUBJECT:
            pool.append(subject)
    return plan


def record_activity(activity: Dict[str, Any], day: str, first_try: bool, keep_days: int = 60) -> Dict[str, Any]:
    """Учитывает решённое задание за день: {"2026-11-02": {"solved": 3, "first_try": 2}}."""
    stats = activity.setdefault(day, {"solved": 0, "first_try": 0})
    stats["solved"] += 1
    if first_try:
        stats["first_try"] += 1
    for old in sorted(activity)[:-keep_days]:
        del activity[old]
    return stats


def update_streak(last_study_date: Optional[str], streak: int, today: date) -> int:
    """Серия учебных дней подряд. Выходные серию не прерывают."""
    if not last_study_date:
        return 1
    last = date.fromisoformat(last_study_date)
    if last == today:
        return max(streak, 1)
    return streak + 1 if school_days_between(last, today) == 0 else 1


def reason_note(reason: str, topic: Optional[str]) -> str:
    if reason == "weak" and topic:
        return f"🎯 Тема «{topic}» — по итогам диагностики"
    if reason == "review" and topic:
        return f"🔁 Повторяем тему «{topic}»"
    return "🆕 Новое задание"


def collect_stats(profiles: List[Dict[str, Any]], now_utc: datetime) -> Dict[str, Any]:
    """Статистика для /stats: сколько учеников, кто занимается, сколько решено, сколько слабых тем закреплено."""
    today = local_now(now_utc, DEFAULT_TZ_OFFSET).date()
    week = {(today - timedelta(days=i)).isoformat() for i in range(7)}
    result = {
        "students": len(profiles), "diagnostic_done": 0, "active_today": 0, "active_week": 0,
        "solved_today": 0, "solved_week": 0, "first_try_week": 0,
        "weak_topics": 0, "weak_mastered": 0, "retention_base": 0, "retained": 0,
    }
    for p in profiles:
        if p.get("diagnostic_status") == "done":
            result["diagnostic_done"] += 1
        activity = topic_progress_json(p.get("activity"))
        days = set(activity)
        if today.isoformat() in days:
            result["active_today"] += 1
            result["solved_today"] += activity[today.isoformat()].get("solved", 0)
        if days & week:
            result["active_week"] += 1
        for d in days & week:
            result["solved_week"] += activity[d].get("solved", 0)
            result["first_try_week"] += activity[d].get("first_try", 0)
        for entry in topic_progress_json(p.get("topic_progress")).values():
            if entry.get("was_weak"):
                result["weak_topics"] += 1
                if entry.get("status") == "mastered":
                    result["weak_mastered"] += 1
        # Удержание: ученики, пришедшие 7+ дней назад, — сколько из них занимались спустя неделю после первого дня
        if days:
            first = date.fromisoformat(min(days))
            if (today - first).days >= 7:
                result["retention_base"] += 1
                if any(date.fromisoformat(d) >= first + timedelta(days=7) for d in days):
                    result["retained"] += 1
    return result


def topic_progress_json(value: Any) -> Dict[str, Any]:
    import json
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        data = json.loads(value)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
