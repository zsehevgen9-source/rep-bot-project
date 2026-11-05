"""Режим для родителя: подключение по коду, недельный отчёт, расписание за ребёнка.

Основной способ (как ученик вступает в класс учителя): родитель нажимает «👨‍👩‍👧 Я родитель» и получает
семейный код (6 цифр, действует 7 дней, подходит для нескольких детей). Ребёнок вводит его:
«⚙️ Настройки → 👨‍👩‍👧 Родитель → 🔑 Ввести код родителя» или /family 123456.
Запасной способ: ребёнок показывает свой код (сутки), родитель пишет боту /parent 123456.
Родитель видит отчёт о ребёнке, может внести расписание. Каждое воскресенье в 18:00 родителю приходит отчёт за неделю.
Ребёнок всегда видит, кто подключён, и может отключить родителя.
"""
from datetime import timedelta

from app.handlers.tutor.common import *  # noqa: F401,F403
from app.services import cards
from app.services import schedule as schedule_rules

CODE_TTL = DAY
FAMILY_TTL = 7 * DAY
MAX_PARENTS = 3


class ParentMixin:
    # ---------- данные ----------

    async def _parents_of(self, child_uid: int) -> List[Dict[str, Any]]:
        data = load_json((await self._load_profile(child_uid)).get("parents"), [])
        return data if isinstance(data, list) else []

    async def _children_of(self, parent_uid: int) -> List[int]:
        """Дети родителя: из Redis, а если там пусто (Redis очищен) — из базы."""
        cached = load_json(await self.redis.get_state(f"children_{parent_uid}"), None)
        if isinstance(cached, list):
            # Проверяем по профилю ребёнка: он мог удалить данные или отключить родителя — тогда его здесь нет
            actual = [c for c in cached if await self._is_parent_of(parent_uid, c)]
            if actual != cached:
                await self.redis.set_state(f"children_{parent_uid}", json.dumps(actual), ex=GRADE_TTL)
            return actual
        children = []
        for profile in await self.profiles.all_profiles():
            if any(p.get("user_id") == parent_uid for p in load_json(profile.get("parents"), []) or []):
                children.append(profile["user_id"])
        await self.redis.set_state(f"children_{parent_uid}", json.dumps(children), ex=GRADE_TTL)
        return children

    async def _child_name(self, child_uid: int) -> str:
        return (await self._load_profile(child_uid)).get("name") or "ребёнок"

    # ---------- ученик: код для родителя ----------

    async def show_parent_code(self, message: NormalizedMessage, note: str = "") -> None:
        uid = message.user_id
        code = await self._child_code(uid)
        parents = await self._parents_of(uid)
        grade = await self._get_grade(uid)
        rows = [[_btn("🔑 Ввести код родителя", "fam_enter")]]
        rows += [[_btn(f"🔕 Отключить {n}", f"parent_unlink_{p['user_id']}")] for n, p in enumerate(parents, 1)]
        if len(parents) > 1:
            rows.append([_btn("🔕 Отключить всех", "parent_unlink_all")])
        rows.append([_back(f"settings_{grade}" if grade else "back_to_menu")])
        if parents:
            status = f"Сейчас подключено родителей: {len(parents)}.\n" + "\n".join(
                f"{n}. Родитель" + (f" — подключён {p['since']}" if p.get("since") else "")
                for n, p in enumerate(parents, 1)) + "\nЛишнего можно отключить кнопкой с его номером."
        else:
            status = "Пока никто не подключён."
        await self._send_with_voice(
            message,
            f"{note}👨‍👩‍👧 Родители\n\n"
            "Мама или папа нажимает в Маячке «👨‍👩‍👧 Я родитель» и получает код. "
            "Нажми «🔑 Ввести код родителя» и напиши его — готово!\n\n"
            f"Или наоборот: покажи родителю свой код. Код: {code}\n"
            f"Родитель открывает этого бота в MAX и пишет: /parent {code}\n"
            "(твой код действует сутки)\n\n"
            "Родитель будет видеть, как ты занимаешься, и получать по воскресеньям короткий отчёт.\n\n" + status,
            keyboard=_kb(rows),
            voice_text="Попроси маму или папу нажать в Маячке «Я родитель» — они получат код. "
                       "Нажми «Ввести код родителя» и напиши его.",
        )

    async def _child_code(self, uid: int) -> str:
        code = await self.redis.get_state(f"pcode_of_{uid}")
        if not code or await self.redis.get_state(f"pcode_{code}") != str(uid):
            code = f"{random.randint(0, 999999):06d}"
            await self.redis.set_state(f"pcode_{code}", str(uid), ex=CODE_TTL)
            await self.redis.set_state(f"pcode_of_{uid}", code, ex=CODE_TTL)
        return code

    # ---------- ученик вводит код родителя (как код класса учителя) ----------

    async def ask_family_code(self, message: NormalizedMessage) -> None:
        uid = message.user_id
        if not await self._get_grade(uid):
            await self.show_parent_menu(message)
            return
        await self.redis.set_state(f"awaiting_family_code_{uid}", "true", ex=3600)
        await self._quiet(message, "🔑 Напиши код родителя — 6 цифр.\n\n"
                                   "Родитель видит его в Маячке, в разделе «👨‍👩‍👧 Я родитель».",
                          keyboard=_kb([[_back("parent_code")]]))

    async def join_family(self, message: NormalizedMessage, code: str) -> None:
        """Ученик подключает родителя по семейному коду."""
        uid = message.user_id
        await self.redis.delete_state(f"awaiting_family_code_{uid}")
        digits = "".join(ch for ch in code if ch.isdigit())
        if not await self._get_grade(uid):
            if await self._children_of(uid) or await self.redis.get_state(f"is_parent_{uid}"):
                await self.show_parent_menu(message, note="🙂 Этот код вводит ребёнок на своём телефоне.")
            else:
                await self.answer(message, "Код родителя вводит ученик 🙂 Сначала зарегистрируйся: /start")
            return
        data = load_json(await self.redis.get_state(f"fcode_{digits}"), None) if len(digits) == 6 else None
        parent_uid = data.get("uid") if isinstance(data, dict) else None
        if not parent_uid or await self.redis.get_state(f"fcode_of_{parent_uid}") != digits:
            await self.redis.set_state(f"awaiting_family_code_{uid}", "true", ex=3600)
            await self._quiet(message, "Код не подошёл 🙈 Проверь цифры и напиши ещё раз. Свежий код родитель "
                                       "найдёт в Маячке: «👨‍👩‍👧 Я родитель».",
                              keyboard=_kb([[_back("parent_code")]]))
            return
        if parent_uid == uid:
            await self.answer(message, "Это твой собственный код 🙂 Его вводит ребёнок на своём телефоне.")
            return
        await self._attach_parent(parent_uid, data.get("chat_id"), uid)
        name = await self._child_name(uid)
        await self.api.send_message(
            user_id=parent_uid, text=f"✅ {name} подключил(а) вас к своим занятиям. "
                                     "Отчёт можно открыть в родительском разделе, а за неделю я пришлю его сам в воскресенье.",
            keyboard=_kb([[_btn("👨‍👩‍👧 Родительский раздел", "parent_menu")]]))
        await self.show_parent_code(message, note="✅ Готово! Родитель подключён — он видит твои занятия.\n\n")

    async def _attach_parent(self, parent_uid: int, parent_chat: Optional[int], child_uid: int) -> None:
        parents = [p for p in await self._parents_of(child_uid) if p.get("user_id") != parent_uid]
        since = topic_progress.now().strftime("%d.%m.%Y")
        parents = (parents + [{"user_id": parent_uid, "chat_id": parent_chat, "since": since}])[-MAX_PARENTS:]
        await self._save_profile(child_uid, parents=json.dumps(parents))
        children = [c for c in await self._children_of(parent_uid) if c != child_uid] + [child_uid]
        await self.redis.set_state(f"children_{parent_uid}", json.dumps(children), ex=GRADE_TTL)

    async def unlink_parent(self, message: NormalizedMessage, parent_uid: int) -> None:
        """Ученик отключает одного родителя."""
        uid = message.user_id
        parents = await self._parents_of(uid)
        if any(p.get("user_id") == parent_uid for p in parents):
            await self._save_profile(uid, parents=json.dumps([p for p in parents if p.get("user_id") != parent_uid]))
            await self._forget_child(parent_uid, uid)
            await self.api.send_message(user_id=parent_uid,
                                        text=f"{await self._child_name(uid)} отключил(а) ваш родительский доступ.")
            await self.answer(message, "✅ Родитель отключён: он больше не видит твои занятия.")
        await self.show_parent_code(message)

    async def _forget_child(self, parent_uid: int, child_uid: int) -> None:
        children = [c for c in await self._children_of(parent_uid) if c != child_uid]
        await self.redis.set_state(f"children_{parent_uid}", json.dumps(children), ex=GRADE_TTL)

    async def forget_family(self, uid: int) -> None:
        """Перед «Удалить мои данные»: ученик отключается от родителей, родитель — от детей (обе стороны узнают)."""
        name = await self._child_name(uid)
        for parent in await self._parents_of(uid):
            await self._forget_child(parent["user_id"], uid)
            try:
                await self.api.send_message(user_id=parent["user_id"],
                                            text=f"{name} удалил(а) свои данные в боте — родительский доступ отключён.")
            except Exception as e:
                logger.warning("Не удалось сообщить родителю %s: %s", parent.get("user_id"), e)
        for child_uid in await self._children_of(uid):
            parents = await self._parents_of(child_uid)
            await self._save_profile(child_uid, parents=json.dumps([p for p in parents if p.get("user_id") != uid]))
            try:
                await self.api.send_message(user_id=child_uid, text="👨‍👩‍👧 Родитель отключился от твоих занятий.")
            except Exception as e:
                logger.warning("Не удалось сообщить ученику %s: %s", child_uid, e)

    async def unlink_all_parents(self, message: NormalizedMessage) -> None:
        uid = message.user_id
        for parent in await self._parents_of(uid):
            await self.redis.delete_state(f"children_{parent['user_id']}")
            await self.api.send_message(user_id=parent["user_id"],
                                        text=f"{await self._child_name(uid)} отключил(а) родительский доступ.")
        await self._save_profile(uid, parents=json.dumps([]))
        grade = await self._get_grade(uid)
        if grade:
            await self.show_settings(message, grade)

    # ---------- родитель ----------

    async def family_code(self, uid: int, chat_id: Optional[int] = None) -> str:
        """Семейный код родителя (для детей): 6 цифр на 7 дней. Заодно запоминаем, что человек — родитель."""
        code = await self.redis.get_state(f"fcode_of_{uid}")
        data = load_json(await self.redis.get_state(f"fcode_{code}"), None) if code else None
        if not isinstance(data, dict) or data.get("uid") != uid:
            for _ in range(20):
                code = f"{random.randint(0, 999999):06d}"
                if not await self.redis.get_state(f"fcode_{code}"):
                    break
            await self.redis.set_state(f"fcode_{code}", json.dumps({"uid": uid, "chat_id": chat_id}), ex=FAMILY_TTL)
            await self.redis.set_state(f"fcode_of_{uid}", code, ex=FAMILY_TTL)
        await self.redis.set_state(f"is_parent_{uid}", "1", ex=GRADE_TTL)
        return code

    async def ask_parent_code(self, message: NormalizedMessage) -> None:
        uid = message.user_id
        await self.redis.delete_state(f"awaiting_name_{uid}")
        await self.redis.set_state(f"awaiting_parent_code_{uid}", "true", ex=3600)
        code = await self.family_code(uid, message.chat_id)
        await self._send_with_voice(
            message,
            "👨‍👩‍👧 Здравствуйте! Подключим вас к занятиям ребёнка.\n\n"
            f"🔑 Ваш код: {code}\n\n"
            "Пусть ребёнок откроет Маячка и введёт этот код: «⚙️ Настройки → 👨‍👩‍👧 Родитель → 🔑 Ввести код родителя» "
            f"(или напишет боту /family {code}). Код действует 7 дней — по нему можно подключить нескольких детей. "
            "Когда ребёнок подключится, я сразу напишу.\n\n"
            "Если ребёнок уже показал вам свой код — просто введите код ребёнка сюда, 6 цифр.\n\n"
            "Подключаясь, вы как родитель соглашаетесь на обработку данных ребёнка для учёбы — подробно: /privacy",
            keyboard=None if await self._children_of(uid) else _kb([[_btn("↩️ Я не родитель", "parent_cancel")]]),
            voice_text="Здравствуйте! Продиктуйте ребёнку ваш код. Он введёт его у себя в настройках, "
                       "в разделе «Родитель», и вы увидите его занятия.",
        )

    async def cancel_parent(self, uid: int) -> bool:
        """«Я не родитель» (нажал по ошибке): забываем код и отметку родителя, если детей ещё нет."""
        if await self._children_of(uid):
            return False
        code = await self.redis.get_state(f"fcode_of_{uid}")
        if code:
            await self.redis.delete_state(f"fcode_{code}")
        for key in ("fcode_of", "is_parent", "awaiting_parent_code"):
            await self.redis.delete_state(f"{key}_{uid}")
        return True

    async def link_parent(self, message: NormalizedMessage, code: str) -> None:
        uid = message.user_id
        code = "".join(ch for ch in code if ch.isdigit())
        child = await self.redis.get_state(f"pcode_{code}") if len(code) == 6 else None
        if not child:
            await self.answer(message, "Код не подошёл 🙈 Проверьте цифры. Проще всего — продиктуйте ребёнку ваш код "
                                       "(нажмите «👨‍👩‍👧 Я родитель»), он введёт его у себя в настройках.")
            return
        child_uid = int(child)
        if child_uid == uid:
            await self.answer(message, "Это ваш собственный код 🙂 Его нужно ввести с телефона родителя.")
            return
        await self.redis.delete_state(f"awaiting_parent_code_{uid}")
        await self._attach_parent(uid, message.chat_id, child_uid)
        await self.redis.set_state(f"is_parent_{uid}", "1", ex=GRADE_TTL)
        name = await self._child_name(child_uid)
        # Ребёнок всегда знает, кто видит его занятия
        await self.api.send_message(user_id=child_uid, text="👨‍👩‍👧 К твоим занятиям подключился родитель. "
                                    "Отключить можно в «⚙️ Настройки → 👨‍👩‍👧 Родитель».")
        await self.show_parent_menu(message, note=f"✅ Готово! Вы подключены к занятиям: {name}.")

    async def show_parent_menu(self, message: NormalizedMessage, note: str = "") -> None:
        uid = message.user_id
        children = await self._children_of(uid)
        if not children:
            await self.ask_parent_code(message)
            return
        lines, rows = [], []
        for child_uid in children[:MAX_PARENTS]:
            profile = await self._load_profile(child_uid)
            name = profile.get("name") or "ребёнок"
            lines.append(f"• {name}" + (f", {profile.get('grade')} класс" if profile.get("grade") else ""))
            label = name if _vis_len(name) <= 8 else name[:7] + "."
            rows.append([_btn(f"📊 Отчёт · {label}", f"parent_rep_{child_uid}")])
            rows.append([_btn(f"🗓 Расписание · {label}", f"parent_sch_{child_uid}")])
            rows.append([_btn(f"🚫 Отключить · {label}", f"parent_leave_{child_uid}")])
        rows.append([_btn("➕ Ещё ребёнок", "parent_start")])
        text = (f"{note}\n\n" if note else "") + "👨‍👩‍👧 Родительский раздел\n\n" + "\n".join(lines) + (
            "\n\n📊 — отчёт о занятиях, 🗓 — внести расписание уроков за ребёнка.\n"
            "Отчёт за неделю я пришлю сам в воскресенье в 18:00.")
        await self.answer(message, text, keyboard=_kb(rows))

    async def ask_leave_child(self, message: NormalizedMessage, child_uid: int) -> None:
        if not await self._is_parent_of(message.user_id, child_uid):
            await self.show_parent_menu(message)
            return
        name = await self._child_name(child_uid)
        await self.answer(
            message,
            f"Отключиться от занятий: {name}? Отчёты приходить перестанут. Подключиться снова можно по коду.",
            keyboard=_kb([[_btn("✅ Да, отключиться", f"parent_leave_yes_{child_uid}")], [_back("parent_menu")]]),
        )

    async def leave_child(self, message: NormalizedMessage, child_uid: int) -> None:
        """Родитель сам отключается от ребёнка."""
        uid = message.user_id
        parents = await self._parents_of(child_uid)
        if not any(p.get("user_id") == uid for p in parents):
            await self._forget_child(uid, child_uid)
            await self.show_parent_menu(message)
            return
        await self._save_profile(child_uid, parents=json.dumps([p for p in parents if p.get("user_id") != uid]))
        await self.api.send_message(user_id=child_uid, text="👨‍👩‍👧 Родитель отключился от твоих занятий.")
        await self._forget_child(uid, child_uid)
        note = f"✅ Вы отключились от занятий: {await self._child_name(child_uid)}."
        if await self._children_of(uid):
            await self.show_parent_menu(message, note=note)
        else:
            await self.answer(message, note + " Чтобы подключиться снова, напишите /parent — я дам код для ребёнка.")

    async def _is_parent_of(self, parent_uid: int, child_uid: int) -> bool:
        return any(p.get("user_id") == parent_uid for p in await self._parents_of(child_uid))

    async def parent_report_button(self, message: NormalizedMessage, child_uid: int) -> None:
        if not await self._is_parent_of(message.user_id, child_uid):
            await self.show_parent_menu(message)
            return
        text = await self.build_parent_report(child_uid)
        await self._parent_card(message, child_uid)
        await self.answer(message, text, keyboard=_kb([[_back("parent_menu")]]))

    async def build_parent_report(self, child_uid: int) -> str:
        """Отчёт за последние 7 дней: занятия, оценки, слабые темы. Без ИИ — только факты."""
        profile = await self._load_profile(child_uid)
        name = profile.get("name") or "Ребёнок"
        grade = profile.get("grade")
        today = daily_rules.local_now(topic_progress.now(), profile.get("tz_offset")).date()
        week = [(today - timedelta(days=i)).isoformat() for i in range(7)]
        activity = load_json(profile.get("activity"), {})
        solved = sum(activity.get(d, {}).get("solved", 0) for d in week)
        first_try = sum(activity.get(d, {}).get("first_try", 0) for d in week)
        days = sum(1 for d in week if activity.get(d, {}).get("solved"))
        lines = [f"📊 {name}" + (f", {grade} класс" if grade else "") + " — неделя "
                 f"{(today - timedelta(days=6)).strftime('%d.%m')}–{today.strftime('%d.%m')}", ""]
        if solved:
            lines.append(f"📅 Занимался(ась) {days} {_days_word(days)} из 7, решено заданий: {solved}, "
                         f"с первой попытки: {first_try} ({round(100 * first_try / solved)}%).")
        else:
            lines.append("📅 На этой неделе занятий не было.")
        streak = profile.get("streak_days") or 0
        if streak > 1:
            lines.append(f"🔥 Серия: {streak} учебных дней подряд.")
        marks = [m for m in load_json(profile.get("lesson_marks"), []) if m.get("date") in week]
        if marks:
            lines.append("")
            lines.append("📒 Оценки за уроки:")
            for m in marks[-8:]:
                lines.append(f"• {subject_title(grade, m['subject_id'])}, «{m['topic']}» — {m['mark']}")
        progress = load_json(profile.get("topic_progress"), {})
        weak = [e for e in progress.values() if e.get("grade") == grade and e.get("status") == "weak"]
        mastered = [e for e in progress.values() if e.get("grade") == grade and e.get("status") == "mastered"]
        if mastered:
            lines.append("")
            lines.append(f"✅ Закреплено тем: {len(mastered)}.")
        if weak:
            lines.append("")
            lines.append("🎯 Стоит подтянуть:")
            for e in weak[:5]:
                lines.append(f"• {e['subject']}: {e['topic']}")
        schedule = load_json(profile.get("schedule"), {})
        if not schedule:
            lines.append("")
            lines.append("🗓 Расписание уроков не внесено — с ним занятия идут по сегодняшним урокам. "
                         "Его можно внести здесь: «🗓 Расписание».")
        return "\n".join(lines)

    async def _parent_card(self, message: NormalizedMessage, child_uid: int, to_user: Optional[int] = None) -> bool:
        """Недельный отчёт картинкой: дни занятий, решено, точность, оценки за уроки, слабые темы."""
        profile = await self._load_profile(child_uid)
        grade = profile.get("grade")
        today = daily_rules.local_now(topic_progress.now(), profile.get("tz_offset")).date()
        week = [(today - timedelta(days=i)).isoformat() for i in range(7)]
        activity = load_json(profile.get("activity"), {})
        solved = sum(activity.get(d, {}).get("solved", 0) for d in week)
        first_try = sum(activity.get(d, {}).get("first_try", 0) for d in week)
        days = sum(1 for d in week if activity.get(d, {}).get("solved"))
        marks = [(f"{subject_title(grade, m['subject_id'])} · {m['topic']}", m["mark"])
                 for m in load_json(profile.get("lesson_marks"), []) if m.get("date") in week][-6:]
        weak = [f"{e['subject']}: {e['topic']}" for e in load_json(profile.get("topic_progress"), {}).values()
                if e.get("grade") == grade and e.get("status") == "weak"]
        period = f"{(today - timedelta(days=6)).strftime('%d.%m')}–{today.strftime('%d.%m')}"
        return await self._send_card(
            message, cards.parent_card, profile.get("name") or "Ребёнок", grade, period, days, solved,
            round(100 * first_try / solved) if solved else None, marks, weak, to_user=to_user,
        )

    async def send_parent_report(self, message: NormalizedMessage) -> None:
        """Вызывается рассылкой (message — от имени ребёнка): отчёт всем подключённым родителям."""
        child_uid = message.user_id
        text = await self.build_parent_report(child_uid)
        for parent in await self._parents_of(child_uid):
            try:
                await self._parent_card(message, child_uid, to_user=parent["user_id"])
                await self.api.send_message(user_id=parent["user_id"], text=text,
                                            keyboard=_kb([[_btn("👨‍👩‍👧 Родительский раздел", "parent_menu")]]))
            except Exception as e:
                logger.error("Не удалось отправить отчёт родителю %s: %s", parent.get("user_id"), e)
        profile = await self._load_profile(child_uid)
        today = daily_rules.local_now(topic_progress.now(), profile.get("tz_offset")).date().isoformat()
        await self._save_profile(child_uid, last_parent_report=today)

    async def parent_schedule(self, message: NormalizedMessage, child_uid: int) -> None:
        uid = message.user_id
        if not await self._is_parent_of(uid, child_uid):
            await self.show_parent_menu(message)
            return
        profile = await self._load_profile(child_uid)
        grade = profile.get("grade")
        week = self._week_text(grade, load_json(profile.get("schedule"), {})) if grade else "—"
        await self.redis.set_state(f"awaiting_parent_sched_{uid}", str(child_uid), ex=3600)
        await self.answer(
            message,
            f"🗓 Расписание: {profile.get('name') or 'ребёнок'}\n\n{week}\n\n"
            "Пришлите фото расписания, скриншот из электронного дневника, файл Word / Excel — "
            "или напишите текстом, например:\nПн: математика, русский, чтение\nВт: …",
            keyboard=_kb([[_back("parent_menu")]]),
        )

    async def parent_schedule_input(self, message: NormalizedMessage, child_uid: int, text: Optional[str] = None,
                                    upload: Optional[tuple] = None) -> None:
        """Расписание за ребёнка: разбираем текст или файл по предметам ЕГО класса и просим подтвердить."""
        uid = message.user_id
        grade = (await self._load_profile(child_uid)).get("grade")
        if not grade or not await self._is_parent_of(uid, child_uid):
            await self.redis.delete_state(f"awaiting_parent_sched_{uid}")
            await self.show_parent_menu(message)
            return
        raw = text or ""
        if upload:
            await self.answer(message, "Смотрю расписание… 🔎")
            raw, _ = await self.read_upload(*upload)
            if raw.strip():
                raw = await ai_client.schedule_lines(raw, [n for n, _ in SUBJECTS[grade]]) or raw
        parsed, unknown = schedule_rules.parse_schedule(raw, [sid for _, sid in SUBJECTS[grade]])
        if not parsed:
            await self.answer(message, "Не получилось разобрать расписание 🙈 Начните каждую строку с дня недели "
                                       "(«Пн: математика, русский») или пришлите фото почётче.",
                              keyboard=_kb([[_back("parent_menu")]]))
            return
        await self.redis.delete_state(f"awaiting_parent_sched_{uid}")
        await self.redis.set_state(f"parent_sched_pending_{uid}", json.dumps([child_uid, parsed], ensure_ascii=False), ex=3600)
        text_out = "Вот что я понял:\n\n" + self._week_text(grade, parsed)
        if unknown:
            text_out += "\n\nПропустил уроки без заданий: " + ", ".join(unknown[:10]) + "."
        await self.answer(message, text_out + "\n\nВсё верно?",
                          keyboard=_kb([[_btn("✅ Всё верно", "parent_sch_ok")], [_back("parent_menu")]]))

    async def parent_schedule_confirm(self, message: NormalizedMessage) -> None:
        uid = message.user_id
        data = load_json(await self.redis.get_state(f"parent_sched_pending_{uid}"), None)
        if not data or not await self._is_parent_of(uid, int(data[0])):
            await self.show_parent_menu(message)
            return
        child_uid, parsed = int(data[0]), data[1]
        schedule = load_json((await self._load_profile(child_uid)).get("schedule"), {})
        schedule.update(parsed)
        await self._save_profile(child_uid, schedule=json.dumps(schedule, ensure_ascii=False))
        await self.redis.delete_state(f"parent_sched_pending_{uid}")
        await self.api.send_message(user_id=child_uid, text="🗓 Родитель внёс твоё расписание уроков — "
                                    "теперь я буду заниматься с тобой по нему.")
        await self.show_parent_menu(message, note="✅ Расписание сохранено.")


def _days_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "дня"
    return "дней"
