"""«📸 Помощь с домашкой»: ученик фотографирует задание (или пишет его) — бот ведёт подсказками, ответ не называет.

Фото распознаёт Yandex Vision OCR, YandexGPT восстанавливает условие и определяет предмет. Дальше — обычная проверка
с подсказками, как в заданиях бота: эталон бот считает сам и держит при себе, чтобы не проговориться.
"""
from app.handlers.tutor.common import *  # noqa: F401,F403


class HomeworkMixin:
    async def ask_homework(self, message: NormalizedMessage) -> None:
        uid = message.user_id
        grade = await self._get_grade(uid)
        if not grade:
            await self.send_main_menu(message, await self.redis.get_state(f"name_{uid}") or "друг")
            return
        await self.redis.set_state(f"awaiting_homework_{uid}", "true", ex=3600)
        await self._send_with_voice(
            message,
            "📸 Сфотографируй задание из учебника или тетради и пришли мне — или напиши его текстом.\n\n"
            "Я помогу разобраться: дам подсказки и наводящие вопросы, а ответ ты найдёшь сам 💪\n"
            "Если в задании есть рисунок или чертёж — опиши его словами.",
            keyboard=_kb([[_back(f"subjects_{grade}")]]),
            voice_text="Сфотографируй задание и пришли мне, или напиши его текстом. Я помогу разобраться подсказками.",
        )

    async def homework_from_file(self, message: NormalizedMessage, url: str, name: Optional[str], kind: Optional[str]) -> None:
        await self.redis.delete_state(f"awaiting_homework_{message.user_id}")
        await self.answer(message, "Читаю задание… 🔎")
        raw, _ = await self.read_upload(url, name, kind)
        if not raw.strip():
            await self._send_with_voice(
                message,
                "Не получилось прочитать задание 😕 Сфотографируй поближе, ровно и при хорошем свете — "
                "или напиши задание текстом.",
                keyboard=_kb([[_btn("📸 Попробовать ещё раз", "hw")]]),
            )
            return
        await self._homework_from_raw(message, raw)

    async def homework_from_text(self, message: NormalizedMessage, text: str) -> None:
        await self.redis.delete_state(f"awaiting_homework_{message.user_id}")
        await self._homework_from_raw(message, text)

    async def _homework_from_raw(self, message: NormalizedMessage, raw: str) -> None:
        uid = message.user_id
        grade = await self._get_grade(uid)
        if not await self._ai_spend(uid):
            await self._ai_limit_reached(message)
            return
        titles = [name for name, _ in SUBJECTS[grade]]
        tasks = await ai_client.homework_tasks(raw, grade, titles)
        if not tasks:
            await self._send_with_voice(
                message,
                "Я не нашёл здесь задания 🤔 Пришли фото поближе к заданию или напиши его текстом.",
                keyboard=_kb([[_btn("📸 Попробовать ещё раз", "hw")]]),
            )
            return
        if len(tasks) == 1:
            await self._start_homework(message, grade, tasks[0])
            return
        await self.redis.set_state(f"hw_tasks_{uid}", json.dumps(tasks, ensure_ascii=False), ex=3600)
        listing = "\n\n".join(f"{n}. {t['text']}" for n, t in enumerate(tasks, 1))
        rows = [[_btn(f"Задание {n}", f"hw_pick_{n - 1}") for n in range(1, len(tasks) + 1)]]
        await self._send_with_voice(
            message, f"Я нашёл несколько заданий:\n\n{listing}\n\nС какого начнём?", keyboard=_kb(rows),
            voice_text="Я нашёл несколько заданий. Выбери, с какого начнём.",
        )

    async def homework_pick(self, message: NormalizedMessage, index: int) -> None:
        uid = message.user_id
        tasks = load_json(await self.redis.get_state(f"hw_tasks_{uid}"), [])
        grade = await self._get_grade(uid)
        if not grade or not 0 <= index < len(tasks):
            await self.ask_homework(message)
            return
        await self._start_homework(message, grade, tasks[index])

    async def _start_homework(self, message: NormalizedMessage, grade: int, task: Dict[str, Any]) -> None:
        by_title = {name.lower(): sid for name, sid in SUBJECTS[grade]}
        subject_id = by_title.get((task.get("subject") or "").lower()) or SUBJECTS[grade][0][1]
        # Эталон бот вычисляет сам и не показывает: по нему проверяет ответ и вычищает случайные подсказки-ответы
        reference = await ai_client.solve(task["text"])
        await self._deliver_task(
            message, "regular", grade, subject_id, back_payload=f"subjects_{grade}", topic=task.get("topic") or None,
            given={"text": task["text"], "answer": reference, "options": [], "topic": task.get("topic") or None,
                   "label": "📸 Задание с фото. Решай сам — если застрянешь, напиши «не знаю» или нажми «📖 Объясни тему»."},
        )
