"""Свой банк проверенных заданий: всё, что ИИ составил и перепроверил, сохраняется и выдаётся следующим ученикам.

Хранится в Redis (с appendonly — переживает перезапуск):
    bank:<класс>:<предмет>:<тема>        — список заданий и вопросов теста по теме (до MAX_PER_TOPIC)
    bank_topics:<класс>:<предмет>        — какие темы есть в банке
    bank_count                           — сколько всего заданий в банке (для /stats)
    bank_seen_<ученик>                   — что ученик уже видел (чтобы не повторялось)
Вопрос теста: {"id", "q", "options", "answer", "topic", "level"}; задание: {"id", "text", "answer", "options", "topic", "level"}.
"""
import hashlib
import json
import random
from typing import Any, Dict, List, Optional

MAX_PER_TOPIC = 200
MAX_SEEN = 1000


def _norm(topic: Optional[str]) -> str:
    return " ".join((topic or "").lower().replace("ё", "е").split())[:80]


def _item_id(text: str) -> str:
    return hashlib.sha1(text.strip().lower().encode("utf-8")).hexdigest()[:12]


class TaskCache:
    def __init__(self, redis):
        self.redis = redis

    async def _load(self, key: str, default):
        raw = await self.redis.get_state(key)
        try:
            value = json.loads(raw) if raw else default
        except ValueError:
            value = default
        return value if isinstance(value, type(default)) else default

    async def _save(self, key: str, value) -> None:
        await self.redis.set_state(key, json.dumps(value, ensure_ascii=False))

    # ---------- запись ----------

    async def add(self, grade: int, subject_id: str, items: List[Dict[str, Any]], kind: str, level: int) -> int:
        """Сохраняет проверенные задания (kind: "question" — вопрос теста, "task" — задание с ответом). Возвращает, сколько новых."""
        added = 0
        topics = await self._load(f"bank_topics:{grade}:{subject_id}", [])
        for item in items:
            text = item.get("q") or item.get("text") or ""
            topic = _norm(item.get("topic"))
            if not text or not topic or not item.get("answer"):
                continue
            key = f"bank:{grade}:{subject_id}:{topic}"
            stored = await self._load(key, [])
            item_id = _item_id(text)
            if any(s.get("id") == item_id for s in stored):
                continue
            stored.append({**item, "id": item_id, "kind": kind, "level": level})
            await self._save(key, stored[-MAX_PER_TOPIC:])
            if topic not in topics:
                topics.append(topic)
            added += 1
        if added:
            await self._save(f"bank_topics:{grade}:{subject_id}", topics)
            count = int(await self.redis.get_state("bank_count") or 0) + added
            await self.redis.set_state("bank_count", str(count))
        return added

    @staticmethod
    def id_of(text: str) -> str:
        return _item_id(text)

    # ---------- чтение ----------

    async def _seen(self, uid: int) -> List[str]:
        return await self._load(f"bank_seen_{uid}", [])

    async def mark_seen(self, uid: int, item_ids: List[str]) -> None:
        seen = await self._seen(uid)
        seen += [i for i in item_ids if i and i not in seen]
        await self.redis.set_state(f"bank_seen_{uid}", json.dumps(seen[-MAX_SEEN:]))

    async def take(
        self, uid: int, grade: int, subject_id: str, kind: str, count: int, topics: Optional[List[str]] = None,
        level: Optional[int] = None, n_options: Optional[int] = None, rnd: Optional[random.Random] = None,
    ) -> List[Dict[str, Any]]:
        """До count заданий, которые ученик ещё не видел. topics=None — любые темы предмета.
        Если заданий нужного уровня не хватает — берёт и соседних уровней."""
        rnd = rnd or random.Random()
        wanted = [_norm(t) for t in topics] if topics else await self._load(f"bank_topics:{grade}:{subject_id}", [])
        seen = set(await self._seen(uid))
        pool: List[Dict[str, Any]] = []
        for topic in dict.fromkeys(wanted):
            for item in await self._load(f"bank:{grade}:{subject_id}:{topic}", []):
                if item.get("kind") != kind or item.get("id") in seen:
                    continue
                if n_options and len(item.get("options") or []) != n_options:
                    continue
                pool.append(item)
        rnd.shuffle(pool)
        if level is not None:
            pool.sort(key=lambda i: abs((i.get("level") or level) - level))
        # Разные темы — вперемешку: не больше двух вопросов одной темы, пока есть другие
        chosen, per_topic = [], {}
        for item in pool:
            topic = _norm(item.get("topic"))
            if per_topic.get(topic, 0) < 2 or len({_norm(i.get("topic")) for i in pool}) <= 1:
                chosen.append(item)
                per_topic[topic] = per_topic.get(topic, 0) + 1
            if len(chosen) >= count:
                break
        for item in pool:   # тем мало — добираем любыми
            if len(chosen) >= count:
                break
            if item not in chosen:
                chosen.append(item)
        return chosen

    async def size(self) -> int:
        return int(await self.redis.get_state("bank_count") or 0)
