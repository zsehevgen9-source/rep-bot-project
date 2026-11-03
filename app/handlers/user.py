"""Обработчик сообщений и кнопок ученика.

Логика разбита на части в app/handlers/tutor/: core (голос, профиль), menus (экраны),
tasks (задания), diagnostic (диагностика). Здесь — только разбор входящих сообщений и кнопок.
"""
from app.handlers.base import BaseHandler
from app.services import cards
from app.handlers.tutor.common import *  # noqa: F401,F403
from app.handlers.tutor.core import CoreMixin
from app.handlers.tutor.privacy import PrivacyMixin
from app.handlers.tutor.teacher import TeacherMixin
from app.handlers.tutor.lighthouse import LighthouseMixin
from app.handlers.tutor.chest import ChestMixin
from app.handlers.tutor.lightning import LightningMixin
from app.handlers.tutor.interests import InterestsMixin
from app.handlers.tutor.flashcards import FlashcardsMixin
from app.handlers.tutor.daily import DailyMixin
from app.handlers.tutor.diagnostic import DiagnosticMixin
from app.handlers.tutor.menus import MenuMixin
from app.handlers.tutor.rewards import RewardsMixin
from app.handlers.tutor.subject_test import SubjectTestMixin
from app.handlers.tutor.demo import DemoMixin
from app.handlers.tutor.homework import HomeworkMixin
from app.handlers.tutor.lesson import LessonMixin
from app.handlers.tutor.parents import ParentMixin
from app.handlers.tutor.program import ProgramMixin
from app.handlers.tutor.schedule import ScheduleMixin
from app.handlers.tutor.tasks import TasksMixin
from app.handlers.tutor.timezone import TimezoneMixin
from app.handlers.tutor.voice_admin import VoiceAdminMixin
from app.handlers.tutor.tutorial import KIDS_MAX_GRADE, TutorialMixin


class UserHandler(CoreMixin, MenuMixin, TasksMixin, SubjectTestMixin, DiagnosticMixin, DailyMixin, TutorialMixin,
                  RewardsMixin, VoiceAdminMixin, TimezoneMixin, ScheduleMixin, LessonMixin,
                  ProgramMixin, HomeworkMixin, ParentMixin, DemoMixin, PrivacyMixin, TeacherMixin, LighthouseMixin, ChestMixin, LightningMixin, InterestsMixin, FlashcardsMixin, BaseHandler):

    async def handle_message(self, message: NormalizedMessage):
        # Голосовое сообщение: распознаём и дальше работаем с ним как с обычным текстом
        if message.audio_url and not (message.text or "").strip():
            recognized = await self._voice_to_text(message)
            if not recognized:
                return
            message.text = recognized

        # Фото или документ: расписание уроков (если его ждём) или вопрос «Это расписание?»
        if message.file_url:
            uid = message.user_id
            await self._touch_activity(message)
            if await self.redis.get_state(f"awaiting_schedule_{uid}") == "true":
                await self.schedule_from_file(message, message.file_url, message.file_name, message.file_kind)
                return
            if await self.redis.get_state(f"awaiting_homework_{uid}") == "true":
                await self.homework_from_file(message, message.file_url, message.file_name, message.file_kind)
                return
            parent_child = await self.redis.get_state(f"awaiting_parent_sched_{uid}")
            if parent_child:
                await self.parent_schedule_input(message, int(parent_child),
                                                 upload=(message.file_url, message.file_name, message.file_kind))
                return
            if await self.assignment_photo_pending(uid):   # фото решения задания учителя
                await self.assignment_photo(message)
                return
            if not (message.text or "").strip():
                await self.redis.set_state(f"last_file_{uid}", json.dumps(
                    [message.file_url, message.file_name, message.file_kind], ensure_ascii=False), ex=3600)
                await self.answer(
                    message, "Я получил " + ("фото" if message.file_kind == "image" else "файл")
                    + " 📎 Что это?",
                    keyboard=_kb([[_btn("📸 Задание — помоги", "hw_file")], [_btn("🗓 Это расписание", "sch_file")],
                                  [_btn("Ничего", "menu")]]),
                )
                return

        if not (message.text or "").strip():
            await self.answer(message, "Напиши мне сообщение или нажми кнопку 🙂")
            return

        text = message.text or ""
        await self._touch_activity(message)
        uid = message.user_id

        # 1. Приветствие: знакомый ученик — сразу в меню, новый — знакомимся
        if text.startswith("/start") or text.strip().lower() == "привет":
            await self._reset_modes(uid)
            profile = await self._load_profile(uid)
            name = profile.get("name") or await self.redis.get_state(f"name_{uid}")
            grade = profile.get("grade") or await self._get_grade(uid)
            if name and grade in SUBJECTS:
                await self.redis.set_state(f"name_{uid}", name, ex=GRADE_TTL)
                await self.redis.set_state(f"grade_{uid}", str(grade), ex=GRADE_TTL)
                await self.redis.delete_state(f"awaiting_name_{uid}")
                state = await self._diag_state(uid)
                if state and state.get("grade") == grade and state["index"] < len(state["ids"]):
                    await self._send_with_voice(
                        message,
                        f"С возвращением, {name}! 👋 Ты не закончил диагностику: ответил на "
                        f"{state['index']} из {len(state['ids'])} вопросов. Продолжим?",
                        keyboard=_kb([[_btn("▶️ Продолжить", "diag_resume")], [_btn("Позже", f"subjects_{grade}")]]),
                    )
                elif not await self.has_consent(uid):
                    await self.ask_consent(message, returning=True)   # ученик был до появления согласия
                else:
                    await self.show_subjects(message, grade, intro=f"С возвращением, {name}! 👋 Готов продолжить учёбу?")
                return

            if await self._children_of(uid):   # это родитель — сразу в родительский раздел
                await self.show_parent_menu(message)
                return
            if await self._is_teacher(uid, explicit=True):   # учитель (не ученик) — сразу в кабинет учителя
                await self.show_teacher_menu(message)
                return
            await self._send_card(message, cards.welcome_card)
            await self.ask_role(message)
            if await self.has_consent(uid):
                # Согласие не требуется (или уже дано): ученик может сразу написать имя, не нажимая кнопку
                await self.redis.set_state(f"awaiting_name_{uid}", "true", ex=3600)
            return

        if text.strip().lower() in ("/privacy", "/политика", "политика", "мои данные"):
            await self._reset_modes(uid)
            await self.show_privacy(message)
            return

        command = text.strip().lower()
        if not command.startswith(("/parent", "/teacher", "/whoami", "/add_teacher", "/del_teacher")) \
                and not await self.redis.get_state(f"awaiting_parent_code_{uid}") \
                and not await self._is_teacher(uid) \
                and not await self.has_consent(uid) and not await self._get_grade(uid) \
                and not await self._children_of(uid):
            # Новый пользователь ещё не дал согласие: имя не запоминаем, в нейросеть ничего не отправляем.
            # Написал что-то вместо кнопки — считаем, что это ученик, и показываем согласие
            await self.ask_consent(message)
            return
        if command.startswith("/parent"):
            await self._reset_modes(uid)
            code = command[len("/parent"):].strip()
            if code:
                await self.link_parent(message, code)
            else:
                await self.show_parent_menu(message)
            return
        if command == "/teacher":
            await self.show_teacher_menu(message)
            return
        if command in ("/zap", "/молния") and await self._get_grade(uid):
            await self._reset_modes(uid)
            await self.show_lightning(message)
            return
        if command in ("/album", "/альбом") and await self._get_grade(uid):
            await self._reset_modes(uid)
            await self.show_album(message)
            return
        if command in ("/mayak", "/маяк") and await self._get_grade(uid):
            await self._reset_modes(uid)
            await self.show_lighthouse(message)
            return
        if command.startswith("/del_teacher"):
            await self.del_teacher(message, command[len("/del_teacher"):])
            return
        if command.startswith("/add_teacher"):
            await self.add_teacher(message, command[len("/add_teacher"):])
            return
        if command.startswith("/join"):
            await self._reset_modes(uid)
            code = text.strip()[len("/join"):].strip()
            if code:
                await self.join_class(message, code)
            else:
                await self.show_my_class(message)
            return
        if command == "/whoami":
            await self.answer(message, f"Твой ID в MAX: {uid}")
            return
        if command == "/voices" or command.startswith("/voice "):
            if not self._is_admin(uid):
                await self.answer(message, "Эта команда доступна только администратору бота.")
                return
            if command == "/voices":
                await self.show_voices(message)
            else:
                await self.choose_voice(message, command.split(maxsplit=1)[1])
            return
        if command.startswith("/demo_student"):
            if not self._is_admin(uid):
                await self.answer(message, "Эта команда доступна только администратору бота.")
                return
            await self.ask_demo_student(message, command[len("/demo_student"):].strip())
            return
        if command in ("/stats", "/demo", "/demo_report", "/demo_friday", "/demo_parent"):
            if not self._is_admin(uid):
                await self.answer(message, "Эта команда доступна только администратору бота.")
                return
            if command == "/stats":
                await self.show_stats(message)
            elif not await self._get_grade(uid):
                await self.answer(message, "Сначала выбери класс: /class")
            elif command == "/demo":
                await self.send_daily(message, regular=True)
            elif command == "/demo_friday":
                await self.send_daily(message, force_week=True)
            elif command == "/demo_parent":
                await self._parent_card(message, uid)
                await self.answer(message, await self.build_parent_report(uid))
            else:
                await self.send_report(message)
            return
        if command in ("/help", "помощь", "обучение", "как пользоваться"):
            await self._reset_modes(uid)
            if not await self._get_grade(uid):
                await self.answer(message, "Сначала напиши /start — познакомимся, и я всё покажу 🙂")
                return
            await self.start_tutorial(message)
            return
        if command in ("/today", "задания на сегодня"):
            await self._reset_modes(uid)
            if await self._daily_plan(uid):
                await self.continue_plan(message)
            else:
                await self.send_daily(message)
            return

        if text.strip().lower() in ("/name", "сменить имя"):
            await self._reset_modes(uid)
            await self.ask_new_name(message)
            return
        if text.strip().lower() in ("/delete_me", "удалить мои данные"):
            await self._reset_modes(uid)
            await self.confirm_delete(message)
            return

        # Смена класса и возврат в меню командами
        if text.strip().lower() in ("/class", "/menu", "сменить класс"):
            name = await self.redis.get_state(f"name_{uid}") or "друг"
            await self.send_main_menu(message, name)
            return

        # Учитель: имя для учеников, код чужого класса, название нового класса, тема задания; ученик: код класса
        fc_subject = await self.redis.get_state(f"awaiting_fc_topic_{uid}")
        if fc_subject:
            await self.fc_make_deck(message, fc_subject, text)
            return
        if await self.redis.get_state(f"awaiting_tname_{uid}") == "true":
            await self.set_teacher_name(message, text)
            return
        if await self.redis.get_state(f"awaiting_tlink_{uid}") == "true":
            await self.link_class(message, text)
            return
        if await self.redis.get_state(f"awaiting_tclass_{uid}"):
            await self.create_class(message, text)
            return
        pending_asg = load_json(await self.redis.get_state(f"awaiting_tasg_{uid}"), None)
        if pending_asg:
            await self.create_assignment(message, pending_asg[0], pending_asg[1], text)
            return
        if await self.redis.get_state(f"awaiting_join_{uid}") == "true":
            await self.join_class(message, text)
            return

        # 2. Ловим имя
        if await self.redis.get_state(f"awaiting_name_{uid}") == "true":
            name = text.strip()[:64]
            if name:
                await self.redis.set_state(f"name_{uid}", name, ex=GRADE_TTL)
                await self.redis.delete_state(f"awaiting_name_{uid}")
                await self._save_profile(uid, name=name)
                grade = await self._get_grade(uid)
                if await self.redis.get_state(f"renaming_{uid}") == "true" and grade:
                    await self.redis.delete_state(f"renaming_{uid}")
                    await self.show_subjects(message, grade, intro=f"Приятно познакомиться заново, {name}! 😊")
                    return
                await self.send_main_menu(message, name)
                return

        # Город или время для часового пояса
        tz_then = await self.redis.get_state(f"awaiting_tz_{uid}")
        if tz_then:
            await self.timezone_from_text(message, text.strip(), tz_then)
            return

        # Родитель вводит код или расписание за ребёнка
        if await self.redis.get_state(f"awaiting_parent_code_{uid}") == "true":
            await self.link_parent(message, text)
            return
        parent_child = await self.redis.get_state(f"awaiting_parent_sched_{uid}")
        if parent_child:
            await self.parent_schedule_input(message, int(parent_child), text=text)
            return

        # Задание для «Помощи с домашкой», написанное текстом
        if await self.redis.get_state(f"awaiting_homework_{uid}") == "true":
            await self.homework_from_text(message, text.strip())
            return

        # Автор учебника, написанный словами
        tb_subject = await self.redis.get_state(f"awaiting_textbook_{uid}")
        if tb_subject:
            await self.textbook_from_text(message, text.strip(), tb_subject)
            return

        # Расписание, написанное текстом
        if await self.redis.get_state(f"awaiting_schedule_{uid}") == "true":
            await self.schedule_from_text(message, text)
            return

        # Тема сегодняшнего урока (по расписанию)
        lesson_sid = await self.redis.get_state(f"awaiting_lesson_topic_{uid}")
        if lesson_sid:
            grade = await self._get_grade(uid)
            await self.redis.delete_state(f"awaiting_lesson_topic_{uid}")
            if grade:
                await self.start_lesson(message, grade, lesson_sid, text.strip())
                return

        # Ответ на вопрос диагностики
        if await self.redis.get_state(f"diag_mode_{uid}") == "true":
            await self._diag_record(message, text.strip())
            return

        # Ответ на вопрос теста по предмету (номер варианта или сам вариант)
        if await self.redis.get_state(f"stest_mode_{uid}") == "true":
            await self.test_answer_text(message, text.strip())
            return

        # 3. Предмет для ЭКЗАМЕНА, написанный словами
        if await self.redis.get_state(f"awaiting_exam_subject_{uid}") == "true":
            exam = await self.redis.get_state(f"exam_type_{uid}") or "oge"
            grade = await self._get_grade(uid)
            if grade is None or exam not in EXAM_NAMES:
                name = await self.redis.get_state(f"name_{uid}") or "друг"
                await self.send_main_menu(message, name)
                return

            subject_id = detect_subject(text)
            if subject_id is None:
                await self._send_with_voice(
                    message,
                    "Я не знаю такой предмет. Нажми на кнопку выше или напиши, например: Математика, Русский, Физика, История...",
                    keyboard=_kb([[_back(f"subjects_{grade}")]]),
                )
                return

            await self.redis.delete_state(f"awaiting_exam_subject_{uid}")
            await self._deliver_task(
                message, "exam", grade, subject_id, back_payload=f"exam_{exam}_{grade}", exam=exam
            )
            return

        # 4. Тема для ОБЫЧНОЙ учёбы
        if await self.redis.get_state(f"awaiting_topic_{uid}") == "true":
            subject_id = await self.redis.get_state(f"current_subj_id_{uid}")
            grade = int(await self.redis.get_state(f"current_grade_{uid}") or 0) or await self._get_grade(uid)
            if not subject_id or not grade:
                name = await self.redis.get_state(f"name_{uid}") or "друг"
                await self.send_main_menu(message, name)
                return

            topic = text.strip()
            await self.redis.delete_state(f"awaiting_topic_{uid}")
            if topic.lower() == "тест":
                await self._deliver_task(
                    message, "regular", grade, subject_id, back_payload=f"subj_{subject_id}_{grade}", topic=None
                )
                return
            # Тема написана — проводим урок: объяснение → пробный пример → задания на оценку
            await self.start_lesson(message, grade, subject_id, topic)
            return

        # 5. Проверка ответа на задание
        if await self.redis.get_state(f"task_active_{uid}") == "true":
            await self._check_answer(message, text)
            return

        # 6. Обычный диалог
        grade = await self._get_grade(uid)
        saved_history = await self.redis.get_state(uid)
        history: list = []
        if saved_history and saved_history.startswith("history_"):
            try:
                history = json.loads(saved_history[8:])
            except ValueError:
                history = []

        history.append({"role": "user", "content": text})
        back = f"subjects_{grade}" if grade else "back_to_menu"
        try:
            feedback = await ai_client.chat_with_mentor(history, context=await self._context(uid))
            await self._send_with_voice(message, feedback, keyboard=_kb([[_back(back)]]))
        except Exception as e:
            logger.error("Ошибка диалога: %s", e)
            feedback = "Извини, я немного задумался. Давай попробуем ещё раз!"
            await self._send_with_voice(message, feedback)

        history.append({"role": "assistant", "content": feedback})
        if len(history) > 15:
            history = history[-15:]
        await self.redis.set_state(uid, f"history_{json.dumps(history, ensure_ascii=False)}", ex=3600)

    async def handle_callback(self, message: NormalizedMessage):
        payload = message.payload or ""
        uid = message.user_id
        name = await self.redis.get_state(f"name_{uid}") or "друг"

        # «🔊 Послушать»: озвучить сообщение, ничего не сбрасывая (можно слушать посреди задания)
        if payload.startswith("say_"):
            await self.say_again(message, payload[4:])
            return

        if payload == "consent_yes":
            await self.give_consent(message)
            return
        if payload == "privacy":
            await self.show_privacy(message)
            return

        if payload == "role_student":
            await self.choose_student(message)
            return
        if payload == "role_teacher":
            await self.choose_teacher(message)
            return
        if payload.startswith("t_ok_") or payload.startswith("t_no_"):
            await self.answer_teacher_request(message, int(payload[5:]), payload.startswith("t_ok_"))
            return

        # Кнопка с вариантом ответа: режимы НЕ сбрасываем — это ответ на текущее задание или вопрос
        if payload.startswith("ans_") and payload[4:].isdigit():
            await self._touch_activity(message)
            if await self.answer_with_option(message, int(payload[4:])):
                return
            logger.warning("Не удалось обработать кнопку: устаревший вариант ответа %s", payload)
            await self._reset_modes(uid)
            grade = await self._get_grade(uid)
            if grade:
                await self.show_subjects(message, grade, intro="Это задание уже закончилось 🙂")
            else:
                await self.send_main_menu(message, name)
            return

        # «🎨 Объясни по-другому» и увлечения: задание (если оно идёт) остаётся активным — режимы не сбрасываем
        if payload in ("via", "via_task", "interests") or payload.startswith(("via_", "int_")):
            try:
                if payload == "via":
                    await self.ask_via(message)
                elif payload == "via_task":
                    await self.back_to_task(message)
                elif payload == "interests":
                    await self.ask_interests(message)
                elif payload.startswith("int_"):
                    await self.toggle_interest(message, payload[len("int_"):])
                else:
                    await self.explain_via(message, payload[len("via_"):])
            except ValueError as e:
                logger.warning("Кнопка %s: %s", payload, e)
                await self.ask_interests(message)
            return

        # «Объясни тему» во время задания: задание остаётся активным
        if payload == "explain":
            await self._touch_activity(message)
            if await self.explain_current_topic(message):
                return
            logger.warning("Не удалось обработать кнопку: объяснение для закончившегося задания")
            grade = await self._get_grade(uid)
            if grade:
                await self.show_subjects(message, grade, intro="Это задание уже закончилось 🙂")
            else:
                await self.send_main_menu(message, name)
            return

        # Ответ в тесте по предмету: тоже без сброса режимов
        if payload.startswith("sta_"):
            await self._touch_activity(message)
            if await self.test_answer_button(message, payload):
                return
            logger.warning("Не удалось обработать кнопку: устаревший ответ теста %s", payload)
            grade = await self._get_grade(uid)
            if grade:
                await self.show_subjects(message, grade, intro="Этот тест уже закончился 🙂")
            else:
                await self.send_main_menu(message, name)
            return

        await self._touch_activity(message)

        # Любая кнопка сбрасывает режимы ожидания
        await self._reset_modes(uid)

        try:
            if payload in ("back_to_menu", "menu"):
                # Старые кнопки из прошлых сообщений: ведём на предметы класса, если класс известен
                grade = await self._get_grade(uid)
                if grade:
                    await self.show_subjects(message, grade)
                else:
                    await self.send_main_menu(message, name)

            elif payload == "more":  # «Ещё задание» того же типа
                raw_ctx = await self.redis.get_state(f"task_ctx_{uid}")
                if not raw_ctx:
                    raise ValueError("контекст задания устарел")
                ctx = json.loads(raw_ctx)
                await self._deliver_task(
                    message, ctx["kind"], int(ctx["grade"]), ctx["subject_id"],
                    back_payload=ctx["back"], exam=ctx.get("exam"), topic=ctx.get("topic"),
                )

            elif payload.startswith("grade_"):  # выбор класса из списка
                grade = int(payload[len("grade_"):])
                if grade not in SUBJECTS:
                    raise ValueError(f"неизвестный класс {grade}")
                await self.redis.set_state(f"grade_{uid}", str(grade), ex=GRADE_TTL)
                await self._save_profile(uid, grade=grade)
                if (await self._load_profile(uid)).get("tz_offset") is None:
                    # Один раз спрашиваем город, чтобы задания приходили по местному времени
                    await self.ask_timezone(message, f"g{grade}")
                else:
                    await self.after_grade_chosen(message, grade)

            elif payload.startswith("tz_"):
                await self.set_timezone(message, payload)

            elif payload == "weektest":  # пятничный мини-тест недели
                choice = load_json(await self.redis.get_state(f"weektest_{uid}"), None)
                grade = await self._get_grade(uid)
                if not choice or not grade:
                    raise ValueError("мини-тест недели устарел")
                await self.start_subject_test(message, grade, choice["subject_id"], focus=choice["topics"], weekly=True)

            elif payload == "plan_regular":
                await self.send_daily(message, regular=True)

            elif payload.startswith("prog_"):  # программа по предмету
                grade = await self._get_grade(uid)
                if not grade:
                    raise ValueError("класс не выбран")
                await self.show_program(message, grade, payload[len("prog_"):])

            elif payload.startswith(("tb_set_", "tb_other_", "tb_none_")):
                await self.textbook_button(message, payload)

            elif payload.startswith("tb_"):  # выбор учебника
                grade = await self._get_grade(uid)
                if not grade:
                    raise ValueError("класс не выбран")
                await self.show_textbooks(message, grade, payload[len("tb_"):])

            elif payload.startswith("lesson_now_"):
                await self.lesson_now(message, payload[len("lesson_now_"):])

            elif payload == "parent_start":
                await self.ask_parent_code(message)

            elif payload == "parent_menu":
                await self.show_parent_menu(message)

            elif payload.startswith("parent_rep_"):
                await self.parent_report_button(message, int(payload[len("parent_rep_"):]))

            elif payload == "parent_sch_ok":
                await self.parent_schedule_confirm(message)

            elif payload.startswith("parent_sch_"):
                await self.parent_schedule(message, int(payload[len("parent_sch_"):]))

            elif payload == "parent_code":
                await self.show_parent_code(message)

            elif payload.startswith("parent_leave_yes_"):
                await self.leave_child(message, int(payload[len("parent_leave_yes_"):]))

            elif payload.startswith("parent_leave_"):
                await self.ask_leave_child(message, int(payload[len("parent_leave_"):]))

            elif payload.startswith("parent_unlink_") and payload[len("parent_unlink_"):].isdigit():
                await self.unlink_parent(message, int(payload[len("parent_unlink_"):]))

            elif payload == "parent_unlink_all":
                await self.unlink_all_parents(message)

            elif payload.startswith("demo_yes_"):
                await self.fill_demo_student(message, int(payload[len("demo_yes_"):]))

            elif payload == "today":   # «📅 Что сегодня» — как команда /today
                if await self._daily_plan(uid):
                    await self.continue_plan(message)
                else:
                    await self.send_daily(message)

            elif payload == "hw":
                await self.ask_homework(message)

            elif payload == "hw_file":
                url, fname, kind = load_json(await self.redis.get_state(f"last_file_{uid}"), [None, None, None])
                if url:
                    await self.homework_from_file(message, url, fname, kind)
                else:
                    await self.ask_homework(message)

            elif payload.startswith("hw_pick_"):
                await self.homework_pick(message, int(payload[len("hw_pick_"):]))

            elif payload == "sched":
                await self.show_schedule(message)

            elif payload.startswith("sch_d_"):
                await self.show_schedule_day(message, int(payload[len("sch_d_"):]))

            elif payload.startswith("sch_"):
                await self.schedule_button(message, payload)

            elif payload.startswith("entry_"):  # входной тест по предмету
                subject_id, grade_str = payload[len("entry_"):].rsplit("_", 1)
                grade = int(grade_str)
                if grade not in SUBJECTS or subject_id not in DEFAULT_TITLES:
                    raise ValueError(f"неверный payload {payload}")
                await self.start_subject_test(message, grade, subject_id, mode="entry")

            elif payload.startswith("lesson_ask_"):
                await self.ask_lesson_topic(message, payload[len("lesson_ask_"):])

            elif payload.startswith("lesson_pick_"):
                await self.lesson_pick(message, int(payload[len("lesson_pick_"):]))

            elif payload == "lesson_trial":
                await self.lesson_trial(message)

            elif payload == "lesson_graded":
                await self.lesson_graded(message)

            elif payload == "tzset":
                await self.ask_timezone(message, "n")

            elif payload.startswith("diag_pick_"):
                await self.toggle_diag_pick(message, payload[len("diag_pick_"):])

            elif payload == "diag_go":
                await self.start_picked_diagnostic(message)

            elif payload.startswith("diag_subj_"):
                subject_id, grade_str = payload[len("diag_subj_"):].rsplit("_", 1)
                grade = int(grade_str)
                if grade not in SUBJECTS or subject_id not in DEFAULT_TITLES:
                    raise ValueError(f"неверный payload {payload}")
                await self.start_diagnostic(message, grade, subjects=[subject_id], mode="subject")

            elif payload.startswith("diag_start_"):
                grade = int(payload[len("diag_start_"):])
                if grade not in SUBJECTS:
                    raise ValueError(f"неизвестный класс {grade}")
                await self.start_diagnostic(message, grade)

            elif payload.startswith("diag_skip_"):
                grade = int(payload[len("diag_skip_"):])
                if grade not in SUBJECTS:
                    raise ValueError(f"неизвестный класс {grade}")
                await self._save_profile(uid, diagnostic_status="skipped", diagnostic_grade=grade)
                await self.show_subjects(
                    message, grade, first=True,
                    intro="Хорошо! Пройти диагностику можно в любой момент: кнопка «📊 Прогресс».",
                )

            elif payload in ("diag_idk", "diag_pause", "diag_resume"):
                state = await self._diag_state(uid)
                if not state:
                    raise ValueError("диагностика не начата или устарела")
                if payload == "diag_idk":
                    await self._diag_record(message, None)
                elif payload == "diag_pause":
                    await self.show_subjects(
                        message, state["grade"],
                        intro=f"Хорошо, {name}! Прогресс сохранён: ты ответил на {state['index']} из "
                              f"{len(state['ids'])}. Вернуться к тесту можно кнопкой «▶️ Продолжить тест».",
                    )
                else:
                    await self._diag_ask(message, state)

            elif payload == "lh":
                await self.show_lighthouse(message)

            elif payload == "fc":
                await self.show_flashcards(message)

            elif payload == "fc_new":
                await self.fc_choose_subject(message)

            elif payload.startswith("fc_s_"):
                await self.fc_choose_topic(message, payload[len("fc_s_"):])

            elif payload.startswith("fc_t_"):
                await self.fc_topic_button(message, int(payload[len("fc_t_"):]))

            elif payload == "fc_go":
                await self.fc_start(message)

            elif payload == "fc_show":
                await self.fc_show(message)

            elif payload in ("fc_yes", "fc_no"):
                await self.fc_answer(message, payload == "fc_yes")

            elif payload == "fc_stop":
                await self.fc_stop(message)

            elif payload == "games":
                await self.show_games(message)

            elif payload.startswith("subjlist_"):
                grade = int(payload[len("subjlist_"):])
                if grade not in SUBJECTS:
                    raise ValueError(f"неизвестный класс {grade}")
                await self.show_subject_list(message, grade)

            elif payload == "zap":
                await self.show_lightning(message)

            elif payload.startswith("zap_go_"):
                await self.start_lightning(message, payload[len("zap_go_"):])

            elif payload.startswith("zap_a_"):
                await self.answer_lightning(message, payload)

            elif payload == "zap_stop":
                await self.stop_lightning(message)

            elif payload == "chest":
                await self.open_chest(message)

            elif payload == "album":
                await self.show_album(message)

            elif payload.startswith("rewards_"):
                grade = int(payload[len("rewards_"):])
                if grade not in SUBJECTS:
                    raise ValueError(f"неизвестный класс {grade}")
                await self.show_rewards(message, grade)

            elif payload == "tutorial":
                await self.start_tutorial(message)

            elif payload == "tut_end":
                await self.finish_tutorial(message)

            elif payload.startswith("tut_tap_"):
                await self.tutorial_tap(message, int(payload[len("tut_tap_"):]))

            elif payload.startswith("tut_q_"):
                index, option = payload[len("tut_q_"):].split("_")
                await self.tutorial_quiz(message, int(index), int(option))

            elif payload.startswith("tut_"):
                await self.show_tutorial_step(message, int(payload[len("tut_"):]))

            elif payload == "plan_next":
                await self.continue_plan(message)

            elif payload == "plan_skip":
                await self.continue_plan(message, skipped=True)

            elif payload == "plan_resume":
                grade = await self._get_grade(uid)
                if not grade:
                    raise ValueError("класс не выбран")
                await self.show_subjects(message, grade, intro=f"Ура, ты вернулся, {name}! 🎉 Продолжим?")

            elif payload == "notify_settings":
                await self.show_notify_settings(message)

            elif payload == "notify_toggle" or payload.startswith(("dtime_", "rtime_")):
                await self.set_notify(message, payload)

            elif payload.startswith("settings_"):
                grade = int(payload[len("settings_"):])
                if grade not in SUBJECTS:
                    raise ValueError(f"неизвестный класс {grade}")
                await self.show_settings(message, grade)

            elif payload == "set_name":
                await self.ask_new_name(message)

            elif payload == "set_class":
                await self.send_main_menu(message, name)

            elif payload == "voice_toggle":
                await self.toggle_voice(message)

            elif payload == "t_menu":
                await self.show_teacher_menu(message)
            elif payload in ("t_kind_primary", "t_kind_subject"):
                await self.set_teacher_kind(message, payload[len("t_kind_"):])
            elif payload == "t_kind":
                await self.ask_teacher_kind(message)
            elif payload == "t_subjects":
                await self.ask_teacher_subjects(message)
            elif payload == "t_subj_done":
                await self.teacher_subjects_done(message)
            elif payload.startswith("t_subj_"):
                await self.toggle_teacher_subject(message, payload[len("t_subj_"):])
            elif payload == "t_rename":
                await self.ask_teacher_name(message)
            elif payload == "t_prof":
                await self.show_teacher_profile(message)
            elif payload == "t_add":
                await self.ask_add_class(message)
            elif payload == "t_link":
                await self.ask_link_class(message)
            elif payload.startswith("t_unlink_"):
                await self.unlink_class(message, payload[len("t_unlink_"):])
            elif payload == "t_new":
                await self.ask_new_class(message)
            elif payload.startswith("t_new_"):
                await self.ask_class_name(message, int(payload[len("t_new_"):]))
            elif payload.startswith("t_name_"):
                grade, name = payload[len("t_name_"):].split("_", 1)
                await self.create_class(message, name, int(grade))
            elif payload.startswith("t_cls_"):
                await self.show_class(message, payload[len("t_cls_"):])
            elif payload.startswith("t_stu_"):
                await self.show_students(message, payload[len("t_stu_"):])
            elif payload.startswith("t_rep_"):
                code, student = payload[len("t_rep_"):].split("_", 1)
                await self.show_student_report(message, code, int(student))
            elif payload.startswith("t_weak_"):
                await self.show_class_weak(message, payload[len("t_weak_"):])
            elif payload.startswith("t_asgs_"):
                code, sid = payload[len("t_asgs_"):].split("_", 1)
                await self.ask_assignment_topic(message, code, sid)
            elif payload.startswith("t_asgt_"):
                await self.assignment_topic_index(message, int(payload[len("t_asgt_"):]))
            elif payload.startswith("t_asgw_"):
                code, index = payload[len("t_asgw_"):].split("_", 1)
                await self.assignment_from_weak(message, code, int(index))
            elif payload.startswith("t_asg_"):
                await self.ask_assignment_subject(message, payload[len("t_asg_"):])
            elif payload.startswith("t_resd_"):
                code, aid = payload[len("t_resd_"):].split("_", 1)
                await self.show_result_detail(message, code, aid)
            elif payload.startswith("t_res_"):
                await self.show_results(message, payload[len("t_res_"):])
            elif payload.startswith("t_ph_"):
                code, aid, student = payload[len("t_ph_"):].split("_", 2)
                await self.show_assignment_photo(message, code, aid, int(student))
            elif payload.startswith("t_rem_"):
                code, aid = payload[len("t_rem_"):].split("_", 1)
                await self.remind_assignment(message, code, aid)
            elif payload.startswith("t_code_"):
                await self.show_class_code(message, payload[len("t_code_"):])
            elif payload.startswith("t_delyes_"):
                await self.delete_class(message, payload[len("t_delyes_"):])
            elif payload.startswith("t_del_"):
                await self.ask_delete_class(message, payload[len("t_del_"):])
            elif payload.startswith("asg_"):
                code, aid = payload[len("asg_"):].split("_", 1)
                await self.start_assignment(message, code, aid)
            elif payload == "sc_my":
                await self.show_my_class(message)
            elif payload == "sc_leave":
                await self.leave_class(message)

            elif payload == "delete_me":
                await self.confirm_delete(message)

            elif payload == "delete_me_yes":
                await self.delete_user_data(message)

            elif payload.startswith("progress_"):
                grade = int(payload[len("progress_"):])
                if grade not in SUBJECTS:
                    raise ValueError(f"неизвестный класс {grade}")
                await self.show_progress(message, grade)

            elif payload.startswith("subjects_"):  # «Назад» к предметам
                grade = int(payload[len("subjects_"):])
                if grade not in SUBJECTS:
                    raise ValueError(f"неизвестный класс {grade}")
                await self.show_subjects(message, grade)

            elif payload.startswith("exvarl_"):
                _, exam, grade_str = payload.split("_")
                grade = int(grade_str)
                if exam not in ("oge", "ege") or grade not in SUBJECTS:
                    raise ValueError(f"неверная кнопка {payload}")
                await self.show_variant_subjects(message, exam, grade)

            elif payload.startswith("exvar_"):
                _, exam, grade_str, subject_id = payload.split("_", 3)
                grade = int(grade_str)
                if exam not in ("oge", "ege") or grade not in SUBJECTS or subject_id not in DEFAULT_TITLES:
                    raise ValueError(f"неверная кнопка {payload}")
                await self.start_subject_test(message, grade, subject_id, mode=f"exam_{exam}")

            elif payload.startswith("exam_"):
                _, exam, grade_str = payload.split("_")
                grade = int(grade_str)
                if exam not in EXAM_NAMES or grade not in SUBJECTS:
                    raise ValueError(f"неверный payload {payload}")
                await self.show_exam_subjects(message, exam, grade)

            elif payload.startswith("exsub_"):
                _, exam, grade_str, subject_id = payload.split("_", 3)
                grade = int(grade_str)
                if exam not in EXAM_NAMES or grade not in SUBJECTS or subject_id not in DEFAULT_TITLES:
                    raise ValueError(f"неверный payload {payload}")
                await self.redis.set_state(f"grade_{uid}", str(grade), ex=GRADE_TTL)
                await self._deliver_task(
                    message, "exam", grade, subject_id, back_payload=f"exam_{exam}_{grade}", exam=exam
                )

            elif payload == "st_stop":
                await self.stop_test(message)

            elif payload.startswith("st_fix_"):
                await self.fix_test_question(message, int(payload[len("st_fix_"):]))

            elif payload.startswith("subjtest_"):  # сгенерированный тест по предмету
                subject_id, grade_str = payload[len("subjtest_"):].rsplit("_", 1)
                grade = int(grade_str)
                if grade not in SUBJECTS or subject_id not in DEFAULT_TITLES:
                    raise ValueError(f"неверный payload {payload}")
                await self.redis.set_state(f"grade_{uid}", str(grade), ex=GRADE_TTL)
                await self.start_subject_test(message, grade, subject_id)

            elif payload.startswith("subjtask_"):  # одно задание на тему, которую пора повторить
                subject_id, grade_str = payload[len("subjtask_"):].rsplit("_", 1)
                grade = int(grade_str)
                if grade not in SUBJECTS or subject_id not in DEFAULT_TITLES:
                    raise ValueError(f"неверный payload {payload}")
                await self.redis.set_state(f"grade_{uid}", str(grade), ex=GRADE_TTL)
                # Сначала темы, которые пора повторить (и слабые из диагностики), иначе — тема на выбор ИИ
                topic, note = await self._pick_review_topic(uid, grade, subject_id)
                await self._deliver_task(
                    message, "regular", grade, subject_id, back_payload=f"subj_{subject_id}_{grade}",
                    topic=topic, note=note,
                )

            elif payload.startswith("subj_"):
                # payload вида subj_<id>_<класс>; сам id может содержать «_» (например, geo_math)
                subject_id, grade_str = payload[len("subj_"):].rsplit("_", 1)
                grade = int(grade_str)
                if grade not in SUBJECTS or subject_id not in DEFAULT_TITLES:
                    raise ValueError(f"неверный payload {payload}")
                await self.ask_topic(message, subject_id, grade)

            else:
                raise ValueError(f"неизвестный payload {payload!r}")

        except (ValueError, IndexError, KeyError) as e:
            logger.warning("Не удалось обработать кнопку: %s", e)
            grade = await self._get_grade(uid)
            if grade:
                await self.show_subjects(message, grade)
            else:
                await self.send_main_menu(message, name)
