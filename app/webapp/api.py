"""API мини-приложения: те же данные и та же логика, что у бота в чате.

Здесь нет веб-сервера — только методы, которые возвращают словари (их легко проверить тестами).
Веб-сервер (server.py) проверяет подпись MAX, узнаёт пользователя и вызывает эти методы.

Всё делается через обычный обработчик бота (UserHandler): профиль, прогресс, награды, банк заданий,
уровень сложности — ничего не дублируется, а значит, приложение и чат всегда показывают одно и то же.
"""
import asyncio
import json
import logging
import random
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional

from app.handlers.tutor.common import (
    SUBJECTS, _shuffled, daily_rules, load_json, rewards_rules, subject_title, topic_progress,
)
from app.handlers.tutor.subject_test import test_size
from app.schemas.payloads import NormalizedMessage
from app.services import chest as chest_rules
from app.services import flashcards as fc_rules
from app.services import lighthouse as lh_rules
from app.services import lightning as zap_rules
from app.services import schedule as schedule_rules
from app.services.levels import LEVEL_NAMES
from app.services.programs import programs
from app.services.task_utils import normalize_answer

logger = logging.getLogger(__name__)

APP_TEST_TTL = 3 * 3600
ZAP_GRACE = 15          # секунд сверх 60 на доставку ответа «игра закончена» (медленный интернет)
ZAP_QUESTIONS = 40      # вопросов на одну игру (за 60 секунд больше не успеть)
FC_DAY_GOAL = 5         # столько карточек за день — и тренировка засчитывается как занятие
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
GOOD = ("known", "mastered")


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str = ""):
        super().__init__(message)
        self.status, self.message, self.code = status, message, code


class MiniAppAPI:
    """handler_factory(session) → UserHandler; session_factory() → async-контекст с сессией базы."""

    def __init__(self, handler_factory: Callable, session_factory: Callable, bot_username: str = ""):
        self.handler_factory = handler_factory
        self.session_factory = session_factory
        self.bot_username = bot_username
        self._background: set = set()

    @asynccontextmanager
    async def _handler(self):
        async with self.session_factory() as session:
            yield self.handler_factory(session)

    def bot_link(self) -> str:
        return f"https://max.ru/{self.bot_username}" if self.bot_username else ""

    # ---------- главный экран: всё об ученике ----------

    async def state(self, uid: int, child: Optional[int] = None) -> Dict[str, Any]:
        async with self._handler() as h:
            children = await h._children_of(uid)
            kids = []
            for cid in children:
                p = await h._load_profile(cid)
                kids.append({"id": cid, "name": p.get("name") or "Ребёнок", "grade": p.get("grade")})
            target = uid
            if child is not None:
                if child not in children and not await h._is_parent_of(uid, child):
                    raise ApiError(403, "Этот ученик не подключён к вашему аккаунту", "not_parent")
                target = child
            grade = await h._get_grade(target)
            profile = await h._load_profile(target)
            name = profile.get("name") or await h.redis.get_state(f"name_{target}") or ""
            result: Dict[str, Any] = {
                "role": "parent_view" if child is not None else ("student" if grade else ("parent" if kids else "new")),
                "bot_link": self.bot_link(),
                "children": kids,
                "name": name,
                "grade": grade,
            }
            if grade:
                result.update(await self._student(h, target, grade, profile))
            return result

    async def _student(self, h, uid: int, grade: int, profile: Dict[str, Any]) -> Dict[str, Any]:
        mode = rewards_rules.mode_for_grade(grade)
        rewards = load_json(profile.get("rewards"), {})
        points = rewards.get("points", 0)
        facts = await h._reward_facts(uid, profile, grade)
        level_name, to_next = rewards_rules.level(points, mode)
        scale = 10 if mode == rewards_rules.COACH else 1
        have = set(rewards.get("achievements") or [])
        progress = load_json(profile.get("topic_progress"), {})
        today = daily_rules.local_now(topic_progress.now(), profile.get("tz_offset")).date()

        subjects = []
        for title, sid in SUBJECTS[grade]:
            entries = [e for e in progress.values() if e.get("grade") == grade and e.get("subject_id") == sid]
            counts = {s: sum(1 for e in entries if e.get("status") == s) for s in ("known", "mastered", "learning", "weak")}
            subjects.append({
                "id": sid,
                "title": title,
                "good": counts["known"] + counts["mastered"],
                "learning": counts["learning"],
                "weak": counts["weak"],
                "level": LEVEL_NAMES[await h._subject_level(uid, sid)],
                "textbook": await h._textbook(uid, sid),
                "now": programs.current_topics(grade, sid, today)[:3],
                "topics": [
                    {"topic": e["topic"], "status": e.get("status")}
                    for e in sorted(entries, key=lambda e: e.get("last_seen") or "", reverse=True)
                ][:30],
            })

        activity = load_json(profile.get("activity"), {})
        week = []
        for i in range(6, -1, -1):
            day = today - timedelta(days=i)
            stats = activity.get(day.isoformat(), {})
            week.append({"day": WEEKDAYS[day.weekday()], "solved": stats.get("solved", 0),
                         "first_try": stats.get("first_try", 0), "today": i == 0})
        solved = sum(d["solved"] for d in week)
        first_try = sum(d["first_try"] for d in week)

        marks = [
            {"date": m.get("date"), "subject": subject_title(grade, m["subject_id"]), "topic": m.get("topic"),
             "mark": m.get("mark")}
            for m in load_json(profile.get("lesson_marks"), []) if m.get("grade") == grade
        ][-30:][::-1]
        due = [{"subject": e["subject"], "subject_id": e["subject_id"], "topic": e["topic"], "weak": e.get("status") == "weak"}
               for e in topic_progress.due_topics(progress, grade)][:6]
        weak = [{"subject": e["subject"], "subject_id": e["subject_id"], "topic": e["topic"]}
                for e in progress.values() if e.get("grade") == grade and e.get("status") == "weak"][:10]
        lessons = await h.today_lessons(uid)
        lh = await h._lighthouse_state(uid)
        nxt = lh_rules.next_milestone(lh["streak"])
        chest = await h._chest_status(uid)
        album = await h._chest_album(uid)
        games = {
            "lighthouse": {**lh, "built": lh_rules.stage(lh["streak"]), "stages": len(lh_rules.MILESTONES),
                           "next": {"days": nxt[0], "title": nxt[1], "left": nxt[0] - lh["streak"]} if nxt else None},
            "chest": {"ready": chest["ready"], "opened": chest["opened"], "left": chest["left"],
                      "need": chest_rules.CHEST_NEED, "collected": chest_rules.collected(album),
                      "total": len(chest_rules.STICKERS), "title": album.get("title")},
            "zap": {"best": await h._zap_best(uid), "modes": [{"id": m, "title": zap_rules.MODES[m]}
                                                              for m in h._zap_modes(grade)]},
            "cards": {"available": grade >= 5, "due": await h.fc_due_count(uid) if grade >= 5 else 0},
        }
        return {
            "games": games,
            "mode": mode,
            "mode_name": rewards_rules.MODE_NAMES[mode],
            "points": points * scale,
            "unit": {"play": "⭐", "coach": "⚡"}.get(mode, "очков"),
            "level": level_name,
            "to_next": to_next * scale if to_next else None,
            "streak": facts["streak"],
            "week_goal": rewards_rules.WEEKLY_GOAL[mode],
            "week_done": min(facts["week"]["solved"], rewards_rules.WEEKLY_GOAL[mode]),
            "week": week,
            "report": {"days": sum(1 for d in week if d["solved"]), "solved": solved,
                       "accuracy": round(100 * first_try / solved) if solved else None},
            "achievements": [{"title": t, "how": how, "have": key in have}
                             for key, (t, how) in rewards_rules.ACHIEVEMENTS.items()],
            "subjects": subjects,
            "marks": marks,
            "due": due,
            "weak": weak,
            "today": {"weekday": WEEKDAYS[today.weekday()], "date": today.isoformat(),
                      "lessons": [{"id": sid, "title": subject_title(grade, sid)} for sid in lessons]},
        }

    # ---------- расписание ----------

    async def schedule(self, uid: int, child: Optional[int] = None) -> Dict[str, Any]:
        async with self._handler() as h:
            target = await self._target(h, uid, child)
            grade = await self._grade(h, target)
            return {
                "days": schedule_rules.DAYS,
                "subjects": [{"id": sid, "title": title} for title, sid in SUBJECTS[grade]],
                "schedule": await h._schedule(target),
            }

    async def save_schedule(self, uid: int, schedule: Any, child: Optional[int] = None) -> Dict[str, Any]:
        async with self._handler() as h:
            target = await self._target(h, uid, child)
            grade = await self._grade(h, target)
            allowed = {sid for _, sid in SUBJECTS[grade]}
            if not isinstance(schedule, dict):
                raise ApiError(400, "Неверный формат расписания")
            clean: Dict[str, List[str]] = {}
            for day, lessons in schedule.items():
                if str(day) not in {str(i) for i in range(len(schedule_rules.DAYS))} or not isinstance(lessons, list):
                    raise ApiError(400, "Неверный день недели")
                items = [sid for sid in lessons if isinstance(sid, str) and sid in allowed][:10]
                if items:
                    clean[str(day)] = items
            await h._save_schedule(target, clean)
            return {"ok": True, "schedule": clean}

    # ---------- тренажёр: тест по предмету прямо в приложении ----------

    async def train_start(self, uid: int, subject_id: str, topic: Optional[str] = None) -> Dict[str, Any]:
        async with self._handler() as h:
            grade = await self._grade(h, uid)
            if subject_id not in {sid for _, sid in SUBJECTS[grade]}:
                raise ApiError(400, "Такого предмета нет в твоём классе")
            topic = (topic or "").strip()[:80] or None
            count, n_options = test_size(grade)
            level = await h._subject_level(uid, subject_id)
            focus = [topic] if topic else await h._test_focus_topics(uid, grade, subject_id)
            questions = await h.bank.take(uid, grade, subject_id, "question", count,
                                          topics=[topic] if topic else None, level=level, n_options=n_options)
            if len(questions) < count:
                if not await h._ai_spend(uid):
                    raise ApiError(429, "На сегодня новые задания закончились — приходи завтра! "
                                        "А пока можно повторить темы в чате.", "limit")
                questions = await h._generate_questions(uid, grade, subject_id, count, n_options, level, focus,
                                                        "topic" if topic else "default")
            questions = [dict(q) for q in questions]
            if len(questions) < min(3, count):
                raise ApiError(503, "Не получилось составить задания — попробуй ещё раз.", "retry")
            await h.bank.mark_seen(uid, [q.get("id") or h.bank.id_of(q["q"]) for q in questions])
            for q in questions:
                q["options"] = _shuffled(q["options"])
            state = {"grade": grade, "subject_id": subject_id, "level": level, "index": 0, "questions": questions,
                     "answers": [], "topic": topic, "mode": "test", "weekly": False}
            await h.redis.set_state(f"apptest_{uid}", json.dumps(state, ensure_ascii=False), ex=APP_TEST_TTL)
            return {
                "subject": subject_title(grade, subject_id),
                "level": LEVEL_NAMES[level],
                "questions": [{"q": q["q"], "options": q["options"], "topic": q.get("topic")} for q in questions],
            }

    async def train_answer(self, uid: int, index: int, choice: str) -> Dict[str, Any]:
        async with self._handler() as h:
            state = load_json(await h.redis.get_state(f"apptest_{uid}"), None)
            if not isinstance(state, dict) or state.get("done"):
                raise ApiError(409, "Этот тест уже закончился — начни новый.", "expired")
            if index != len(state["answers"]) or index >= len(state["questions"]):
                raise ApiError(409, "Этот вопрос уже отвечен.", "answered")
            q = state["questions"][index]
            if choice not in q["options"]:
                raise ApiError(400, "Такого варианта нет")
            correct = normalize_answer(choice) == normalize_answer(q["answer"])
            state["answers"].append({"chosen": choice, "correct": correct})
            state["index"] = len(state["answers"])
            result: Dict[str, Any] = {"correct": correct, "done": False}
            if state["index"] >= len(state["questions"]):
                result["done"] = True
                result["summary"] = await self._finish(h, uid, state)
            await h.redis.set_state(f"apptest_{uid}", json.dumps(state, ensure_ascii=False), ex=APP_TEST_TTL)
            return result

    async def _finish(self, h, uid: int, state: Dict[str, Any]) -> Dict[str, Any]:
        """Итог теста: прогресс по темам, уровень сложности, очки — как у теста в чате."""
        state["done"] = True
        grade, sid = state["grade"], state["subject_id"]
        title = subject_title(grade, sid)
        total = len(state["answers"])
        right = sum(1 for a in state["answers"] if a["correct"])
        progress = await h._load_progress(uid)
        for q, a in zip(state["questions"], state["answers"]):
            topic = state.get("topic") or q.get("topic")
            if topic:
                topic_progress.record_answer(progress, grade, sid, title, topic, "correct" if a["correct"] else "wrong")
        await h._save_progress(uid, progress)
        before, after = await h._update_level(uid, sid, ratio=right / total) if total >= 3 else (state["level"],) * 2
        reward = (await h._count_test(uid, right)).strip() if right else ""
        # Ошибки можно разобрать в чате с подсказками: кладём тест туда, где его ищет «Разобрать вопрос N»
        chat_test = await h._test_state(uid)
        if not chat_test or chat_test.get("done"):
            await h._save_test_state(uid, state)
        return {
            "right": right, "total": total,
            "wrong": [i for i, a in enumerate(state["answers"]) if not a["correct"]],
            "level_before": LEVEL_NAMES[before], "level_after": LEVEL_NAMES[after],
            "level_change": (after > before) - (after < before),
            "reward": reward,
        }

    # ---------- ⚡ Молния: вопросы сразу пачкой, чтобы игра шла без задержек; счёт проверяет сервер ----------

    async def zap_start(self, uid: int, mode: str) -> Dict[str, Any]:
        async with self._handler() as h:
            grade = await self._grade(h, uid)
            if mode not in h._zap_modes(grade):
                raise ApiError(400, "Такой игры нет для твоего класса")
            questions = [zap_rules.question(mode, grade) for _ in range(ZAP_QUESTIONS)]
            game = random.randint(100000, 999999)
            await h.redis.set_state(f"zapapp_{uid}", json.dumps(
                {"game": game, "mode": mode, "start": topic_progress.now().timestamp(),
                 "answers": [q["answer"] for q in questions]}), ex=600)
            return {"game": game, "mode": mode, "title": zap_rules.MODES[mode], "seconds": zap_rules.GAME_SECONDS,
                    "lives": zap_rules.LIVES, "questions": questions,
                    "best": (await h._zap_best(uid)).get(mode, 0)}

    async def zap_finish(self, uid: int, game: Any, answers: Any) -> Dict[str, Any]:
        async with self._handler() as h:
            state = load_json(await h.redis.get_state(f"zapapp_{uid}"), None)
            if not isinstance(state, dict) or state.get("game") != game:
                raise ApiError(409, "Эта игра уже закончилась — начни новую.", "expired")
            await h.redis.delete_state(f"zapapp_{uid}")
            if not isinstance(answers, list) or not all(isinstance(a, int) for a in answers):
                raise ApiError(400, "Неверный запрос")
            if topic_progress.now().timestamp() - state["start"] > zap_rules.GAME_SECONDS + ZAP_GRACE:
                answers = []                         # прислали слишком поздно — так не бывает у честной игры
            score = wrong = 0
            for given, right in zip(answers, state["answers"]):
                if given == right:
                    score += 1
                else:
                    wrong += 1
                    if wrong >= zap_rules.LIVES:
                        break
            grade = await h._get_grade(uid)
            res = await h._zap_record(uid, state["mode"], score, grade)
            return {"score": score, "wrong": wrong, "best": res["best"], "new_record": res["new_record"],
                    "rank": res["rank"], "reward": res["points_text"]}

    # ---------- 🎁 Сундук дня и альбом ----------

    async def chest_open(self, uid: int) -> Dict[str, Any]:
        async with self._handler() as h:
            grade = await self._grade(h, uid)
            st = await h._chest_status(uid)
            if st["opened"]:
                raise ApiError(409, "Сундук дня уже открыт — новый будет завтра!", "opened")
            if not st["ready"]:
                raise ApiError(409, f"Сундук закрыт: реши ещё {st['left']} — и он откроется!", "locked")
            prize = await h._chest_open(uid, grade, st["today"])
            return {k: prize.get(k) for k in ("kind", "emoji", "name", "rarity", "duplicate", "points_text",
                                              "collected", "total")}

    async def album(self, uid: int) -> Dict[str, Any]:
        async with self._handler() as h:
            album = await h._chest_album(uid)
            have = album.get("stickers") or {}
            return {
                "stickers": [{"id": sid, "emoji": e, "name": n, "rarity": r, "count": have.get(sid, 0)}
                             for sid, (e, n, r) in chest_rules.STICKERS.items()],
                "rarity_names": chest_rules.RARITY_NAMES,
                "titles": album.get("titles") or [], "title": album.get("title"),
                "opened": album.get("opened", 0),
            }

    # ---------- 🃏 Карточки ----------

    async def cards(self, uid: int) -> Dict[str, Any]:
        async with self._handler() as h:
            grade = await self._grade(h, uid)
            store = await h._fc_store(uid)
            today = await h._fc_today(uid)
            due = fc_rules.due_cards(store, today)
            box = store.get("cards") or {}
            return {
                "due": [{"id": cid, "front": box[cid]["f"], "back": box[cid]["b"], "subject": box[cid]["s"],
                         "topic": box[cid]["t"]} for cid in due[:fc_rules.SESSION_MAX]],
                "due_total": len(due), "total": len(box), "learned": fc_rules.learned(store),
                "next_due": fc_rules.next_due(store, today),
                "decks": [d["t"] for d in (store.get("decks") or [])][-8:][::-1],
                "subjects": [{"id": sid, "title": title} for title, sid in SUBJECTS[grade]],
            }

    async def cards_topics(self, uid: int, subject_id: str) -> Dict[str, Any]:
        async with self._handler() as h:
            grade = await self._grade(h, uid)
            if subject_id not in {sid for _, sid in SUBJECTS[grade]}:
                raise ApiError(400, "Такого предмета нет в твоём классе")
            topics = list(dict.fromkeys(reversed(await h.program_passed(uid, grade, subject_id))))[:8]
            return {"topics": topics}

    async def cards_new(self, uid: int, subject_id: str, topic: Any) -> Dict[str, Any]:
        async with self._handler() as h:
            grade = await self._grade(h, uid)
            if grade < 5:
                raise ApiError(400, "Карточки — с 5 класса")
            if subject_id not in {sid for _, sid in SUBJECTS[grade]}:
                raise ApiError(400, "Такого предмета нет в твоём классе")
            topic = " ".join(str(topic or "").split())[:100]
            if len(topic) < 2:
                raise ApiError(400, "Напиши тему")
            added, error = await h._fc_build(uid, grade, subject_id, topic)
            if error == "limit":
                raise ApiError(429, "На сегодня новые наборы закончились — приходи завтра!", "limit")
            if error:
                raise ApiError(503, "Не получилось составить карточки — попробуй ещё раз или другую тему.", "retry")
            return {"added": added, "topic": topic}

    async def cards_grade(self, uid: int, card: Any, remembered: Any) -> Dict[str, Any]:
        async with self._handler() as h:
            store = await h._fc_store(uid)
            if not isinstance(card, str) or card not in (store.get("cards") or {}) or not isinstance(remembered, bool):
                raise ApiError(400, "Неверный запрос")
            today = await h._fc_today(uid)
            fc_rules.grade_card(store, card, remembered, today)
            await h._fc_save(uid, store)
            # 5 карточек за день = занятие (серия маяка, очки, сундук) — один раз в день
            day = load_json(await h.redis.get_state(f"fcapp_{uid}"), {})
            if day.get("date") != today.isoformat():
                day = {"date": today.isoformat(), "n": 0, "done": False}
            day["n"] += 1
            reward = ""
            if day["n"] >= FC_DAY_GOAL and not day["done"]:
                day["done"] = True
                reward = (await h._count_solved(uid, 1)).strip()
            await h.redis.set_state(f"fcapp_{uid}", json.dumps(day), ex=2 * 86400)
            return {"ok": True, "box": store["cards"][card]["box"], "due": store["cards"][card]["due"],
                    "reward": reward, "learned": fc_rules.learned(store)}

    # ---------- действия в чате: бот продолжает разговор там ----------

    CHAT_ACTIONS = ("lesson", "ask_topic", "fix", "homework", "today")

    async def chat_action(self, uid: int, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Запускает действие бота в чате (урок по теме, разбор ошибки, помощь с домашкой)
        и сразу отвечает: само сообщение бот пришлёт в чат через несколько секунд."""
        if action not in self.CHAT_ACTIONS:
            raise ApiError(400, "Неизвестное действие")
        async with self._handler() as h:
            grade = await self._grade(h, uid)
            sid = params.get("subject_id")
            if action in ("lesson", "ask_topic") and sid not in {s for _, s in SUBJECTS[grade]}:
                raise ApiError(400, "Такого предмета нет в твоём классе")
            if action == "lesson" and not str(params.get("topic") or "").strip():
                raise ApiError(400, "Не указана тема")
            if action == "fix":
                state = await h._test_state(uid)
                n = params.get("n")
                if not state or not state.get("done") or not isinstance(n, int) or not 0 <= n < len(state["answers"]):
                    raise ApiError(409, "Этот тест уже устарел.", "expired")
            chat_id = (await h._load_profile(uid)).get("chat_id")

        async def run() -> None:
            try:
                async with self._handler() as h2:
                    message = NormalizedMessage(chat_id=chat_id, user_id=uid, message_id="", text=None, payload=None)
                    await h2._reset_modes(uid)
                    if action == "lesson":
                        await h2.start_lesson(message, grade, sid, str(params["topic"]).strip())
                    elif action == "ask_topic":
                        await h2.ask_lesson_topic(message, sid)
                    elif action == "fix":
                        await h2.fix_test_question(message, params["n"])
                    elif action == "homework":
                        await h2.ask_homework(message)
                    elif action == "today":
                        await h2.send_daily(message)
            except Exception:
                logger.exception("Мини-приложение: не удалось выполнить действие %s", action)

        task = asyncio.create_task(run())
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return {"ok": True, "bot_link": self.bot_link()}

    # ---------- общее ----------

    async def _target(self, h, uid: int, child: Optional[int]) -> int:
        if child is None:
            return uid
        if not await h._is_parent_of(uid, child):
            raise ApiError(403, "Этот ученик не подключён к вашему аккаунту", "not_parent")
        return child

    async def _grade(self, h, uid: int) -> int:
        grade = await h._get_grade(uid)
        if not grade:
            raise ApiError(409, "Сначала выбери класс в чате с Маячком.", "no_grade")
        return grade
