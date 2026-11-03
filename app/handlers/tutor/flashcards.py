"""«🃏 Карточки»: наборы для запоминания (термины, даты, формулы) и тренировка с интервальным повторением.

Набор по теме составляет ИИ один раз — и он общий для всех учеников класса (кеш fcdeck:…, 60 дней).
Дальше всё бесплатно: у каждого ученика свои «коробки» и даты повторения (fc_<uid>), тренировка — fc_sess_<uid>.
"""
from app.handlers.tutor.common import *  # noqa: F401,F403
from app.services import flashcards as fc_rules


class FlashcardsMixin:

    async def _fc_store(self, uid: int) -> Dict[str, Any]:
        data = load_json(await self.redis.get_state(f"fc_{uid}"), None)
        return data if isinstance(data, dict) else {"cards": {}, "decks": []}

    async def _fc_save(self, uid: int, store: Dict[str, Any]) -> None:
        await self.redis.set_state(f"fc_{uid}", json.dumps(store, ensure_ascii=False))

    async def _fc_today(self, uid: int):
        return await self._local_date(uid)

    async def fc_due_count(self, uid: int) -> int:
        return len(fc_rules.due_cards(await self._fc_store(uid), await self._fc_today(uid)))

    async def show_flashcards(self, message: NormalizedMessage, note: str = "") -> None:
        uid = message.user_id
        grade = await self._get_grade(uid)
        store = await self._fc_store(uid)
        today = await self._fc_today(uid)
        due = fc_rules.due_cards(store, today)
        total = len(store.get("cards") or {})
        lines = [f"{note}🃏 Карточки для запоминания\n"]
        rows = []
        if total:
            lines.append(f"Всего карточек: {total} · крепко запомнено: {fc_rules.learned(store)}")
            if due:
                lines.append(f"🔁 Пора повторить: {len(due)}")
                rows.append([_btn(f"🔁 Повторить ({len(due)})", "fc_go")])
            else:
                nxt = fc_rules.next_due(store, today)
                lines.append("✅ На сегодня всё повторено!" + (f" Следующее повторение — {_ru_date(nxt)}." if nxt else ""))
            if store.get("decks"):
                lines.append("\nНаборы: " + "; ".join(f"«{d['t'][:40]}»" for d in store["decks"][-6:]))
        else:
            lines.append("Термины, даты, формулы и правила, которые нужно знать наизусть. Ты вспоминаешь ответ, "
                         "переворачиваешь карточку и честно жмёшь «Помню» или «Не помню». Я буду возвращать "
                         "забытое чаще, а выученное — реже: через 1, 3, 7, 14 и 30 дней.")
        rows.append([_btn("➕ Новый набор", "fc_new")])
        rows.append([_back(f"subjects_{grade}" if grade else "back_to_menu")])
        await self._quiet(message, "\n".join(lines), keyboard=_kb(rows))

    async def fc_choose_subject(self, message: NormalizedMessage) -> None:
        grade = await self._get_grade(message.user_id)
        if not grade:
            await self.send_main_menu(message, "")
            return
        buttons = [_btn(button_title(name), f"fc_s_{sid}") for name, sid in SUBJECTS[grade]]
        await self._quiet(message, "🃏 По какому предмету набор?",
                          keyboard=_kb(_fit_rows(buttons, max_per_row=2) + [[_back("fc")]]))

    async def fc_choose_topic(self, message: NormalizedMessage, subject_id: str) -> None:
        uid = message.user_id
        grade = await self._get_grade(uid)
        if not grade or subject_id not in [s for _, s in SUBJECTS[grade]]:
            await self.show_flashcards(message)
            return
        topics = list(dict.fromkeys(reversed(await self.program_passed(uid, grade, subject_id))))[:6]
        await self.redis.set_state(f"fc_opts_{uid}", json.dumps({"subject_id": subject_id, "topics": topics},
                                                                ensure_ascii=False), ex=DAY)
        await self.redis.set_state(f"awaiting_fc_topic_{uid}", subject_id, ex=3600)
        # Темы школьной программы длинные — в тексте списком, на кнопках только номера
        rows = _in_rows([_btn(_NUMS[i], f"fc_t_{i}")
                         for i in range(len(topics))], 3)
        rows.append([_back("fc_new")])
        text = f"🃏 {subject_title(grade, subject_id)}: "
        if topics:
            text += ("выбери тему (последние по программе) или напиши свою сообщением.\n\n"
                     + "\n".join(f"{_NUMS[i]} {t}" for i, t in enumerate(topics)))
        else:
            text += "напиши тему сообщением (например, «Великая Отечественная война» или «Квадратные уравнения»)."
        await self._quiet(message, text, keyboard=_kb(rows))

    async def fc_topic_button(self, message: NormalizedMessage, index: int) -> None:
        opts = load_json(await self.redis.get_state(f"fc_opts_{message.user_id}"), {})
        topics = opts.get("topics") or []
        if not 0 <= index < len(topics):
            await self.show_flashcards(message)
            return
        await self.fc_make_deck(message, opts["subject_id"], topics[index])

    async def fc_make_deck(self, message: NormalizedMessage, subject_id: str, topic: str) -> None:
        uid = message.user_id
        await self.redis.delete_state(f"awaiting_fc_topic_{uid}")
        grade = await self._get_grade(uid)
        topic = " ".join(topic.split())[:100]
        if not grade or len(topic) < 2:
            await self.show_flashcards(message)
            return
        title = subject_title(grade, subject_id)
        cache_key = f"fcdeck:{grade}:{subject_id}:{topic.lower()}"
        cards = load_json(await self.redis.get_state(cache_key), None)
        if not cards:
            if not await self._ai_spend(uid):
                await self._ai_limit_reached(message)
                return
            await self.answer(message, f"🃏 Составляю карточки по теме «{topic}»… ⏳")
            await self._typing(message)
            cards = await ai_client.flashcards(grade, title, topic, fc_rules.DECK_SIZE)
            if not cards:
                await self._quiet(message, "Не получилось составить карточки — попробуй ещё раз или другую тему.",
                                  keyboard=_kb([[_btn("➕ Новый набор", "fc_new")], [_back("fc")]]))
                return
            await self.redis.set_state(cache_key, json.dumps(cards, ensure_ascii=False), ex=60 * DAY)
        store = await self._fc_store(uid)
        added = fc_rules.add_deck(store, cards, title, topic, await self._fc_today(uid))
        await self._fc_save(uid, store)
        note = (f"✅ Набор «{topic}»: {added} новых карточек.\n\n" if added
                else f"Карточки по теме «{topic}» у тебя уже есть.\n\n")
        await self.show_flashcards(message, note)

    # ---------- тренировка ----------

    async def fc_start(self, message: NormalizedMessage) -> None:
        uid = message.user_id
        due = fc_rules.due_cards(await self._fc_store(uid), await self._fc_today(uid))[:fc_rules.SESSION_MAX]
        if not due:
            await self.show_flashcards(message)
            return
        session = {"queue": due, "i": 0, "right": 0, "wrong": 0, "again": []}
        await self._fc_ask(message, session)

    async def _fc_ask(self, message: NormalizedMessage, session: Dict[str, Any]) -> None:
        uid = message.user_id
        store = await self._fc_store(uid)
        while session["i"] < len(session["queue"]) and session["queue"][session["i"]] not in store["cards"]:
            session["i"] += 1
        if session["i"] >= len(session["queue"]):
            await self._fc_finish(message, session)
            return
        await self.redis.set_state(f"fc_sess_{uid}", json.dumps(session), ex=DAY)
        card = store["cards"][session["queue"][session["i"]]]
        head = f"🃏 {session['i'] + 1} из {len(session['queue'])} · {card['s']}"
        await self._quiet(message, f"{head}\n\n❓ {card['f']}\n\nВспомни ответ — потом переверни карточку.",
                          keyboard=_kb([[_btn("👀 Показать ответ", "fc_show")], [_btn("⏹ Хватит", "fc_stop")]]))

    async def fc_show(self, message: NormalizedMessage) -> None:
        uid = message.user_id
        session = load_json(await self.redis.get_state(f"fc_sess_{uid}"), None)
        store = await self._fc_store(uid)
        if not session or session["i"] >= len(session["queue"]) or session["queue"][session["i"]] not in store["cards"]:
            await self.show_flashcards(message, "Эта тренировка уже закончилась.\n\n")
            return
        card = store["cards"][session["queue"][session["i"]]]
        await self._quiet(message, f"❓ {card['f']}\n\n💡 {card['b']}\n\nТы вспомнил?",
                          keyboard=_kb([[_btn("✅ Помню", "fc_yes"), _btn("❌ Не помню", "fc_no")]]))

    async def fc_answer(self, message: NormalizedMessage, remembered: bool) -> None:
        uid = message.user_id
        session = load_json(await self.redis.get_state(f"fc_sess_{uid}"), None)
        store = await self._fc_store(uid)
        if not session or session["i"] >= len(session["queue"]) or session["queue"][session["i"]] not in store["cards"]:
            await self.show_flashcards(message, "Эта тренировка уже закончилась.\n\n")
            return
        cid = session["queue"][session["i"]]
        fc_rules.grade_card(store, cid, remembered, await self._fc_today(uid))
        await self._fc_save(uid, store)
        if remembered:
            session["right"] += 1
        else:
            session["wrong"] += 1
            if cid not in session["again"]:          # забытую — ещё раз в конце тренировки
                session["again"].append(cid)
                session["queue"].append(cid)
        session["i"] += 1
        await self._fc_ask(message, session)

    async def fc_stop(self, message: NormalizedMessage) -> None:
        session = load_json(await self.redis.get_state(f"fc_sess_{message.user_id}"), None)
        if not session:
            await self.show_flashcards(message)
            return
        await self._fc_finish(message, session, stopped=True)

    async def _fc_finish(self, message: NormalizedMessage, session: Dict[str, Any], stopped: bool = False) -> None:
        uid = message.user_id
        await self.redis.delete_state(f"fc_sess_{uid}")
        store = await self._fc_store(uid)
        today = await self._fc_today(uid)
        lines = [f"{'⏹ Тренировка остановлена' if stopped else '🎉 Тренировка готова'}! "
                 f"Помню: {session['right']}, повторим ещё: {session['wrong']}."]
        reward = ""
        if session["right"] + session["wrong"] >= 5:
            reward = await self._count_solved(uid, 1)      # тренировка считается занятием: серия, очки, сундук
        nxt = fc_rules.next_due(store, today)
        left = len(fc_rules.due_cards(store, today))
        if left:
            lines.append(f"На сегодня осталось ещё {left} — можно продолжить.")
        elif nxt:
            lines.append(f"Следующее повторение — {_ru_date(nxt)}: загляни в «🎮 Игры → 🃏 Карточки».")
        lines.append(f"Крепко запомнено: {fc_rules.learned(store)} из {len(store.get('cards') or {})}.")
        rows = [[_btn(f"🔁 Ещё ({left})", "fc_go")]] if left else []
        rows.append([_btn("🃏 Карточки", "fc")])
        grade = await self._get_grade(uid)
        rows.append([_back(f"subjects_{grade}" if grade else "back_to_menu")])
        await self._quiet(message, "\n".join(lines) + reward, keyboard=_kb(rows))


_NUMS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣"]
_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
           "ноября", "декабря"]


def _ru_date(iso: str) -> str:
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{d} {_MONTHS[m - 1]}"
