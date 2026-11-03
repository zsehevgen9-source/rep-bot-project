"""Режимы обучения и награды: «Играйка» (1–4), «Тренер» (5–9), «Стратег» (10–11).

Внутри у всех одна валюта — «очки» (за задания, план на день, диагностику). Показываются по-разному:
- Играйка: звёзды ⭐ и сказочные уровни;
- Тренер: очки опыта ⚡ (очки × 10), уровни и цель на неделю;
- Стратег: без «игрушек» — цель на неделю, точность с первой попытки и закреплённые темы.

Здесь только расчёты (без MAX, базы и Redis).
"""
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

PLAY, COACH, STRATEGY = "play", "coach", "strategy"
MODE_NAMES = {PLAY: "Играйка 🎈", COACH: "Тренер 💪", STRATEGY: "Стратег 🎯"}

POINTS = {"first_try": 2, "hinted": 1, "plan_done": 3, "diag_done": 5, "tutorial": 1}

LEVELS = {
    PLAY: [(0, "Звёздочка ✨"), (20, "Искорка 💫"), (50, "Комета ☄️"), (100, "Звездочёт 🔭"), (200, "Космонавт 🚀")],
    COACH: [(0, "Новичок"), (20, "Ученик"), (50, "Знаток"), (100, "Мастер"), (200, "Эксперт")],
}
WEEKLY_GOAL = {PLAY: 10, COACH: 15, STRATEGY: 25}   # решённых заданий за неделю (пн–вс)

# id: (название, как получить)
ACHIEVEMENTS: Dict[str, Tuple[str, str]] = {
    "first_task": ("🌱 Первый шаг", "реши первое задание"),
    "tutorial": ("🎓 Всё понятно", "пройди обучение"),
    "diag_done": ("🧭 Разведчик", "пройди диагностику"),
    "plan_done": ("📅 Всё по плану", "выполни задания на день"),
    "plans_5": ("🗓 Режим дня", "выполни задания на день 5 раз"),
    "streak_3": ("🔥 Три дня подряд", "занимайся 3 учебных дня подряд"),
    "streak_7": ("🔥 Неделя без пропусков", "занимайся 7 учебных дней подряд"),
    "tasks_10": ("🧠 10 заданий", "реши 10 заданий"),
    "tasks_50": ("🏅 50 заданий", "реши 50 заданий"),
    "tasks_100": ("🏆 100 заданий", "реши 100 заданий"),
    "sniper": ("🎯 Снайпер", "ответь верно с первой попытки 10 раз"),
    "persistent": ("🧗 Упорство", "реши задание после 3 и больше попыток"),
    "mastered_1": ("✅ Первая закреплённая тема", "закрепи тему повторениями"),
    "mastered_10": ("📚 10 закреплённых тем", "закрепи 10 тем"),
    "weak_fixed": ("💪 Победил слабую тему", "закрепи тему, которая была слабой в диагностике"),
    "weekly_goal": ("🥇 Цель недели", "выполни цель на неделю"),
}


def mode_for_grade(grade: Optional[int]) -> str:
    if not grade or grade <= 4:
        return PLAY
    return COACH if grade <= 9 else STRATEGY


def level(points: int, mode: str) -> Tuple[Optional[str], Optional[int]]:
    """Название уровня и сколько очков нужно до следующего (None — максимальный уровень или уровней нет)."""
    table = LEVELS.get(mode)
    if not table:
        return None, None
    name, nxt = table[0][1], None
    for threshold, title in table:
        if points >= threshold:
            name = title
        elif nxt is None:
            nxt = threshold - points
    return name, nxt


def points_label(points: int, mode: str) -> str:
    if mode == PLAY:
        return f"{points} ⭐"
    if mode == COACH:
        return f"{points * 10} ⚡"
    return f"{points} очков"


def gained_label(points: int, mode: str) -> str:
    if mode == PLAY:
        return f"+{points} ⭐"
    if mode == COACH:
        return f"+{points * 10} ⚡"
    return ""


def week_stats(activity: Dict[str, Any], today: date) -> Dict[str, int]:
    """Решено за текущую неделю (с понедельника), с первой попытки и дней с занятиями."""
    monday = today - timedelta(days=today.weekday())
    result = {"solved": 0, "first_try": 0, "days": 0}
    for day, stats in activity.items():
        try:
            d = date.fromisoformat(day)
        except ValueError:
            continue
        if monday <= d <= today and stats.get("solved"):
            result["solved"] += stats.get("solved", 0)
            result["first_try"] += stats.get("first_try", 0)
            result["days"] += 1
    return result


def new_achievements(rewards: Dict[str, Any], facts: Dict[str, Any]) -> List[str]:
    """Какие достижения открылись (и сразу отмечает их в rewards["achievements"])."""
    have = set(rewards.get("achievements") or [])
    conditions = {
        "first_task": rewards.get("solved", 0) >= 1,
        "tutorial": facts.get("tutorial_done", False),
        "diag_done": facts.get("diag_done", False),
        "plan_done": rewards.get("plans", 0) >= 1,
        "plans_5": rewards.get("plans", 0) >= 5,
        "streak_3": facts.get("streak", 0) >= 3,
        "streak_7": facts.get("streak", 0) >= 7,
        "tasks_10": rewards.get("solved", 0) >= 10,
        "tasks_50": rewards.get("solved", 0) >= 50,
        "tasks_100": rewards.get("solved", 0) >= 100,
        "sniper": rewards.get("first_try", 0) >= 10,
        "persistent": rewards.get("persistent", 0) >= 1,
        "mastered_1": facts.get("mastered", 0) >= 1,
        "mastered_10": facts.get("mastered", 0) >= 10,
        "weak_fixed": facts.get("weak_mastered", 0) >= 1,
        "weekly_goal": facts.get("weekly_goal_done", False),
    }
    fresh = [a for a in ACHIEVEMENTS if conditions.get(a) and a not in have]
    rewards["achievements"] = list(have | set(fresh))
    return fresh


def add_points(rewards: Dict[str, Any], event: str) -> int:
    """Начисляет очки за событие и обновляет счётчики. Возвращает, сколько очков начислено."""
    gained = POINTS.get(event, 0)
    rewards["points"] = rewards.get("points", 0) + gained
    if event in ("first_try", "hinted"):
        rewards["solved"] = rewards.get("solved", 0) + 1
        if event == "first_try":
            rewards["first_try"] = rewards.get("first_try", 0) + 1
    elif event == "plan_done":
        rewards["plans"] = rewards.get("plans", 0) + 1
    return gained


def status_line(rewards: Dict[str, Any], mode: str, streak: int, week: Dict[str, int]) -> str:
    """Короткая строка для главного меню (пусто, пока ученик ничего не решил)."""
    if not rewards.get("solved") and not rewards.get("points"):
        return ""
    goal = WEEKLY_GOAL[mode]
    parts = []
    if mode == PLAY:
        name, _ = level(rewards.get("points", 0), mode)
        parts += [points_label(rewards.get("points", 0), mode), name]
    elif mode == COACH:
        name, _ = level(rewards.get("points", 0), mode)
        parts += [points_label(rewards.get("points", 0), mode), f"уровень «{name}»"]
    else:
        accuracy = round(100 * week["first_try"] / week["solved"]) if week["solved"] else None
        if accuracy is not None:
            parts.append(f"точность за неделю {accuracy}%")
    if streak > 1:
        parts.append(f"🔥 {streak}")
    parts.append(f"неделя: {min(week['solved'], goal)}/{goal}")
    return " · ".join(parts)
