"""Карточки для запоминания с интервальным повторением (система Лейтнера).

Карточка живёт в «коробке» 0–5. «Помню» — в следующую коробку, повтор через 1, 3, 7, 14, 30 дней.
«Не помню» — снова в коробку 0 и ещё раз в этой же тренировке. Так бот чаще показывает то, что ученик забывает.
Здесь только правила — без MAX, Redis и ИИ.
"""
import hashlib
import re
from datetime import date, timedelta
from typing import Any, Dict, List

INTERVALS = [1, 3, 7, 14, 30]       # через сколько дней повторить после «Помню» из коробки 0, 1, 2, 3, 4+
SESSION_MAX = 12                     # карточек за одну тренировку
MAX_CARDS = 400                      # всего карточек у ученика
DECK_SIZE = 8


def card_id(front: str) -> str:
    return hashlib.sha1(front.strip().lower().encode("utf-8")).hexdigest()[:10]


def add_deck(store: Dict[str, Any], cards: List[Dict[str, str]], subject: str, topic: str, today: date) -> int:
    """Добавляет карточки набора (повторы не дублируются). Возвращает, сколько новых."""
    box = store.setdefault("cards", {})
    added = 0
    for c in cards:
        cid = card_id(c["front"])
        if cid in box:
            continue
        box[cid] = {"f": c["front"], "b": c["back"], "s": subject, "t": topic, "box": 0, "due": today.isoformat()}
        added += 1
    if len(box) > MAX_CARDS:   # самые «выученные» (дальняя коробка, позднее повторение) убираем первыми
        for cid in sorted(box, key=lambda k: (-box[k]["box"], box[k]["due"]))[:len(box) - MAX_CARDS]:
            del box[cid]
    decks = store.setdefault("decks", [])
    if topic not in [d["t"] for d in decks]:
        decks.append({"s": subject, "t": topic})
    return added


def due_cards(store: Dict[str, Any], today: date) -> List[str]:
    cards = store.get("cards") or {}
    due = [cid for cid, c in cards.items() if c["due"] <= today.isoformat()]
    return sorted(due, key=lambda k: (cards[k]["box"], cards[k]["due"]))


def next_due(store: Dict[str, Any], today: date):
    later = [c["due"] for c in (store.get("cards") or {}).values() if c["due"] > today.isoformat()]
    return min(later) if later else None


def grade_card(store: Dict[str, Any], cid: str, remembered: bool, today: date) -> None:
    card = store["cards"][cid]
    if remembered:
        days = INTERVALS[min(card["box"], len(INTERVALS) - 1)]
        card["box"] = min(card["box"] + 1, len(INTERVALS))
        card["due"] = (today + timedelta(days=days)).isoformat()
    else:
        card["box"] = 0
        card["due"] = today.isoformat()


def learned(store: Dict[str, Any]) -> int:
    """Сколько карточек уже крепко запомнено (коробка 3 и дальше — помнит неделю и больше)."""
    return sum(1 for c in (store.get("cards") or {}).values() if c["box"] >= 3)


def parse_flashcards(raw: str) -> list:
    """«КАРТОЧКА: вопрос || ответ» → [{"front", "back"}] (кривые строки пропускаем)."""
    cards, seen = [], set()
    for line in (raw or "").splitlines():
        line = line.strip().lstrip("-•0123456789. )").strip()
        if "||" not in line:
            continue
        line = re.sub(r"(?i)^карточка\s*\d*\s*[:.)-]\s*", "", line)
        front, back = (x.strip().strip("*").strip() for x in line.split("||", 1))
        if not front or not back or len(front) > 160 or len(back) > 240 or front.lower() in seen:
            continue
        seen.add(front.lower())
        cards.append({"front": front, "back": back})
    return cards
