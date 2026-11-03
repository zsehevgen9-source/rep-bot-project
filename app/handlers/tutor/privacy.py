"""Согласие на обработку данных и политика конфиденциальности (/privacy).

Бот хранит данные детей (имя, класс, ответы, оценки, расписание), поэтому перед знакомством спрашивает согласие.
По закону (152-ФЗ) за ребёнка младше 14 лет согласие даёт родитель — бот об этом прямо говорит.
Пока согласия нет, бот не запоминает имя и не отправляет тексты ученика в нейросеть.
"""
from app.handlers.tutor.common import *  # noqa: F401,F403

POLICY_VERSION = "2026-11"

CONSENT_SHORT = (
    "🔒 Прежде чем начать — о твоих данных.\n\n"
    "Чтобы заниматься с тобой, я сохраняю: имя, класс, город (для времени уведомлений), ответы на задания, "
    "оценки за уроки и расписание. Это нужно только для учёбы: никому не продаётся и не показывается, "
    "кроме родителя или учителя — и то лишь если ты сам дашь код.\n\n"
    "Если тебе меньше 14 лет — позови маму или папу: согласие даёт родитель.\n"
    "Удалить все данные можно в любой момент: «⚙️ Настройки → 🗑 Удалить мои данные».\n\n"
    "Подробно — кнопка «📄 Подробнее» или команда /privacy."
)

POLICY = (
    "📄 Политика конфиденциальности бота «Маячок»\n\n"
    "Кто мы. Бот-репетитор «Маячок» сделан школьной командой «Маяк» под руководством учителя "
    "для конкурса «Идея.Фикс».{contact}\n\n"
    "Что храним:\n"
    "• имя (как к тебе обращаться) и класс;\n"
    "• город или часовой пояс — чтобы присылать задания днём, а не ночью;\n"
    "• ответы на задания, прогресс по темам, оценки за уроки, очки и награды;\n"
    "• расписание уроков и выбранные учебники;\n"
    "• если подключён родитель — его номер в MAX, чтобы отправлять ему отчёт.\n\n"
    "Фото решения задания учителя: если учитель дал задание, ученик присылает фото черновика — оно хранится "
    "в MAX, а бот запоминает ссылку, чтобы учитель мог его посмотреть; удаляется вместе с классом или данными ученика.\n\n"
    "Что НЕ храним: фото домашки и расписания (только распознаём текст и сразу забываем), телефон, адрес, "
    "фамилию, данные родителей кроме номера в MAX.\n\n"
    "Зачем: подбирать задания, объяснять темы по программе школы, напоминать о занятиях и показывать прогресс.\n\n"
    "Кому передаём: тексты заданий, ответов и фото для распознавания обрабатывают сервисы Yandex Cloud "
    "(нейросеть YandexGPT, распознавание и синтез речи) — серверы в России. Ничего не продаём и не передаём "
    "для рекламы.\n\n"
    "Родители видят отчёт, только если ученик сам дал им код, и ученик может отключить любого родителя. "
    "Учитель видит занятия и оценки ученика, только если ученик сам вступил в его класс по коду; "
    "выйти из класса можно в «⚙️ Настройки → 🏫 Учитель».\n\n"
    "Сколько храним: пока ты пользуешься ботом. Удалить всё сразу: «⚙️ Настройки → 🗑 Удалить мои данные» "
    "или команда /delete_me — данные стираются, подключённые родители отключаются.\n\n"
    "Согласие: если ученику меньше 14 лет, согласие на обработку данных даёт родитель (законный представитель). "
    "Отозвать согласие = удалить данные (кнопка выше)."
)


class PrivacyMixin:
    # ---------- кто ты: ученик, родитель или учитель ----------

    async def ask_role(self, message: NormalizedMessage) -> None:
        await self._send_with_voice(
            message,
            "Привет! Я Маячок — репетитор для школьников 👋 Буду освещать дорогу в учёбе.\n\n"
            "Кто ты? Выбери кнопку. Если ты ученик — можно просто написать, как тебя зовут.",
            keyboard=_kb([[_btn("🎒 Я ученик", "role_student")], [_btn("👨‍👩‍👧 Я родитель", "parent_start")],
                          [_btn("👩‍🏫 Я учитель", "role_teacher")]]),
            voice_text="Привет! Я Маячок, репетитор для школьников. Кто ты: ученик, родитель или учитель? Выбери кнопку.",
        )

    async def choose_student(self, message: NormalizedMessage) -> None:
        uid = message.user_id
        if not await self.has_consent(uid):
            await self.ask_consent(message)
            return
        grade = await self._get_grade(uid)
        name = await self.redis.get_state(f"name_{uid}")
        if name and grade:
            await self.show_subjects(message, grade)
            return
        await self.redis.set_state(f"awaiting_name_{uid}", "true", ex=3600)
        await self._send_with_voice(message, "Отлично! 🎒 Давай познакомимся: как тебя зовут?")

    async def choose_teacher(self, message: NormalizedMessage) -> None:
        """«👩‍🏫 Я учитель». По умолчанию кабинет открывается сразу (TEACHER_APPROVAL=false), администратору — только
        уведомление. Это безопасно: учитель видит лишь тех учеников, кто сам вступил в его класс по коду.
        TEACHER_APPROVAL=true — кабинет откроется после кнопки «✅ Открыть» у администратора."""
        uid = message.user_id
        if await self._is_teacher(uid):
            await self.show_teacher_menu(message)
            return
        admins = sorted(settings.admin_ids)
        if not getattr(settings, "TEACHER_APPROVAL", False):
            teachers = load_json(await self.redis.get_state("teachers"), [])
            if uid not in teachers:
                teachers.append(uid)
                await self.redis.set_state("teachers", json.dumps(teachers))
            for admin in admins:
                try:
                    await self.api.send_message(
                        user_id=admin, text=f"👩‍🏫 Новый учитель в боте: пользователь {uid}. Ничего делать не нужно.\n"
                                            f"Если это не учитель — закрыть кабинет: /del_teacher {uid}")
                except Exception as e:
                    logger.warning("Не удалось уведомить администратора %s: %s", admin, e)
            await self.show_teacher_menu(message)
            return
        if not admins:
            await self.answer(message, "👩‍🏫 Кабинет учителя открывает администратор бота. Отправьте ему команду:\n"
                                       f"/add_teacher {uid}")
            return
        if await self.redis.get_state(f"teacher_req_{uid}"):
            await self.answer(message, "⏳ Запрос уже у администратора — как только он подтвердит, я сразу напишу.")
            return
        await self.redis.set_state(f"teacher_req_{uid}", "1", ex=7 * DAY)
        for admin in admins:
            try:
                await self.api.send_message(
                    user_id=admin,
                    text=f"👩‍🏫 Запрос на кабинет учителя от пользователя {uid}. Открыть доступ?",
                    keyboard=_kb([[_btn("✅ Открыть", f"t_ok_{uid}"), _btn("✖️ Отклонить", f"t_no_{uid}")]]),
                )
            except Exception as e:
                logger.warning("Не удалось отправить запрос администратору %s: %s", admin, e)
        await self.answer(message, "👩‍🏫 Отправил запрос администратору бота. Как только он подтвердит — "
                                   "я напишу, и откроется кабинет учителя.")

    async def answer_teacher_request(self, message: NormalizedMessage, tid: int, approve: bool) -> None:
        if not self._is_admin(message.user_id):
            await self.answer(message, "Эта кнопка — для администратора бота.")
            return
        await self.redis.delete_state(f"teacher_req_{tid}")
        if approve:
            await self.add_teacher(message, str(tid))
            return
        await self.answer(message, f"✖️ Запрос {tid} отклонён.")
        try:
            await self.api.send_message(user_id=tid, text="Администратор пока не открыл кабинет учителя. "
                                                         "Если это ошибка — свяжитесь с ним.")
        except Exception as e:
            logger.warning("Не удалось сообщить %s: %s", tid, e)

    def _consent_required(self) -> bool:
        return bool(getattr(settings, "CONSENT_REQUIRED", True))

    async def has_consent(self, uid: int) -> bool:
        if not self._consent_required():
            return True
        if await self.redis.get_state(f"consent_{uid}"):
            return True
        stored = (await self._load_profile(uid)).get("consent_at")
        if stored:
            await self.redis.set_state(f"consent_{uid}", str(stored))
        return bool(stored)

    async def ask_consent(self, message: NormalizedMessage, returning: bool = False) -> None:
        text = CONSENT_SHORT
        if returning:
            text = "Мы обновили правила работы с данными — прочитай, пожалуйста, это займёт минуту.\n\n" + text
        rows = [[_btn("✅ Согласен", "consent_yes")], [_btn("📄 Подробнее", "privacy")]]
        await self._send_with_voice(
            message, text, keyboard=_kb(rows),
            voice_text="Прежде чем начать: я сохраняю твоё имя, класс и ответы, только для учёбы. "
                       "Если тебе меньше четырнадцати лет, позови родителя, чтобы он нажал Согласен.",
        )

    async def give_consent(self, message: NormalizedMessage) -> None:
        uid = message.user_id
        stamp = f"{topic_progress.now().isoformat(timespec='seconds')} v{POLICY_VERSION}"
        await self.redis.set_state(f"consent_{uid}", stamp)
        await self._save_profile(uid, consent_at=stamp)
        profile = await self._load_profile(uid)
        name = profile.get("name") or await self.redis.get_state(f"name_{uid}")
        grade = await self._get_grade(uid)
        if name and grade:
            await self.show_subjects(message, grade, intro=f"Спасибо, {name}! 👍 Продолжаем учёбу.")
            return
        await self.redis.set_state(f"awaiting_name_{uid}", "true", ex=3600)
        await self._send_with_voice(message, "Спасибо! 😊 Давай познакомимся: как тебя зовут?")

    async def show_privacy(self, message: NormalizedMessage) -> None:
        uid = message.user_id
        contact = (getattr(settings, "PRIVACY_CONTACT", "") or "").strip()
        text = POLICY.format(contact=f" Связаться с нами: {contact}." if contact else "")
        grade = await self._get_grade(uid)
        rows = []
        if not await self.has_consent(uid):
            rows.append([_btn("✅ Согласен", "consent_yes")])
        elif grade:
            rows.append([_btn("🗑 Удалить мои данные", "delete_me")])
        rows.append([_back(f"settings_{grade}" if grade else "back_to_menu")])
        await self._quiet(message, text, keyboard=_kb(rows))
