"""«🗼 Мой маяк»: серия дней занятий, заморозка и маяк, который достраивается с каждым днём.

Нейросеть не нужна: картинку рисует Pillow (cards.lighthouse_card), правила — services/lighthouse.py.
Рекорд серии и неделя, когда сработала заморозка, хранятся в Redis: lh_best_<uid>, lh_freeze_<uid>.
"""
from app.handlers.tutor.common import *  # noqa: F401,F403
from app.services import cards
from app.services import lighthouse as lh_rules


class LighthouseMixin:

    async def _bump_streak(self, uid: int, profile: Dict[str, Any], today) -> int:
        """Занятие сегодня: продлевает серию (с заморозкой, если пропущен один день), обновляет рекорд и,
        если достроен новый этап маяка, ставит в очередь картинку (её пришлёт _send_certificates)."""
        old = profile.get("streak_days") or 0
        used_week = await self.redis.get_state(f"lh_freeze_{uid}")
        streak, froze = lh_rules.next_streak(profile.get("last_study_date"), old, today, used_week)
        if froze:
            await self.redis.set_state(f"lh_freeze_{uid}", lh_rules.week_key(today), ex=14 * DAY)
        best = int(await self.redis.get_state(f"lh_best_{uid}") or 0)
        if streak > best:
            await self.redis.set_state(f"lh_best_{uid}", str(streak))
        was = old if (froze or lh_rules.missed_days(profile.get("last_study_date"), today) == 0) else 0
        title = lh_rules.reached(was, streak)
        if title:   # картинку нового этапа пришлём после сообщения (если картинки включены)
            await self.redis.set_state(f"lh_new_{uid}", title, ex=DAY)
        notes = []
        if froze:
            notes.append("🧊 Заморозка спасла твою серию!")
        if title:
            notes.append(f"🗼 Серия {streak} {cards._days(streak)} подряд — на маяке появилось: {title}!")
        await self.redis.set_state(f"lh_note_{uid}", "\n".join(notes), ex=600)
        return streak

    async def _lighthouse_note(self, uid: int) -> str:
        """Строка про маяк к сообщению с наградой (пусто, если ничего нового)."""
        note = await self.redis.get_state(f"lh_note_{uid}")
        if note:
            await self.redis.delete_state(f"lh_note_{uid}")
        return f"\n{note}" if note else ""

    async def _lighthouse_state(self, uid: int) -> Dict[str, Any]:
        profile = await self._load_profile(uid)
        today = daily_rules.local_now(topic_progress.now(), profile.get("tz_offset")).date()
        used_week = await self.redis.get_state(f"lh_freeze_{uid}")
        streak = lh_rules.current_streak(profile.get("last_study_date"), profile.get("streak_days") or 0,
                                         today, used_week)
        best = max(int(await self.redis.get_state(f"lh_best_{uid}") or 0), streak)
        studied_today = profile.get("last_study_date") == today.isoformat()
        at_risk = bool(streak) and not studied_today and lh_rules.missed_days(profile.get("last_study_date"), today) == 1
        return {"streak": streak, "best": best, "freeze": lh_rules.freeze_ready(used_week, today),
                "today": studied_today, "at_risk": at_risk}

    def _lighthouse_text(self, st: Dict[str, Any]) -> str:
        streak = st["streak"]
        lines = [f"🗼 Мой маяк\n\n🔥 Серия: {streak} {cards._days(streak)} подряд · рекорд {st['best']}"
                 if streak else "🗼 Мой маяк\n\nПока на скалах пусто — построим маяк? Позанимайся сегодня, "
                                "и появится фундамент."]
        nxt = lh_rules.next_milestone(streak)
        if streak and nxt:
            lines.append(f"Следующий этап — {nxt[1]}: ещё {nxt[0] - streak} {cards._days(nxt[0] - streak)}.")
        elif streak:
            lines.append("Маяк построен полностью! 🎆")
        if st["at_risk"]:
            lines.append("🧊 Вчера ты пропустил — заморозка держит серию. Реши сегодня хотя бы одно задание!")
        elif streak and not st["today"]:
            lines.append("Сегодня ещё не занимался — реши задание, чтобы серия выросла.")
        elif st["today"]:
            lines.append("Сегодня ты уже занимался ✅")
        lines.append("\nКак это работает: каждый день занятий — новый этаж маяка. Выходные серию не прерывают. "
                     f"Заморозка 🧊 раз в неделю спасает серию, если пропущен один день"
                     f" ({'есть' if st['freeze'] else 'уже потрачена на этой неделе'}).")
        return "\n".join(lines)

    async def show_lighthouse(self, message: NormalizedMessage) -> None:
        uid = message.user_id
        grade = await self._get_grade(uid)
        st = await self._lighthouse_state(uid)
        rows = [[_btn("📅 Позаниматься", "today")]] if not st["today"] else []
        rows.append([_back(f"subjects_{grade}" if grade else "back_to_menu")])
        name = await self.redis.get_state(f"name_{uid}") or "Ученик"
        caption = name + (f", {grade} класс" if grade else "")
        text = self._lighthouse_text(st)
        sent = await self._send_card(message, cards.lighthouse_card, caption, st["streak"], st["best"],
                                     st["freeze"], lh_rules.MILESTONES, text=text, keyboard=_kb(rows))
        if not sent:
            await self._quiet(message, text, keyboard=_kb(rows))

    async def _send_lighthouse_news(self, message: NormalizedMessage) -> None:
        """Новый этап маяка — картинкой после сообщения с наградой (текст уже был в самом сообщении)."""
        uid = message.user_id
        title = await self.redis.get_state(f"lh_new_{uid}")
        if not title:
            return
        await self.redis.delete_state(f"lh_new_{uid}")
        st = await self._lighthouse_state(uid)
        name = await self.redis.get_state(f"name_{uid}") or "Ученик"
        await self._send_card(message, cards.lighthouse_card, name, st["streak"], st["best"], st["freeze"],
                              lh_rules.MILESTONES, keyboard=_kb([[_btn("🗼 Мой маяк", "lh")]]))
