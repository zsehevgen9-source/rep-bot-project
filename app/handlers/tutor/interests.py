"""«🎨 Объясни через…»: тема объясняется через то, что нравится ученику — футбол, космос, Майнкрафт, аниме…

Увлечения ученик отмечает в «⚙️ Настройки → 🎯 Увлечения» (interests_<uid>), они идут первыми в списке.
Объяснение одинаково для всех учеников класса → кешируется на 30 дней (expl_via:…), повторный запрос бесплатный.
Новых объяснений (запросов к ИИ) — не больше VIA_DAILY_LIMIT в день на ученика.
"""
from app.handlers.tutor.common import *  # noqa: F401,F403

# key: (кнопка, название мира для ИИ, что в этом мире есть)
INTERESTS = {
    "mine": ("⛏ Майнкрафт", "Майнкрафт", "мир из кубиков: блоки, крафт, шахты, руда, мобы, редстоун, постройки"),
    "foot": ("⚽ Футбол", "футбол", "матч, команда, пасы, удары по воротам, тренер, турнирная таблица"),
    "anime": ("🌸 Аниме", "аниме", "юный герой тренируется и становится сильнее, команда друзей, особые приёмы, "
                                   "соперник, школа героев — свои герои, не из настоящих аниме"),
    "space": ("🚀 Космос", "космос", "ракеты, планеты, орбиты, космонавты, станции, звёзды"),
    "games": ("🎮 Игры", "видеоигры", "уровни, прокачка персонажа, очки опыта, квесты, босс в конце уровня"),
    "food": ("🍕 Еда", "кулинария", "рецепты, пицца, торт, кухня, ингредиенты, повар"),
    "animals": ("🐾 Животные", "животные", "звери, повадки, зоопарк, питомцы, дикая природа"),
    "music": ("🎵 Музыка", "музыка", "ноты, ритм, группа, концерт, песня, инструменты"),
    "cars": ("🚗 Машины", "машины и гонки", "гонки, двигатель, скорость, трасса, пит-стоп, механик"),
    "magic": ("🧙 Магия", "волшебство", "школа волшебства, заклинания, зелья, драконы — свои герои, не из книг"),
}
MAX_INTERESTS = 3


class InterestsMixin:

    async def _interests(self, uid: int) -> List[str]:
        data = load_json(await self.redis.get_state(f"interests_{uid}"), [])
        return [k for k in data if k in INTERESTS] if isinstance(data, list) else []

    # ---------- настройки: мои увлечения ----------

    async def ask_interests(self, message: NormalizedMessage, note: str = "") -> None:
        uid = message.user_id
        chosen = await self._interests(uid)
        grade = await self._get_grade(uid)
        buttons = [_btn(("✅ " if k in chosen else "") + v[0], f"int_{k}") for k, v in INTERESTS.items()]
        rows = _fit_rows(buttons, max_per_row=2) + [[_back(f"settings_{grade}" if grade else "back_to_menu")]]
        text = (f"{note}🎯 Что тебе нравится? Отметь до {MAX_INTERESTS} увлечений — нажми ещё раз, чтобы убрать.\n\n"
                "Под объяснением темы будет кнопка «🎨 По-другому»: объясню через то, что тебе интересно "
                "(например, дроби — через пиццу, а силу тяжести — через футбол).")
        if chosen:
            text += "\n\nСейчас: " + ", ".join(INTERESTS[k][0] for k in chosen)
        await self._quiet(message, text, keyboard=_kb(rows))

    async def toggle_interest(self, message: NormalizedMessage, key: str) -> None:
        uid = message.user_id
        if key not in INTERESTS:
            raise ValueError(f"неизвестное увлечение {key}")
        chosen = await self._interests(uid)
        note = ""
        if key in chosen:
            chosen.remove(key)
        elif len(chosen) >= MAX_INTERESTS:
            note = f"Можно выбрать до {MAX_INTERESTS} — сначала убери одно.\n\n"
        else:
            chosen.append(key)
        await self.redis.set_state(f"interests_{uid}", json.dumps(chosen))
        await self.ask_interests(message, note)

    # ---------- «🎨 По-другому» ----------

    async def _set_via(self, uid: int, grade: int, subject_id: str, topic: str, kind: str) -> None:
        """Запоминаем тему последнего объяснения, чтобы кнопка «🎨 По-другому» знала, что объяснять.
        kind — откуда пришли: "lesson" (урок), "plan" (перед заданием дня), "task" (во время задания)."""
        await self.redis.set_state(f"via_ctx_{uid}", json.dumps(
            {"grade": grade, "subject_id": subject_id, "topic": topic, "kind": kind,
             "back": f"subj_{subject_id}_{grade}"}, ensure_ascii=False), ex=DAY)

    @staticmethod
    def _via_continue(kind: str) -> List[List[Dict[str, str]]]:
        """Как продолжить после объяснения «по-другому» — туда же, откуда пришли."""
        if kind == "lesson":
            return [[_btn("✍️ Пробный пример", "lesson_trial")], [_btn("📝 Сразу на оценку", "lesson_graded")]]
        if kind == "plan":
            return [[_btn("✅ Понятно, к заданию", "plan_next")]]
        if kind == "task":
            return [[_btn("↩️ К заданию", "via_task")]]
        return []

    async def back_to_task(self, message: NormalizedMessage) -> None:
        """«↩️ К заданию»: снова показать текущее задание с вариантами (оно не сбрасывалось)."""
        uid = message.user_id
        task = await self.redis.get_state(f"current_task_{uid}")
        grade = await self._get_grade(uid)
        if await self.redis.get_state(f"task_active_{uid}") != "true" or not task:
            await self.show_subjects(message, grade) if grade else await self.send_main_menu(message, "")
            return
        options = load_json(await self.redis.get_state(f"options_{uid}"), [])
        ctx = load_json(await self.redis.get_state(f"task_ctx_{uid}"), {})
        back = await self.redis.get_state(f"back_{uid}") or f"subjects_{grade}"
        await self._quiet(message, f"✍️ Задание:\n\n{task}" + _options_text(options),
                          keyboard=_kb(_option_rows(options) + self._task_extra_rows(bool(ctx.get("plan")))
                                       + [[_back(back)]]))

    @staticmethod
    def _via_row() -> List[List[Dict[str, str]]]:
        return [[_btn("🎨 Объясни по-другому", "via")]]

    async def ask_via(self, message: NormalizedMessage) -> None:
        uid = message.user_id
        ctx = load_json(await self.redis.get_state(f"via_ctx_{uid}"), None)
        if not ctx:
            await self._quiet(message, "Сначала открой объяснение темы — потом можно попросить объяснить по-другому 🙂")
            return
        chosen = await self._interests(uid)
        order = chosen + [k for k in INTERESTS if k not in chosen]
        buttons = [_btn(("⭐ " if k in chosen else "") + INTERESTS[k][0], f"via_{k}") for k in order]
        rows = _fit_rows(buttons, max_per_row=2) + [[_btn("🎯 Мои увлечения", "interests")], [_back(ctx["back"])]]
        await self._quiet(message, f"🎨 Тема «{ctx['topic']}». Через что объяснить?", keyboard=_kb(rows))

    async def explain_via(self, message: NormalizedMessage, key: str) -> None:
        uid = message.user_id
        ctx = load_json(await self.redis.get_state(f"via_ctx_{uid}"), None)
        if not ctx or key not in INTERESTS:
            await self.ask_via(message)
            return
        label, world, details = INTERESTS[key]
        grade, subject_id, topic = int(ctx["grade"]), ctx["subject_id"], ctx["topic"]
        title = subject_title(grade, subject_id)
        cache_key = f"expl_via:{grade}:{subject_id}:{topic.lower()}:{key}"
        text = await self.redis.get_state(cache_key)
        if not text:
            limit = int(getattr(settings, "VIA_DAILY_LIMIT", 5) or 0)
            day = topic_progress.now().date().isoformat()
            used = int(await self.redis.get_state(f"via_used_{uid}_{day}") or 0)
            if limit and used >= limit and not self._is_admin(uid):
                await self._quiet(message, f"🎨 На сегодня новые объяснения «по-другому» закончились ({limit} в день). "
                                           "Завтра можно снова! А уже готовые объяснения открываются всегда.",
                                  keyboard=_kb([[_back(ctx["back"])]]))
                return
            if not await self._ai_spend(uid):
                await self._ai_limit_reached(message)
                return
            await self.redis.set_state(f"via_used_{uid}_{day}", str(used + 1), ex=2 * DAY)
            await self.answer(message, f"{label} Придумываю объяснение… ⏳")
            await self._typing(message)
            text = await ai_client.explain_via(grade, title, topic, world, details)
            if text and not text.startswith(("Ошибка при обращении к ИИ", "Ошибка сети")):
                await self.redis.set_state(cache_key, text, ex=30 * DAY)
        rows = self._via_continue(ctx.get("kind", "")) + [[_btn("🎨 Ещё по-другому", "via")], [_back(ctx["back"])]]
        await self._send_with_voice(message, f"{label} · {topic}\n\n{text}", keyboard=_kb(rows))
