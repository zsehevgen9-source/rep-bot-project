"""Плюшки кабинета родителя: «👏 Похвалить», «🎯 Цель недели» с наградой, «💡 Как помочь дома»,
тревожные сигналы и сравнение недель.

Redis:
    goal_<ребёнок>          — цель недели {"target", "prize", "by", "set", "done"} (done — неделя, когда выполнена)
    goal_pending_<родитель> — выбранное число заданий, пока родитель пишет награду
    praise_<родитель>_<реб> — дата последней похвалы (хвалить можно раз в день)
    alerts_off_<родитель>   — "1": тревожные сигналы выключены
    alert_idle_<ребёнок>    — дата последнего занятия, о которой уже предупредили (одно сообщение на один перерыв)
    phelp:<класс>:<предмет>:<тема> — совет «Как помочь дома» (общий для всех родителей, 60 дней)
"""
from datetime import date, timedelta

from app.handlers.tutor.common import *  # noqa: F401,F403
from app.services import lighthouse as lh_rules

PRAISE_POINTS = 3                         # +3 ⭐ (1–4 класс) или +30 ⚡ (5–9 класс)
GOAL_CHOICES = (5, 10, 15, 20, 30)
PRAISES = ["👏 Молодец!", "💪 Горжусь тобой!", "🌟 Ты супер!"]
IDLE_SCHOOL_DAYS = 3                      # сколько учебных дней без занятий — повод написать родителю


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _tasks_word(n: int) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return "заданий"
    return {1: "задание", 2: "задания", 3: "задания", 4: "задания"}.get(n % 10, "заданий")


class FamilyMixin:

    async def _family_payload(self, message: NormalizedMessage, payload: str) -> bool:
        """Кнопки родителя pr_*. True — кнопка обработана."""
        parts = payload.split("_")
        try:
            if payload == "pr_alerts":
                on = await self.toggle_alerts(message.user_id)
                await self.show_parent_menu(message, note="🔔 Сигналы включены." if on else "🔕 Сигналы выключены.")
            elif parts[1] == "praise":
                await self.ask_praise(message, int(parts[2]))
            elif parts[1] == "praisep":
                await self.praise_button(message, int(parts[2]), int(parts[3]))
            elif parts[1] == "goal":
                await self.show_goal(message, int(parts[2]))
            elif parts[1] == "goalt":
                await self.goal_pick_target(message, int(parts[2]), int(parts[3]))
            elif parts[1] == "goalp":
                await self.goal_no_prize(message, int(parts[2]))
            elif parts[1] == "goaldel":
                await self.delete_goal(message, int(parts[2]))
            elif parts[1] == "help":
                await self.show_help_topics(message, int(parts[2]))
            elif parts[1] == "helpt":
                await self.show_help_topic(message, int(parts[2]), int(parts[3]))
            else:
                return False
        except (IndexError, ValueError):
            await self.show_parent_menu(message)
        return True

    # ---------- общее ----------

    async def _parent_guard(self, message: NormalizedMessage, child_uid: int) -> bool:
        if await self._is_parent_of(message.user_id, child_uid):
            return True
        await self.show_parent_menu(message)
        return False

    def _family_rows(self, child_uid: int) -> List[List[Dict[str, str]]]:
        return [[_btn("👏 Похвалить", f"pr_praise_{child_uid}"), _btn("🎯 Цель", f"pr_goal_{child_uid}")],
                [_btn("💡 Как помочь дома", f"pr_help_{child_uid}")],
                [_btn("🗓 Расписание", f"parent_sch_{child_uid}")],
                [_back("parent_menu")]]

    async def _child_today(self, child_uid: int) -> date:
        profile = await self._load_profile(child_uid)
        return daily_rules.local_now(topic_progress.now(), profile.get("tz_offset")).date()

    async def child_weeks(self, child_uid: int, count: int = 4) -> List[Dict[str, Any]]:
        """Последние недели по 7 дней (последняя — сегодняшняя): решено и доля с первой попытки."""
        profile = await self._load_profile(child_uid)
        activity = load_json(profile.get("activity"), {})
        today = await self._child_today(child_uid)
        weeks = []
        for k in range(count - 1, -1, -1):
            end = today - timedelta(days=7 * k)
            days = [(end - timedelta(days=i)).isoformat() for i in range(7)]
            solved = sum(activity.get(d, {}).get("solved", 0) for d in days)
            first = sum(activity.get(d, {}).get("first_try", 0) for d in days)
            weeks.append({"label": f"{(end - timedelta(days=6)).strftime('%d.%m')}–{end.strftime('%d.%m')}",
                          "solved": solved, "accuracy": round(100 * first / solved) if solved else None,
                          "days": sum(1 for d in days if activity.get(d, {}).get("solved"))})
        return weeks

    async def weeks_compare_line(self, child_uid: int) -> str:
        prev, cur = (await self.child_weeks(child_uid, 2))
        if not prev["solved"] and not cur["solved"]:
            return ""
        arrow = lambda a, b: "↑" if a > b else ("↓" if a < b else "=")   # noqa: E731
        line = f"📈 По сравнению с прошлой неделей: решено {cur['solved']} (было {prev['solved']}) {arrow(cur['solved'], prev['solved'])}"
        if cur["accuracy"] is not None and prev["accuracy"] is not None:
            line += f", с первой попытки {cur['accuracy']}% (было {prev['accuracy']}%) {arrow(cur['accuracy'], prev['accuracy'])}"
        return line + "."

    # ---------- 👏 Похвалить ----------

    async def ask_praise(self, message: NormalizedMessage, child_uid: int) -> None:
        if not await self._parent_guard(message, child_uid):
            return
        name = await self._child_name(child_uid)
        rows = [[_btn(p, f"pr_praisep_{child_uid}_{i}")] for i, p in enumerate(PRAISES)]
        rows.append([_back(f"parent_rep_{child_uid}")])
        await self._quiet(message, f"👏 Что сказать: {name}? Ребёнок получит ваши слова и бонусные очки.",
                          keyboard=_kb(rows))

    async def praise_child(self, message: NormalizedMessage, child_uid: int, phrase: int = 0) -> str:
        """Похвала от родителя: сообщение ребёнку и бонус (раз в день). Возвращает текст для родителя."""
        uid = message.user_id
        if not await self._is_parent_of(uid, child_uid):
            return "Этот ребёнок не подключён к вам."
        today = (await self._child_today(child_uid)).isoformat()
        name = await self._child_name(child_uid)
        if await self.redis.get_state(f"praise_{uid}_{child_uid}") == today:
            return f"Сегодня вы уже хвалили: {name} 🙂 Завтра можно снова."
        await self.redis.set_state(f"praise_{uid}_{child_uid}", today, ex=2 * DAY)
        profile = await self._load_profile(child_uid)
        rewards = load_json(profile.get("rewards"), {})
        rewards["points"] = rewards.get("points", 0) + PRAISE_POINTS
        await self._save_profile(child_uid, rewards=json.dumps(rewards, ensure_ascii=False))
        label = rewards_rules.gained_label(PRAISE_POINTS, rewards_rules.mode_for_grade(profile.get("grade")))
        words = PRAISES[phrase] if 0 <= phrase < len(PRAISES) else PRAISES[0]
        await self.api.send_message(user_id=child_uid, text=f"👨‍👩‍👧 Сообщение от родителя: {words}\n"
                                    "Родитель видит, как ты стараешься 💛" + (f"\nБонус: {label}" if label else ""))
        return f"✅ Отправил: {name} получит «{words}»" + (f" и {label}" if label else "") + "."

    async def praise_button(self, message: NormalizedMessage, child_uid: int, phrase: int) -> None:
        note = await self.praise_child(message, child_uid, phrase)
        await self._quiet(message, note, keyboard=_kb(self._family_rows(child_uid)))

    # ---------- 🎯 Цель недели ----------

    async def goal_state(self, child_uid: int) -> Optional[Dict[str, Any]]:
        goal = load_json(await self.redis.get_state(f"goal_{child_uid}"), None)
        if not isinstance(goal, dict) or not goal.get("target"):
            return None
        today = await self._child_today(child_uid)
        activity = load_json((await self._load_profile(child_uid)).get("activity"), {})
        monday = _monday(today)
        solved = sum(activity.get((monday + timedelta(days=i)).isoformat(), {}).get("solved", 0)
                     for i in range((today - monday).days + 1))
        return {"target": goal["target"], "prize": goal.get("prize") or "", "solved": solved,
                "left": max(0, goal["target"] - solved), "done": goal.get("done") == lh_rules.week_key(today),
                "days_left": 6 - today.weekday()}

    async def goal_line(self, child_uid: int) -> str:
        g = await self.goal_state(child_uid)
        if not g:
            return ""
        if g["done"]:
            return "🎯 Цель недели выполнена! 🎉" + (f" Награда: «{g['prize']}»" if g["prize"] else "")
        return (f"🎯 Цель недели: {min(g['solved'], g['target'])} из {g['target']}"
                + (f" · до награды «{g['prize']}» — ещё {g['left']}" if g["prize"] else f" · осталось {g['left']}"))

    async def _goal_check(self, uid: int) -> str:
        """После решённых заданий: цель недели выполнена — поздравляем ребёнка (в ответ) и родителей."""
        g = await self.goal_state(uid)
        if not g or g["done"] or g["solved"] < g["target"]:
            return ""
        goal = load_json(await self.redis.get_state(f"goal_{uid}"), {})
        goal["done"] = lh_rules.week_key(await self._child_today(uid))
        await self.redis.set_state(f"goal_{uid}", json.dumps(goal, ensure_ascii=False))
        name = await self._child_name(uid)
        for parent in await self._parents_of(uid):
            try:
                await self.api.send_message(
                    user_id=parent["user_id"],
                    text=f"🎉 {name} выполнил(а) цель недели: {g['target']} {_tasks_word(g['target'])}!"
                         + (f"\nОбещанная награда: «{g['prize']}» 🎁" if g["prize"] else ""),
                    keyboard=_kb([[_btn("👏 Похвалить", f"pr_praise_{uid}")]]))
            except Exception as e:
                logger.warning("Не удалось поздравить родителя %s: %s", parent.get("user_id"), e)
        return ("\n🎯🎉 Цель недели выполнена!" + (f" Награда от родителя: «{g['prize']}» 🎁" if g["prize"] else "")
                + " Родитель уже знает.")

    async def show_goal(self, message: NormalizedMessage, child_uid: int, note: str = "") -> None:
        if not await self._parent_guard(message, child_uid):
            return
        name = await self._child_name(child_uid)
        g = await self.goal_state(child_uid)
        if g:
            status = (f"Сейчас: {g['target']} {_tasks_word(g['target'])} за неделю"
                      + (f", награда «{g['prize']}»" if g["prize"] else "") + ".\n"
                      + ("✅ На этой неделе уже выполнена!" if g["done"]
                         else f"Решено на этой неделе: {g['solved']}, осталось {g['left']}."))
        else:
            status = "Цель пока не задана."
        rows = _in_rows([_btn(str(n), f"pr_goalt_{child_uid}_{n}") for n in GOAL_CHOICES], 5)
        if g:
            rows.append([_btn("🗑 Убрать цель", f"pr_goaldel_{child_uid}")])
        rows.append([_back(f"parent_rep_{child_uid}")])
        await self._quiet(
            message,
            f"{note}🎯 Цель недели: {name}\n\n{status}\n\n"
            "Выберите, сколько заданий решить за неделю (с понедельника по воскресенье). Потом напишете награду — "
            "например, «поход в кино». Ребёнок увидит цель и прогресс, а когда выполнит — я сообщу вам обоим.",
            keyboard=_kb(rows))

    async def goal_pick_target(self, message: NormalizedMessage, child_uid: int, target: int) -> None:
        uid = message.user_id
        if not await self._parent_guard(message, child_uid) or target not in GOAL_CHOICES:
            return
        await self.redis.set_state(f"goal_pending_{uid}", json.dumps([child_uid, target]), ex=3600)
        await self.redis.set_state(f"awaiting_goal_prize_{uid}", "true", ex=3600)
        await self._quiet(message, f"🎁 Цель: {target} {_tasks_word(target)} за неделю. Какая будет награда?\n\n"
                                   "Напишите сообщением, например: «поход в кино» или «час мультиков».",
                          keyboard=_kb([[_btn("Без награды", f"pr_goalp_{child_uid}")], [_back(f"pr_goal_{child_uid}")]]))

    async def set_goal(self, parent_uid: int, child_uid: int, target: int, prize: str) -> bool:
        """Сохранить цель и сообщить ребёнку. Общая часть для чата и приложения."""
        if not await self._is_parent_of(parent_uid, child_uid) or target not in GOAL_CHOICES:
            return False
        prize = " ".join(str(prize or "").split())[:60]
        goal = {"target": target, "prize": prize, "by": parent_uid,
                "set": (await self._child_today(child_uid)).isoformat(), "done": None}
        old = load_json(await self.redis.get_state(f"goal_{child_uid}"), {})
        if isinstance(old, dict) and old.get("target") == target and old.get("done"):
            goal["done"] = old["done"]                     # ту же цель уже выполнили на этой неделе
        await self.redis.set_state(f"goal_{child_uid}", json.dumps(goal, ensure_ascii=False))
        g = await self.goal_state(child_uid)
        try:
            await self.api.send_message(
                user_id=child_uid,
                text=f"🎯 Родитель поставил цель недели: решить {target} {_tasks_word(target)}."
                     + (f"\n🎁 Награда: «{prize}»!" if prize else "")
                     + f"\nНа этой неделе уже решено: {g['solved']}. Вперёд! 💪")
        except Exception as e:
            logger.warning("Не удалось сообщить ребёнку %s о цели: %s", child_uid, e)
        return True

    async def goal_save_prize(self, message: NormalizedMessage, prize: str) -> None:
        uid = message.user_id
        await self.redis.delete_state(f"awaiting_goal_prize_{uid}")
        pending = load_json(await self.redis.get_state(f"goal_pending_{uid}"), None)
        await self.redis.delete_state(f"goal_pending_{uid}")
        if not pending:
            await self.show_parent_menu(message)
            return
        child_uid, target = int(pending[0]), int(pending[1])
        if not await self.set_goal(uid, child_uid, target, prize):
            await self.show_parent_menu(message)
            return
        await self.show_goal(message, child_uid, note="✅ Цель поставлена — ребёнок уже знает.\n\n")

    async def goal_no_prize(self, message: NormalizedMessage, child_uid: int) -> None:
        pending = load_json(await self.redis.get_state(f"goal_pending_{message.user_id}"), None)
        if not pending or int(pending[0]) != child_uid:
            await self.show_goal(message, child_uid)
            return
        await self.goal_save_prize(message, "")

    async def delete_goal(self, message: NormalizedMessage, child_uid: int) -> None:
        if not await self._parent_guard(message, child_uid):
            return
        await self.redis.delete_state(f"goal_{child_uid}")
        await self.show_goal(message, child_uid, note="🗑 Цель убрана.\n\n")

    # ---------- 💡 Как помочь дома ----------

    async def child_weak_topics(self, child_uid: int) -> List[Dict[str, Any]]:
        profile = await self._load_profile(child_uid)
        grade = profile.get("grade")
        progress = load_json(profile.get("topic_progress"), {})
        weak = [e for e in progress.values() if e.get("grade") == grade and e.get("status") == "weak"]
        weak.sort(key=lambda e: e.get("last_seen") or "", reverse=True)
        return [{"subject_id": e.get("subject_id"), "subject": e.get("subject"), "topic": e.get("topic")}
                for e in weak[:6] if e.get("topic")]

    async def show_help_topics(self, message: NormalizedMessage, child_uid: int) -> None:
        if not await self._parent_guard(message, child_uid):
            return
        weak = await self.child_weak_topics(child_uid)
        name = await self._child_name(child_uid)
        if not weak:
            await self._quiet(message, f"💡 У {name} сейчас нет тем, которые нужно подтягивать 👍\n"
                                       "Когда появятся, здесь будут советы, как помочь дома.",
                              keyboard=_kb(self._family_rows(child_uid)))
            return
        text = (f"💡 Как помочь дома: {name}\n\nВыберите тему — я подскажу простыми словами, как за 10 минут "
                "разобрать её вместе с ребёнком:\n\n" + "\n".join(f"{i + 1}. {w['subject']}: {w['topic']}"
                                                                  for i, w in enumerate(weak)))
        rows = _in_rows([_btn(str(i + 1), f"pr_helpt_{child_uid}_{i}") for i in range(len(weak))], 3)
        rows.append([_back(f"parent_rep_{child_uid}")])
        await self._quiet(message, text, keyboard=_kb(rows))

    async def help_advice(self, parent_uid: int, child_uid: int, subject_id: str, topic: str) -> Optional[str]:
        """Совет родителю по теме (из кеша или от ИИ). None — лимит ИИ на сегодня, "" — ИИ не справился."""
        grade = (await self._load_profile(child_uid)).get("grade")
        if not grade:
            return ""
        key = f"phelp:{grade}:{subject_id}:{topic.lower()}"
        cached = await self.redis.get_state(key)
        if cached:
            return cached
        if not await self._ai_spend(parent_uid):
            return None
        text = await ai_client.parent_help(grade, subject_title(grade, subject_id), topic)
        if not text or text.startswith("Ошибка") or len(text) < 40:
            return ""
        await self.redis.set_state(key, text, ex=60 * DAY)
        return text

    async def show_help_topic(self, message: NormalizedMessage, child_uid: int, index: int) -> None:
        if not await self._parent_guard(message, child_uid):
            return
        weak = await self.child_weak_topics(child_uid)
        if not 0 <= index < len(weak):
            await self.show_help_topics(message, child_uid)
            return
        w = weak[index]
        await self._typing(message)
        text = await self.help_advice(message.user_id, child_uid, w["subject_id"], w["topic"])
        rows = [[_btn("💡 Другая тема", f"pr_help_{child_uid}")], [_back(f"parent_rep_{child_uid}")]]
        if text is None:
            await self._quiet(message, "На сегодня советы закончились — загляните завтра 🙂", keyboard=_kb(rows))
        elif not text:
            await self._quiet(message, "Не получилось составить совет — попробуйте ещё раз.", keyboard=_kb(rows))
        else:
            await self._quiet(message, f"💡 {w['subject']}: «{w['topic']}»\n\n{text}", keyboard=_kb(rows))

    # ---------- 🔔 Тревожные сигналы ----------

    async def alerts_on(self, parent_uid: int) -> bool:
        return await self.redis.get_state(f"alerts_off_{parent_uid}") != "1"

    async def toggle_alerts(self, parent_uid: int) -> bool:
        on = not await self.alerts_on(parent_uid)
        if on:
            await self.redis.delete_state(f"alerts_off_{parent_uid}")
        else:
            await self.redis.set_state(f"alerts_off_{parent_uid}", "1")
        return on

    async def _alert_parents(self, child_uid: int, text: str) -> int:
        sent = 0
        for parent in await self._parents_of(child_uid):
            if not await self.alerts_on(parent["user_id"]):
                continue
            try:
                await self.api.send_message(
                    user_id=parent["user_id"], text=text + "\n\nОтключить такие сообщения: «👨‍👩‍👧 Родительский раздел → 🔔 Сигналы».",
                    keyboard=_kb([[_btn("📊 Отчёт", f"parent_rep_{child_uid}")], [_btn("💡 Как помочь дома", f"pr_help_{child_uid}")]]))
                sent += 1
            except Exception as e:
                logger.warning("Не удалось отправить сигнал родителю %s: %s", parent.get("user_id"), e)
        return sent

    async def _mark_alert(self, uid: int, subject_id: str) -> None:
        """После оценки за урок: две двойки подряд по одному предмету за 2 недели — сразу пишем родителю."""
        profile = await self._load_profile(uid)
        marks = [m for m in load_json(profile.get("lesson_marks"), []) if m.get("subject_id") == subject_id]
        if len(marks) < 2 or marks[-1].get("mark") != 2 or marks[-2].get("mark") != 2:
            return
        today = daily_rules.local_now(topic_progress.now(), profile.get("tz_offset")).date()
        if marks[-2].get("date", "") < (today - timedelta(days=14)).isoformat():
            return
        grade = profile.get("grade")
        await self._alert_parents(
            uid, f"⚠️ {profile.get('name') or 'Ребёнок'}: две двойки подряд по предмету «{subject_title(grade, subject_id)}» "
                 f"(темы «{marks[-2].get('topic')}» и «{marks[-1].get('topic')}»). Стоит разобрать вместе — "
                 "в «💡 Как помочь дома» есть подсказки.")

    async def family_check(self, message: NormalizedMessage) -> None:
        """Вечерняя проверка (вызывает планировщик от имени ребёнка): давно не занимался — пишем родителю один раз."""
        uid = message.user_id
        profile = await self._load_profile(uid)
        last = profile.get("last_study_date")
        if not last or not profile.get("grade"):
            return
        today = daily_rules.local_now(topic_progress.now(), profile.get("tz_offset")).date()
        try:
            last_day = date.fromisoformat(last)
        except ValueError:
            return
        missed = daily_rules.school_days_between(last_day, today) + (1 if today.weekday() < 5 else 0)
        if missed < IDLE_SCHOOL_DAYS or await self.redis.get_state(f"alert_idle_{uid}") == last:
            return
        await self.redis.set_state(f"alert_idle_{uid}", last, ex=60 * DAY)
        await self._alert_parents(uid, f"🔔 {profile.get('name') or 'Ребёнок'} не занимается уже {missed} "
                                       f"{'учебных дня' if missed % 10 in (2, 3, 4) and not 12 <= missed % 100 <= 14 else 'учебных дней'}. Может, напомнить или поставить цель недели с небольшой наградой?")
