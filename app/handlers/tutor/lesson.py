"""Урок по теме: подробное объяснение → пробный пример (с подсказками, не на оценку) → задания на оценку → оценка.

Урок начинается, когда ученик пишет тему в предмете, или по расписанию: «Какую тему сегодня проходили?».
"""
from app.handlers.tutor.common import *  # noqa: F401,F403
from app.services.programs import programs


class LessonMixin:
    @staticmethod
    def _tb(textbook: Optional[str]) -> Dict[str, Any]:
        return {"textbook": textbook} if textbook else {}

    async def _lesson_expired(self, message: NormalizedMessage) -> None:
        grade = await self._get_grade(message.user_id)
        if grade:
            await self.show_subjects(message, grade, intro="Этот урок уже закончился 🙂 Выбери предмет и напиши тему.")
        else:
            await self.send_main_menu(message, await self.redis.get_state(f"name_{message.user_id}") or "друг")
    async def _lesson(self, uid: int) -> Optional[Dict[str, Any]]:
        state = load_json(await self.redis.get_state(f"lesson_{uid}"), None)
        return state if isinstance(state, dict) and state.get("topic") else None

    async def start_lesson(self, message: NormalizedMessage, grade: int, subject_id: str, topic: str) -> None:
        uid = message.user_id
        topic = topic.strip()[:80]
        title = subject_title(grade, subject_id)
        await self.redis.set_state(
            f"lesson_{uid}", json.dumps({"grade": grade, "subject_id": subject_id, "topic": topic}, ensure_ascii=False),
            ex=DAY,
        )
        textbook = await self._textbook(uid, subject_id)
        # Объяснение темы тоже кешируем: для того же класса, темы и учебника оно одинаковое
        cache_key = f"expl:{grade}:{subject_id}:{topic.lower()}:{textbook or ''}"
        text = await self.redis.get_state(cache_key)
        if not text:
            if not await self._ai_spend(uid):
                await self._ai_limit_reached(message)
                return
            await self.answer(message, f"Готовлю урок по теме «{topic}»… 📘")
            await self._typing(message)
            # Без имени ученика: объяснение из кеша увидят и другие ребята
            text = await ai_client.explain_topic(grade, title, topic, context={"grade": grade, "subject": title}, detailed=True,
                                                 **self._tb(textbook))
            if text and not text.startswith(("Ошибка при обращении к ИИ", "Ошибка сети")):
                await self.redis.set_state(cache_key, text, ex=30 * DAY)
        await self._mark_explained(uid, grade, subject_id, topic)
        self.start_prefetch(uid, grade, subject_id, topic)   # задания на оценку готовятся, пока ученик читает
        await self._set_via(uid, grade, subject_id, topic, "lesson")
        await self._send_with_voice(
            message,
            f"📘 Урок · {title} · {topic}\n\n{text}",
            keyboard=_kb([
                [_btn("✍️ Пробный пример", "lesson_trial")],
                [_btn("📝 Сразу на оценку", "lesson_graded")],
                *self._via_row(),
                [_back(f"subj_{subject_id}_{grade}")],
            ]),
        )

    async def lesson_trial(self, message: NormalizedMessage) -> None:
        """Пробный пример: с подсказками, ошибки не считаются."""
        state = await self._lesson(message.user_id)
        if not state:
            await self._lesson_expired(message)
            return
        await self._deliver_task(
            message, "regular", state["grade"], state["subject_id"],
            back_payload=f"subj_{state['subject_id']}_{state['grade']}", topic=state["topic"],
            note="✍️ Пробный пример — не на оценку: если ошибёшься, я подскажу.", lesson="trial",
        )

    async def lesson_graded(self, message: NormalizedMessage) -> None:
        """Задания на оценку: тест по теме урока без подсказок, в конце — оценка 2–5."""
        state = await self._lesson(message.user_id)
        if not state:
            await self._lesson_expired(message)
            return
        await self.start_subject_test(message, state["grade"], state["subject_id"], focus=[state["topic"]], mode="lesson")

    async def ask_lesson_topic(self, message: NormalizedMessage, subject_id: str) -> None:
        """«Какую тему сегодня проходили?» — кнопки с последними темами по предмету или тема словами."""
        uid = message.user_id
        grade = await self._get_grade(uid)
        if not grade or subject_id not in [s for _, s in SUBJECTS[grade]]:
            await self._lesson_expired(message)
            return
        progress = await self._load_progress(uid)
        recent = sorted(
            (e for e in progress.values() if e.get("grade") == grade and e.get("subject_id") == subject_id),
            key=lambda e: e.get("last_seen") or "", reverse=True,
        )
        # Сначала — темы, которые сейчас идут по программе, потом — недавние темы ученика
        topics: List[str] = list(programs.current_topics(grade, subject_id, await self._local_date(uid)))
        for e in recent:
            if e["topic"] not in topics:
                topics.append(e["topic"])
        topics = topics[:4]
        await self.redis.set_state(f"lesson_opts_{uid}", json.dumps([subject_id, topics], ensure_ascii=False), ex=DAY)
        await self.redis.set_state(f"awaiting_lesson_topic_{uid}", subject_id, ex=3600)
        title = subject_title(grade, subject_id)
        # Длинная тема на кнопку не влезет — тогда на кнопке номер, а тема целиком в сообщении
        rows = [[_btn(t if _vis_len(t) <= BUTTON_MAX else f"Тема {i + 1}", f"lesson_pick_{i}")] for i, t in enumerate(topics)]
        rows.append([_back(f"subjects_{grade}")])
        text = f"📘 {title}. Какую тему сегодня проходили на уроке? Напиши её (например: «Дроби»)"
        if topics:
            text += " или выбери из недавних:\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(topics, 1))
        await self._send_with_voice(message, text, keyboard=_kb(rows))

    async def lesson_pick(self, message: NormalizedMessage, index: int) -> None:
        uid = message.user_id
        data = load_json(await self.redis.get_state(f"lesson_opts_{uid}"), None)
        grade = await self._get_grade(uid)
        if not data or not grade or not 0 <= index < len(data[1]):
            await self._lesson_expired(message)
            return
        await self.redis.delete_state(f"awaiting_lesson_topic_{uid}")
        await self.start_lesson(message, grade, data[0], data[1][index])
