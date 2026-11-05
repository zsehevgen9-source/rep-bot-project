"""Обучение «Как пользоваться ботом»: для 1–2 классов — с голосом и тренировкой, для 3–11 — коротко по шагам."""
from app.handlers.tutor.common import *  # noqa: F401,F403

# Каждый шаг: текст, что сказать голосом, тип ("info" — просто «Дальше», "tap" — нажать кнопку, "quiz" — ответить)
KIDS_STEPS = [
    {"kind": "info",
     "text": "👋 Привет! Я — твой помощник в учёбе. Я умею говорить! 🔊\n\n"
             "После моих сообщений приходит голосовое. Нажми на него ▶️, чтобы послушать.",
     "voice": "Привет! Я твой помощник в учёбе. Я умею говорить. "
              "Нажимай на голосовое сообщение, чтобы послушать меня. Потом нажми кнопку «Дальше»."},
    {"kind": "tap",
     "text": "👆 Под моими сообщениями есть кнопки. Нажимай на них пальчиком.\n\nПопробуй: нажми кнопку «👆 Нажми меня».",
     "voice": "Под моими сообщениями есть кнопки. Нажимай на них пальчиком. Попробуй: нажми кнопку «Нажми меня».",
     "button": "👆 Нажми меня"},
    {"kind": "quiz",
     "text": "🔢 Когда я задаю вопрос, выбери ответ кнопкой с цифрой.\n\nДавай попробуем! Сколько будет 1 + 1?",
     "voice": "Когда я задаю вопрос, выбери ответ кнопкой. Давай попробуем! Сколько будет один плюс один? "
              "Первая кнопка — один, вторая — два, третья — три.",
     "options": ["1", "2", "3"], "answer": "2",
     "hint": "Почти! Посчитай на пальчиках: один пальчик и ещё один пальчик. Сколько получилось? ✋",
     "hint_voice": "Почти! Посчитай на пальчиках: один пальчик и ещё один пальчик. Сколько получилось?"},
    {"kind": "info",
     "text": "📚 В меню есть кнопки с предметами: Математика, Русский язык и другие. "
             "Нажми на предмет — и я дам тебе задание.\n\n◀️ Кнопка «Назад» возвращает на шаг назад.",
     "voice": "В меню есть кнопки с предметами: математика, русский язык и другие. "
              "Нажми на предмет, и я дам тебе задание. Кнопка «Назад» возвращает на шаг назад."},
    {"kind": "info",
     "text": "📅 Каждый день после школы я буду присылать тебе маленькие задания.\n\n"
             "🙋 Если что-то непонятно — попроси взрослого помочь или открой «⚙️ Настройки → 🎓 Обучение», и я всё повторю.\n\n"
             "👨‍👩‍👧 Для взрослых: /today — задания на сегодня, /class — сменить класс, "
             "«⚙️ Настройки» — время уведомлений.",
     "voice": "Каждый день после школы я буду присылать тебе маленькие задания. "
              "Если что-то непонятно, попроси взрослого помочь или нажми кнопку «Обучение», и я всё повторю. "
              "Нажми «Всё понятно»!"},
]

MAIN_STEPS = [
    {"kind": "info",
     "text": "🎓 Как со мной заниматься · 1 из 5\n\n"
             "📚 В главном меню — предметы (у старших — кнопка «📚 Предметы»). В первый раз я предложу короткий входной тест по пройденным темам.\n"
             "Потом напиши тему (например, «Дроби») — я проведу урок: объясню с примерами, дам пробный пример "
             "и задания на оценку. А «🎲 Тест по предмету» — это новый тест каждый раз.\n"
             "📸 Не получается домашка? Нажми «📸 Помощь с домашкой» и пришли фото задания — помогу разобраться.{prep_text}",
     "voice": "В главном меню — кнопки предметов. Нажми на предмет и напиши тему — я проведу урок: объясню, "
              "дам пробный пример и задания на оценку.{prep_voice}"},
    {"kind": "info",
     "text": "✍️ Как отвечать · 2 из 5\n\n"
             "Напиши ответ сообщением или нажми кнопку с вариантом (1️⃣ 2️⃣ 3️⃣), если они есть.\n"
             "Ошибся — не страшно: я не скажу ответ сразу, а дам подсказку, чтобы ты дошёл сам 💡\n"
             "Ответил верно — появится «➡️ Ещё задание».",
     "voice": "Напиши ответ сообщением или нажми кнопку с вариантом. Если ошибёшься, я дам подсказку, "
              "чтобы ты дошёл до ответа сам. Ответил верно — появится кнопка «Ещё задание»."},
    {"kind": "info",
     "text": "📅 Каждый день · 3 из 5\n\n"
             "По будням в 16:00 я пишу сам: если ты добавил расписание («⚙️ Настройки → 🗓 Расписание уроков»), предложу закрепить сегодняшние уроки, "
             "а если нет — пришлю задания по темам, которые стоит подтянуть. В 19:30 — короткий отчёт.\n"
             "Открыть самому: кнопка «📅 Что сегодня» в меню (или команда /today).\n"
             "Время меняется в «⚙️ Настройки → 🔔 Уведомления и время».",
     "voice": "По будням в шестнадцать ноль-ноль я присылаю задание на день, а вечером — короткий отчёт. "
              "Время можно поменять в настройках."},
    {"kind": "info",
     "text": "📊 Прогресс · 4 из 5\n\n"
             "В «📊 Прогресс» видно, какие темы ✅ закреплены, какие 🔄 изучаешь и что ❗ стоит подтянуть.\n"
             "Темы возвращаются на повторение через 1, 3, 7 и 14 дней — так знания запоминаются надолго.\n"
             "🎮 «Игры»: ⚡ Молния на скорость, 🗼 Мой маяк растёт, когда занимаешься каждый день, "
             "а после 3 заданий в день открывается 🎁 сундук с наградой.",
     "voice": "В разделе «Прогресс» видно, какие темы закреплены, а какие стоит подтянуть. "
              "Темы возвращаются на повторение через один, три, семь и четырнадцать дней."},
    {"kind": "info",
     "text": "⌨️ Команды · 5 из 5\n\n"
             "/start — главное меню\n/today — задания на сегодня\n/class — сменить класс\n"
             "/name — сменить имя\n/help — это обучение\n/delete_me — удалить мои данные\n"
             "Родитель нажимает «👨‍👩‍👧 Я родитель» и получает код — введи его в «⚙️ Настройки → 👨‍👩‍👧 Родитель».\n\n"
             "◀️ «Назад» всегда возвращает на шаг назад.",
     "voice": "И последнее: команды. Их список у тебя на экране. Кнопка «Назад» всегда возвращает на шаг назад. Удачи!"},
]

KIDS_MAX_GRADE = 2


class TutorialMixin:
    """Обучение «как пользоваться ботом»."""

    def _tutorial_steps(self, grade: Optional[int]):
        return KIDS_STEPS if grade and grade <= KIDS_MAX_GRADE else MAIN_STEPS

    async def start_tutorial(self, message: NormalizedMessage, after: str = "menu") -> None:
        """after: куда вести после обучения — "diag" (к диагностике, если она есть) или "menu"."""
        await self.redis.set_state(f"tut_after_{message.user_id}", after, ex=DAY)
        await self.show_tutorial_step(message, 0)

    async def show_tutorial_step(self, message: NormalizedMessage, index: int, prefix: str = "") -> None:
        grade = await self._get_grade(message.user_id)
        steps = self._tutorial_steps(grade)
        if not 0 <= index < len(steps):
            raise ValueError(f"нет шага обучения {index}")
        step = dict(steps[index])
        if "{prep_text}" in step["text"]:
            exam = EXAM_BY_GRADE.get(grade or 0)
            if exam == "vpr":
                prep_text, prep_voice = "\n📝 «Подготовка к ВПР» — задания в формате ВПР.", " Подготовка к ВПР — задания в формате ВПР."
            elif exam in ("oge", "ege"):
                name = EXAM_NAMES[exam]
                prep_text = f"\n📝 «Подготовка к {name}» — задания в формате экзамена."
                prep_voice = f" Подготовка к {name} — задания в формате экзамена."
            else:
                prep_text = prep_voice = ""
            step["text"] = step["text"].replace("{prep_text}", prep_text)
            step["voice"] = step["voice"].replace("{prep_voice}", prep_voice)
        rows: List[List[Dict[str, str]]] = []
        if step["kind"] == "tap":
            rows.append([_btn(step["button"], f"tut_tap_{index}")])
        elif step["kind"] == "quiz":
            rows += _option_rows(step["options"], prefix=f"tut_q_{index}_")
        else:
            last = index == len(steps) - 1
            rows.append([_btn("✅ Всё понятно!" if last else "Дальше ▶️", "tut_end" if last else f"tut_{index + 1}")])
        nav = []
        if index > 0:
            nav.append(_btn("◀️ Назад", f"tut_{index - 1}"))
        if index < len(steps) - 1:
            nav.append(_btn("✖️ Закрыть", "tut_end"))
        if nav:
            rows.append(nav)
        await self._send_with_voice(message, prefix + step["text"], keyboard=_kb(rows), voice_text=step["voice"])

    async def tutorial_tap(self, message: NormalizedMessage, index: int) -> None:
        await self.show_tutorial_step(message, index + 1, prefix="👍 Молодец! Вот так и нажимают кнопки.\n\n")

    async def tutorial_quiz(self, message: NormalizedMessage, index: int, option: int) -> None:
        grade = await self._get_grade(message.user_id)
        step = self._tutorial_steps(grade)[index]
        if step.get("kind") != "quiz" or not 0 <= option < len(step["options"]):
            raise ValueError("неверная кнопка обучения")
        if step["options"][option] == step["answer"]:
            await self.show_tutorial_step(message, index + 1, prefix="🎉 Правильно! Вот так и отвечают на вопросы.\n\n")
            return
        rows = _option_rows(step["options"], prefix=f"tut_q_{index}_") + [
            [_btn("◀️ Назад", f"tut_{index - 1}"), _btn("✖️ Закрыть", "tut_end")]]
        await self._send_with_voice(message, step["hint"], keyboard=_kb(rows), voice_text=step["hint_voice"])

    async def finish_tutorial(self, message: NormalizedMessage) -> None:
        uid = message.user_id
        first_time = not (await self._load_profile(uid)).get("tutorial_done")
        await self._save_profile(uid, tutorial_done=True)
        reward = await self._reward(uid, "tutorial") if first_time else ""
        after = await self.redis.get_state(f"tut_after_{uid}")
        await self.redis.delete_state(f"tut_after_{uid}")
        grade = await self._get_grade(uid)
        if not grade:
            name = await self.redis.get_state(f"name_{uid}") or "друг"
            await self.send_main_menu(message, name)
            return
        if after == "diag" and await self._should_offer_diagnostic(uid, grade):
            if reward:
                await self.answer(message, "Отлично, обучение пройдено!" + reward)
            await self.offer_diagnostic(message, grade)
            return
        await self.show_subjects(message, grade, intro="Отлично! Если что-то забудешь — открой «⚙️ Настройки → 🎓 Обучение» 🙂" + reward)
