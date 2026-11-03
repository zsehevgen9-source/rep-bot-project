"""«🗓 Расписание уроков»: ученик (или родитель) вносит уроки по дням — кнопками или текстом.

По расписанию бот в 16:00 предлагает закрепить то, что было на сегодняшних уроках.
"""
from app.handlers.tutor.common import *  # noqa: F401,F403
from app.services import schedule as schedule_rules
from app.services.documents import describe_bytes, document_text, image_mime, to_jpeg


class ScheduleMixin:
    async def _schedule(self, uid: int) -> Dict[str, List[str]]:
        data = load_json((await self._load_profile(uid)).get("schedule"), {})
        return data if isinstance(data, dict) else {}

    async def _save_schedule(self, uid: int, schedule: Dict[str, List[str]]) -> None:
        await self._save_profile(uid, schedule=json.dumps(schedule, ensure_ascii=False))

    async def today_lessons(self, uid: int) -> List[str]:
        profile = await self._load_profile(uid)
        weekday = daily_rules.local_now(topic_progress.now(), profile.get("tz_offset")).weekday()
        return schedule_rules.lessons_on(await self._schedule(uid), weekday)

    def _week_text(self, grade: int, schedule: Dict[str, List[str]]) -> str:
        lines = []
        for i, name in enumerate(schedule_rules.DAY_NAMES):
            lessons = schedule.get(str(i), [])
            if lessons or i < 5:
                titles = ", ".join(subject_title(grade, sid) for sid in lessons) or "—"
                lines.append(f"{schedule_rules.DAYS[i]}: {titles}")
        return "\n".join(lines)

    async def show_schedule(self, message: NormalizedMessage, note: str = "") -> None:
        uid = message.user_id
        grade = await self._get_grade(uid)
        if not grade:
            await self.send_main_menu(message, await self.redis.get_state(f"name_{uid}") or "друг")
            return
        schedule = await self._schedule(uid)
        rows = _fit_rows([_btn(day, f"sch_d_{i}") for i, day in enumerate(schedule_rules.DAYS)], max_per_row=3)
        rows.append([_btn("📷 Фото, файл или текст", "sch_text")])
        if schedule:
            rows.append([_btn("🧹 Очистить всё", "sch_clear")])
        rows.append([_back(f"subjects_{grade}")])
        text = (f"{note}\n\n" if note else "") + "🗓 Расписание уроков\n\n" + self._week_text(grade, schedule) + (
            "\n\nНажми на день и выбери уроки по порядку — или нажми «📷 Фото, файл или текст»: можно прислать "
            "фото расписания, скриншот из электронного дневника, файл Word или Excel. "
            "По расписанию я буду помогать закрепить то, что было на уроках.\n"
            "В начальной школе расписание удобно внести вместе с родителями 🙂"
        )
        await self._quiet(
            message, text, keyboard=_kb(rows),
            voice_text="Расписание уроков. Нажми на день недели и выбери уроки по порядку. Или напиши расписание текстом.",
        )

    async def show_schedule_day(self, message: NormalizedMessage, day: int, note: str = "") -> None:
        uid = message.user_id
        grade = await self._get_grade(uid)
        if not grade or not 0 <= day < len(schedule_rules.DAYS):
            await self.show_schedule(message)
            return
        lessons = (await self._schedule(uid)).get(str(day), [])
        listing = "\n".join(f"{n}. {subject_title(grade, sid)}" for n, sid in enumerate(lessons, 1)) or "пока пусто"
        buttons = [_btn(button_title(name), f"sch_a_{day}_{sid}") for name, sid in SUBJECTS[grade]]
        rows = _fit_rows(buttons, max_per_row=2)
        if lessons:
            rows.append([_btn("↩️ Убрать последний", f"sch_u_{day}")])
        rows.append([_btn("✅ Готово", "sched")])
        text = (f"{note}\n" if note else "") + (
            f"🗓 {schedule_rules.DAY_NAMES[day].capitalize()}\n\n{listing}\n\n"
            "Нажимай предметы в том порядке, как идут уроки. Физкультуру, музыку и другие уроки без заданий пропускай."
        )
        await self.answer(message, text, keyboard=_kb(rows))

    async def schedule_button(self, message: NormalizedMessage, payload: str) -> None:
        uid = message.user_id
        grade = await self._get_grade(uid)
        schedule = await self._schedule(uid)
        if payload.startswith("sch_a_"):
            day_str, sid = payload[len("sch_a_"):].split("_", 1)
            if not grade or sid not in [s for _, s in SUBJECTS[grade]]:
                await self.show_schedule_day(message, int(day_str), note="Этой кнопки уже нет — выбери предмет ниже 🙂")
                return
            lessons = schedule.setdefault(str(int(day_str)), [])
            if len(lessons) >= 8:
                await self.show_schedule_day(message, int(day_str), note="В день не больше 8 уроков 🙂")
                return
            lessons.append(sid)
            await self._save_schedule(uid, schedule)
            await self.show_schedule_day(message, int(day_str))
        elif payload.startswith("sch_u_"):
            day = int(payload[len("sch_u_"):])
            if schedule.get(str(day)):
                schedule[str(day)].pop()
                if not schedule[str(day)]:
                    del schedule[str(day)]
                await self._save_schedule(uid, schedule)
            await self.show_schedule_day(message, day)
        elif payload == "sch_ok":
            pending = load_json(await self.redis.get_state(f"sched_pending_{uid}"), None)
            if not isinstance(pending, dict) or not pending:
                await self.show_schedule(message)
                return
            await self.redis.delete_state(f"sched_pending_{uid}")
            schedule.update(pending)
            await self._save_schedule(uid, schedule)
            await self.show_schedule(message, note="✅ Записал расписание!")
        elif payload == "sch_fix":
            pending = load_json(await self.redis.get_state(f"sched_pending_{uid}"), None)
            await self.redis.delete_state(f"sched_pending_{uid}")
            if isinstance(pending, dict) and pending:
                schedule.update(pending)
                await self._save_schedule(uid, schedule)
            await self.show_schedule(message, note="Записал — теперь поправь нужный день: нажми на него.")
        elif payload == "sch_file":
            # Файл пришёл, когда расписание не ждали: ученик подтвердил, что это расписание
            url, name, kind = load_json(await self.redis.get_state(f"last_file_{uid}"), [None, None, None])
            if not url:
                await self.show_schedule(message)
                return
            await self.schedule_from_file(message, url, name, kind)
        elif payload == "sch_clear":
            await self._save_schedule(uid, {})
            await self.show_schedule(message, note="Расписание очищено.")
        elif payload == "sch_text":
            await self.redis.set_state(f"awaiting_schedule_{uid}", "true", ex=3600)
            await self._send_with_voice(
                message,
                "📷 Пришли фото расписания или скриншот из электронного дневника — или файл Word / Excel.\n\n"
                "✍️ Или напиши расписание на неделю, например:\n"
                "Пн: математика, русский, чтение\nВт: русский, математика, окружающий мир\n…\n\n"
                "Можно и столбиком — как в дневнике. Уроки без заданий (физкультуру, музыку) я пропущу.",
                keyboard=_kb([[_back("sched")]]),
                voice_text="Пришли фото расписания или файл. Или напиши расписание на неделю, "
                           "например: понедельник — математика, русский, чтение.",
            )
        else:
            raise ValueError(f"неизвестная кнопка расписания {payload}")

    async def read_upload(self, url: str, name: Optional[str], kind: Optional[str]):
        """Текст из присланного фото, скриншота, PDF, Word или Excel. Возвращает (текст, скачанные байты)."""
        data = await (self.api.download_image(url) if kind == "image" else self.api.download_file(url))
        logger.info("Файл «%s» (%s): скачано %s", name, kind, describe_bytes(data))
        raw = ""
        if data:
            raw = document_text(data, name or "") or ""
            mime = image_mime(data)
            if not raw and mime != "application/pdf":
                # Фото приводим к JPEG разумного размера: так OCR принимает и WEBP, и огромные снимки с телефона
                jpeg = await to_jpeg(data)
                if jpeg:
                    data, mime = jpeg, "image/jpeg"
            if not raw and mime:
                raw = await ai_client.recognize_image_text(data, mime)
                logger.info("Распознано с фото: %d символов", len(raw))
            elif not raw:
                logger.warning("Файл не похож ни на фото, ни на документ: %s", describe_bytes(data))
        return raw, data

    async def schedule_from_file(self, message: NormalizedMessage, url: str, name: Optional[str], kind: Optional[str]) -> None:
        """Фото, скриншот, PDF, Word или Excel с расписанием → распознаём → показываем, что поняли, → «Всё верно»."""
        uid = message.user_id
        grade = await self._get_grade(uid)
        if not grade:
            await self.send_main_menu(message, await self.redis.get_state(f"name_{uid}") or "друг")
            return
        await self.answer(message, "Смотрю расписание… 🔎 Это займёт до минуты.")
        raw, data = await self.read_upload(url, name, kind)
        if not raw.strip() and kind == "image" and data and len(data) < 30_000:
            # MAX отдал боту только маленькое превью фото — читать нечего. Файлом приходит оригинал
            await self._send_with_voice(
                message,
                "Фото пришло ко мне совсем маленьким 🔍 — текст не разобрать. Пришли его, пожалуйста, как файл: "
                "нажми 📎 (скрепку) → «Файл» и выбери фото расписания. Так оно придёт в полном размере.",
                keyboard=_kb([[_btn("🗓 Внести кнопками", "sched")]]),
                voice_text="Фото пришло совсем маленьким. Пришли его как файл: нажми скрепку, потом «Файл», и выбери фото.",
            )
            return
        if not raw.strip():
            await self._send_with_voice(
                message,
                "Не получилось прочитать расписание 😕 Пришли фото поровнее и при хорошем свете, скриншот "
                "или файл Word / Excel — или внеси расписание кнопками.",
                keyboard=_kb([[_btn("🗓 Внести кнопками", "sched")]]),
            )
            return
        titles = [name for name, _ in SUBJECTS[grade]]
        lines = await ai_client.schedule_lines(raw, titles) or raw
        parsed, unknown = schedule_rules.parse_schedule(lines, [sid for _, sid in SUBJECTS[grade]])
        if not parsed:
            await self._send_with_voice(
                message,
                "Я прочитал файл, но не нашёл в нём расписания уроков 🤔 Пришли другое фото или внеси расписание кнопками.",
                keyboard=_kb([[_btn("🗓 Внести кнопками", "sched")]]),
            )
            return
        await self.redis.delete_state(f"awaiting_schedule_{uid}")
        await self.redis.set_state(f"sched_pending_{uid}", json.dumps(parsed, ensure_ascii=False), ex=3600)
        text = "📷 Вот что я понял:\n\n" + self._week_text(grade, parsed)
        if unknown:
            text += "\n\nПропустил уроки без заданий: " + ", ".join(unknown[:10]) + "."
        text += "\n\nВсё верно? Если что-то не так — нажми «✏️ Исправить» и поправь день кнопками."
        await self._send_with_voice(
            message, text, keyboard=_kb([[_btn("✅ Всё верно", "sch_ok")], [_btn("✏️ Исправить", "sch_fix")]]),
            voice_text="Вот что я понял. Проверь расписание на экране и нажми «Всё верно» или «Исправить».",
        )

    async def schedule_from_text(self, message: NormalizedMessage, text: str) -> None:
        uid = message.user_id
        grade = await self._get_grade(uid)
        if not grade:
            await self.redis.delete_state(f"awaiting_schedule_{uid}")
            return
        parsed, unknown = schedule_rules.parse_schedule(text, [sid for _, sid in SUBJECTS[grade]])
        if not parsed:
            await self._send_with_voice(
                message,
                "Не получилось разобрать расписание 🙈 Начни каждую строку с дня недели, например: "
                "«Пн: математика, русский».",
                keyboard=_kb([[_back("sched")]]),
            )
            return
        await self.redis.delete_state(f"awaiting_schedule_{uid}")
        schedule = await self._schedule(uid)
        schedule.update(parsed)
        await self._save_schedule(uid, schedule)
        note = "✅ Записал расписание!"
        if unknown:
            note += " Пропустил уроки без заданий или незнакомые: " + ", ".join(unknown[:8]) + "."
        await self.show_schedule(message, note=note)
