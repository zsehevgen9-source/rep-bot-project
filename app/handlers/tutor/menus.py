"""Экраны: выбор класса, предметы, экзамены, темы, прогресс."""
from app.handlers.tutor.common import *  # noqa: F401,F403
from app.services import cards

VARIANT_EXAMS = ("oge", "ege")   # пробный вариант с прогнозом — для 9 и 11 класса
INLINE_SUBJECTS = 5   # столько предметов (1–4 класс) помещаем прямо в главное меню; больше — экраном «📚 Предметы»


class MenuMixin:
    """Экраны: выбор класса, предметы, экзамены, темы, прогресс."""

    async def send_main_menu(self, message: NormalizedMessage, name: str):
        """Выбор класса (корневой экран, «Назад» здесь не нужна)."""
        welcome_text = f"Отлично, {name}! Выбери свой класс, и давай начнем!"
        buttons = [_btn(f"{i} класс", f"grade_{i}") for i in range(1, 12)]

        await self._reset_modes(message.user_id)
        await self.redis.set_state(message.user_id, "history_[]", ex=3600)

        await self._send_with_voice(message, welcome_text, keyboard=_kb(_in_rows(buttons, 3)))

    async def show_subjects(
        self, message: NormalizedMessage, grade: int, first: bool = False, intro: Optional[str] = None
    ):
        """Главное меню: предметы класса, экзамен, прогресс. Кнопки «Назад» к классам здесь нет: класс меняется командой /class."""
        await self.redis.set_state(f"grade_{message.user_id}", str(grade), ex=GRADE_TTL)

        rows: List[List[Dict[str, str]]] = []
        state = await self._diag_state(message.user_id)
        if state and state.get("grade") == grade and state["index"] < len(state["ids"]):
            rows.append([_btn("▶️ Продолжить тест", "diag_resume")])
        # Главная кнопка: что делать сегодня (уроки по расписанию, мини-тест в пятницу или план по слабым темам)
        plan = await self._daily_plan(message.user_id)
        if plan and plan.get("grade") == grade and plan["index"] < len(plan["items"]):
            rows.append([_btn(f"📅 Что сегодня · {plan['index']}/{len(plan['items'])}", "today")])
        else:
            rows.append([_btn("📅 Что сегодня", "today")])
        if (await self._chest_status(message.user_id))["ready"]:
            rows.append([_btn("🎁 Открыть сундук", "chest")])
        exam = EXAM_BY_GRADE.get(grade)
        if exam:
            rows.append([_btn(f"📝 Подготовка к {EXAM_NAMES[exam]}", f"exam_{exam}_{grade}")])

        # Меню покороче: у малышей (до 5 предметов) предметы прямо здесь, у старших — отдельным экраном «📚 Предметы»
        subjects = SUBJECTS.get(grade, [])
        if len(subjects) <= INLINE_SUBJECTS:
            rows.extend(_fit_rows([_btn(button_title(name), f"subj_{sid}_{grade}") for name, sid in subjects],
                                  max_per_row=2))
            rows.append([_btn("📸 Помощь с домашкой", "hw")])
        else:
            rows.append([_btn("📚 Предметы", f"subjlist_{grade}"), _btn("📸 Домашка", "hw")])
        rows.append([_btn("🎮 Игры", "games"), _btn("🏆 Награды", f"rewards_{grade}")])
        rows.append([_btn("📊 Прогресс", f"progress_{grade}"), _btn("⚙️ Настройки", f"settings_{grade}")])
        app_url = await miniapp_url(self.redis)
        if app_url:
            rows.append([_link("📱 Открыть приложение", app_url)])

        prep = exam_word(grade)
        where = "Выбери предмет" if len(subjects) <= INLINE_SUBJECTS else "Выбери «📚 Предметы»"
        if prep == "ВПР":
            choose = f"{where} для обычной учёбы или нажми «📝 Подготовка к ВПР»"
        elif prep:
            choose = f"{where} для обычной учёбы или нажми на кнопку подготовки к экзамену"
        else:
            choose = f"{where}, и начнём заниматься"  # 1–3 классы: подготовки к экзаменам нет
        if first:
            text = (f"О, {grade} класс, отличный выбор! 🚀 {choose}. В каждом предмете я сначала предложу короткий "
                    "входной тест по пройденным темам.\nДобавь расписание уроков в «⚙️ Настройки → 🗓 Расписание "
                    "уроков» — и я буду заниматься с тобой по нему. Как всё устроено — в «⚙️ Настройки → 🎓 Обучение».")
        else:
            text = f"{choose}:"
        if intro:
            text = f"{intro}\n\n{text}"
        voice = text
        status = await self.menu_status(message.user_id, grade)
        if status:
            text = f"{status}\n\n{text}"  # строку с очками и уровнем показываем, но не зачитываем
        await self._send_with_voice(message, text, keyboard=_kb(rows), voice_text=voice)

    async def show_exam_subjects(self, message: NormalizedMessage, exam: str, grade: int):
        await self.redis.set_state(f"grade_{message.user_id}", str(grade), ex=GRADE_TTL)
        await self.redis.set_state(f"exam_type_{message.user_id}", exam, ex=3600)
        # Предмет можно не только нажать, но и написать словами
        await self.redis.set_state(f"awaiting_exam_subject_{message.user_id}", "true", ex=3600)

        buttons = [_btn(button_title(name), f"exsub_{exam}_{grade}_{sid}") for name, sid in exam_subjects(grade)]
        rows = []
        text = f"Отлично! Подготовка к {EXAM_NAMES[exam]}. 📚 Выбери предмет кнопкой или напиши его название:"
        if exam in VARIANT_EXAMS:
            rows.append([_btn("🧪 Пробный вариант", f"exvarl_{exam}_{grade}")])
            text = (f"📝 Подготовка к {EXAM_NAMES[exam]}\n\n🧪 «Пробный вариант» — 10 заданий на время и прогноз "
                    f"результата.\n📚 Или выбери предмет — задания по одному с подсказками (можно написать название):")
        rows += _fit_rows(buttons, max_per_row=2)
        rows.append([_back(f"subjects_{grade}")])
        await self._quiet(message, text, keyboard=_kb(rows))

    async def show_variant_subjects(self, message: NormalizedMessage, exam: str, grade: int) -> None:
        """Пробный вариант ОГЭ/ЕГЭ: выбор предмета (и прошлые результаты по предметам)."""
        uid = message.user_id
        history = load_json(await self.redis.get_state(f"exvar_{uid}"), {})
        lines = [f"🧪 Пробный вариант {EXAM_NAMES[exam]}: 10 заданий в формате экзамена, время засекаю. "
                 "В конце — прогноз и что подтянуть.\n\nВыбери предмет:"]
        for name, sid in exam_subjects(grade):
            items = history.get(sid) or []
            if items:
                last = items[-1]
                value = f"≈{last['value']} б." if exam == "ege" else f"«{last['value']}»"
                lines.append(f"• {name}: последний — {round(last['share'] * 100)}% ({value}), вариантов: {len(items)}")
        buttons = [_btn(button_title(name), f"exvar_{exam}_{grade}_{sid}") for name, sid in exam_subjects(grade)]
        await self._quiet(message, "\n".join(lines),
                          keyboard=_kb(_fit_rows(buttons, max_per_row=2) + [[_back(f"exam_{exam}_{grade}")]]))

    async def ask_topic(self, message: NormalizedMessage, subject_id: str, grade: int):
        title = subject_title(grade, subject_id)
        uid = message.user_id
        await self.redis.set_state(f"grade_{uid}", str(grade), ex=GRADE_TTL)
        await self.redis.set_state(f"current_subj_{uid}", title, ex=3600)
        await self.redis.set_state(f"current_subj_id_{uid}", subject_id, ex=3600)
        await self.redis.set_state(f"current_grade_{uid}", str(grade), ex=3600)
        await self.redis.set_state(f"awaiting_topic_{uid}", "true", ex=3600)

        rows = [[_btn("🎲 Тест по предмету", f"subjtest_{subject_id}_{grade}")]]
        now_topic = await self.program_topic_now(uid, grade, subject_id)
        if now_topic:
            rows.insert(0, [_btn("📘 Урок по программе", f"lesson_now_{subject_id}")])
            rows.append([_btn("📚 Программа", f"prog_{subject_id}"), _btn("📗 Учебник", f"tb_{subject_id}")])
        review_topic, _ = await self._pick_review_topic(uid, grade, subject_id)
        if review_topic:
            rows.append([_btn("🔁 Повторить тему", f"subjtask_{subject_id}_{grade}")])
        rows.append([_back(f"subjects_{grade}")])
        needs_check = await self.subject_needs_check(uid, grade, subject_id)
        if needs_check:
            rows.insert(0, [_btn("🧭 Входной тест", f"entry_{subject_id}_{grade}")])
        keyboard = _kb(rows)
        text = (f"О, {title} — это классная тема! 🎯 Какую тему вы сейчас проходите? Напиши её (например: «Дроби») — "
                "я проведу урок: объясню тему с примерами, дам пробный пример, а потом задания на оценку.\n"
                "Или нажми «🎲 Тест по предмету» — я составлю для тебя новый тест.")
        if now_topic:
            textbook = await self._textbook(uid, subject_id)
            text += (f"\n\n📚 Сейчас по программе: «{now_topic}» — нажми «📘 Урок по программе»."
                     f"\n📗 Учебник: {textbook or 'не выбран — нажми «📗 Учебник»'}")
        if needs_check:
            text = ("🧭 Давай начнём с короткого входного теста по темам, которые вы уже прошли, — так я пойму, "
                    "что ты знаешь, а что стоит подтянуть.\n\n" + text)
        if review_topic:
            text += f"\n\n🔁 Пора повторить тему «{review_topic}» — для этого есть кнопка «Повторить тему»."
        await self._quiet(message, text, keyboard=keyboard)

    async def show_progress(self, message: NormalizedMessage, grade: int):
        profile = await self._load_profile(message.user_id)
        has_questions = bool(self._diag_questions(grade))
        rows: List[List[Dict[str, str]]] = []

        if profile.get("diagnostic_status") == "done" and profile.get("diagnostic_summary"):
            scores = load_json(profile.get("diagnostic_scores"), {})
            weak = load_json(profile.get("weak_topics"), [])
            lines = [f"📊 Итоги диагностики ({profile.get('diagnostic_grade') or grade} класс)", ""]
            lines += [f"• {subject}: {v['correct']} из {v['total']}" for subject, v in scores.items()]
            if weak:
                lines += ["", "🎯 Темы для тренировки: " + ", ".join(f"{w['topic']} ({w['subject']})" for w in weak)]
            lines += ["", profile["diagnostic_summary"]]
            text, voice = "\n".join(lines), profile["diagnostic_summary"]
            if has_questions:
                rows.append([_btn("🔄 Диагностика заново", f"diag_start_{grade}")])
        else:
            text = "Ты ещё не проходил диагностику. Она поможет понять, какие темы стоит подтянуть."
            if not has_questions:
                text += f" Для {grade} класса диагностика скоро появится 🙂"
            voice = text
            if has_questions:
                rows.append([_btn("🚀 Пройти диагностику", f"diag_start_{grade}")])

        progress = load_json(profile.get("topic_progress"), {})
        info = topic_progress.summary(progress, grade)
        if info["total"]:
            lines = ["", "📚 Темы:"]
            for subject, counts in info["by_subject"].items():
                parts = [f"{topic_progress.STATUS_LABELS[s].split()[0]} {n}" for s, n in counts.items() if n]
                lines.append(f"• {subject}: " + " · ".join(parts))
            if info["was_weak"]:
                lines.append(f"Закреплено слабых тем: {info['weak_now_mastered']} из {info['was_weak']}")
            if info["due"]:
                names = ", ".join(f"{e['topic']} ({e['subject']})" for e in info["due"][:5])
                lines.append(f"🔁 Пора повторить: {names}")
            lines.append("✅ закреплено · 🔄 изучаем · ❗ стоит подтянуть · 👍 знает")
            text += "\n" + "\n".join(lines)

        marks = [m for m in load_json(profile.get("lesson_marks"), []) if m.get("grade") == grade]
        if marks:
            by_subject: Dict[str, List[str]] = {}
            for m in marks:
                by_subject.setdefault(subject_title(grade, m["subject_id"]), []).append(str(m["mark"]))
            text += "\n\n📒 Оценки за уроки:\n" + "\n".join(
                f"• {s}: {', '.join(v[-8:])}" for s, v in by_subject.items())
        app_url = await miniapp_url(self.redis, "progress")
        if app_url:
            rows.append([_link("📱 Подробно в приложении", app_url)])
        rows.append([_back(f"subjects_{grade}")])
        await self._progress_card(message, grade, profile, info)
        await self._quiet(message, text, keyboard=_kb(rows), voice_text=voice)

    async def _progress_card(self, message: NormalizedMessage, grade: int, profile: Dict[str, Any],
                             info: Dict[str, Any]) -> bool:
        """Карточка «Мой прогресс»: очки, серия, цель недели и полоски по предметам."""
        uid = message.user_id
        mode = rewards_rules.mode_for_grade(grade)
        rewards = load_json(profile.get("rewards"), {})
        facts = await self._reward_facts(uid, profile, grade)
        points = rewards.get("points", 0)
        level_name, _ = rewards_rules.level(points, mode)
        shown = points * 10 if mode == rewards_rules.COACH else points
        subjects = [
            (subject, c.get("known", 0) + c.get("mastered", 0), c.get("learning", 0), c.get("weak", 0))
            for subject, c in info["by_subject"].items()
        ]
        name = await self.redis.get_state(f"name_{uid}") or "Ученик"
        return await self._send_card(
            message, cards.progress_card, name, grade,
            f"уровень «{level_name}»" if level_name else rewards_rules.MODE_NAMES[mode].split()[0],
            f"{shown:,}".replace(",", " "), facts["streak"],
            min(facts["week"]["solved"], rewards_rules.WEEKLY_GOAL[mode]), rewards_rules.WEEKLY_GOAL[mode], subjects,
        )

    async def show_subject_list(self, message: NormalizedMessage, grade: int) -> None:
        """«📚 Предметы» (5–11 класс): все предметы класса."""
        buttons = [_btn(button_title(name), f"subj_{sid}_{grade}") for name, sid in SUBJECTS.get(grade, [])]
        await self._quiet(message, "📚 Выбери предмет:",
                          keyboard=_kb(_fit_rows(buttons, max_per_row=2) + [[_back(f"subjects_{grade}")]]))

    async def show_games(self, message: NormalizedMessage) -> None:
        """«🎮 Игры»: Молния, Карточки, Мой маяк."""
        uid = message.user_id
        grade = await self._get_grade(uid)
        rows = [[_btn("⚡ Молния — на скорость", "zap")]]
        lines = ["🎮 Игры и тренировки\n", "⚡ Молния — 60 секунд, кто больше ответит."]
        if hasattr(self, "show_flashcards") and (grade or 0) >= FLASHCARDS_FROM_GRADE:
            due = await self.fc_due_count(uid)
            rows.append([_btn(f"🃏 Карточки ({due})" if due else "🃏 Карточки", "fc")])
            lines.append("🃏 Карточки — термины, даты, формулы: бот сам вернёт то, что ты забываешь.")
        rows.append([_btn("🗼 Мой маяк", "lh"), _btn("📒 Альбом", "album")])
        lines.append("🗼 Мой маяк — растёт, когда занимаешься каждый день. 📒 Альбом — наклейки из сундуков.")
        rows.append([_back(f"subjects_{grade}" if grade else "back_to_menu")])
        await self._quiet(message, "\n".join(lines), keyboard=_kb(rows))

    async def show_settings(self, message: NormalizedMessage, grade: int):
        name = await self.redis.get_state(f"name_{message.user_id}") or "не указано"
        label = {"auto": "🔊 Голос: сразу", "button": "🔈 Голос: по кнопке", "off": "🔇 Голос: выключен"}
        rows = [
            [_btn("✏️ Имя", "set_name"), _btn("🔢 Класс", "set_class")],
            [_btn("🔔 Уведомления и время", "notify_settings")],
            [_btn("🗓 Расписание уроков", "sched")],
            [_btn(label[await self._voice_mode(message.user_id)], "voice_toggle")],
            [_btn("🎯 Увлечения", "interests"), _btn("🎓 Обучение", "tutorial")],
            [_btn("👨‍👩‍👧 Родитель", "parent_code"), _btn("🏫 Учитель", "sc_my")],
        ]
        if self._diag_questions(grade):
            rows.append([_btn("🔄 Диагностика заново", f"diag_start_{grade}")])
        rows.append([_btn("🔒 Данные", "privacy"), _btn("🗑 Удалить", "delete_me")])
        rows.append([_back(f"subjects_{grade}")])
        await self._quiet(
            message,
            f"⚙️ Настройки\n\nИмя: {name}\nКласс: {grade}",
            keyboard=_kb(rows),
            voice_text="Настройки. Здесь можно сменить имя или класс.",
        )

    async def toggle_voice(self, message: NormalizedMessage):
        """«Голос» в настройках: сразу → по кнопке → выключен → сразу …"""
        uid = message.user_id
        nxt = {"auto": "b", "button": "0", "off": "1"}[await self._voice_mode(uid)]
        await self.redis.set_state(f"voice_on_{uid}", nxt)
        await self.answer(message, {
            "1": "🔊 Голос — сразу: каждое сообщение продублирую голосовым.",
            "b": "🔈 Голос — по кнопке: под сообщением будет «🔊 Послушать», нажми — и я прочитаю.",
            "0": "🔇 Голос выключен: буду писать только текстом. Включить — здесь же.",
        }[nxt])
        await self.show_settings(message, await self._get_grade(uid))

    async def ask_new_name(self, message: NormalizedMessage):
        await self.redis.set_state(f"awaiting_name_{message.user_id}", "true", ex=3600)
        await self.redis.set_state(f"renaming_{message.user_id}", "true", ex=3600)
        await self._send_with_voice(message, "Как тебя теперь называть? Напиши или скажи имя.")

    async def confirm_delete(self, message: NormalizedMessage):
        grade = await self._get_grade(message.user_id)
        await self._quiet(
            message,
            "Удалить все твои данные: имя, класс, итоги диагностики и прогресс? Это нельзя отменить.",
            keyboard=_kb([
                [_btn("Да, удалить", "delete_me_yes")],
                [_back(f"settings_{grade}" if grade else "back_to_menu")],
            ]),
        )

    async def delete_user_data(self, message: NormalizedMessage):
        uid = message.user_id
        await self.forget_family(uid)   # родители и дети больше не видят друг друга
        await self.forget_classes(uid)  # ученик выходит из класса, учитель — удаляет свои классы
        await self.profiles.delete(uid)
        for prefix in USER_KEY_PREFIXES + ["renaming"]:
            await self.redis.delete_state(f"{prefix}_{uid}")
        await self.redis.delete_state(uid)
        await self.answer(message, "Готово: все твои данные удалены. Чтобы начать заново, напиши /start.")
