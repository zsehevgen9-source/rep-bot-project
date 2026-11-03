"""Программа по предмету и учебник: «📚 Программа» (темы по четвертям) и «📗 Учебник» (автор — отдельно по каждому предмету)."""
from app.handlers.tutor.common import *  # noqa: F401,F403
from app.services.programs import programs

QUARTER_NAMES = ["1 четверть", "2 четверть", "3 четверть", "4 четверть"]
STATUS_MARKS = {"mastered": "✅", "known": "✅", "learning": "🔄", "weak": "❗"}


def author_label(author: str) -> str:
    """Короткая подпись для кнопки: «Рудницкая (Начальная школа XXI века)» → «Рудницкая»."""
    short = author.split(" (")[0].strip()
    if _vis_len(short) <= BUTTON_MAX:
        return short
    # «Вигасин, Годер, Свенцицкая» → «Вигасин, Годер и др.»
    names = [n.strip() for n in short.split(",")]
    for count in range(len(names) - 1, 0, -1):
        label = ", ".join(names[:count]) + " и др."
        if _vis_len(label) <= BUTTON_MAX:
            return label
    return _short(short, BUTTON_MAX)


class ProgramMixin:
    async def _textbooks(self, uid: int) -> Dict[str, str]:
        data = load_json((await self._load_profile(uid)).get("textbooks"), {})
        return data if isinstance(data, dict) else {}

    async def _textbook(self, uid: int, subject_id: str) -> Optional[str]:
        return (await self._textbooks(uid)).get(subject_id) or None

    async def _local_date(self, uid: int):
        profile = await self._load_profile(uid)
        return daily_rules.local_now(topic_progress.now(), profile.get("tz_offset")).date()

    async def program_topic_now(self, uid: int, grade: int, subject_id: str) -> Optional[str]:
        topics = programs.current_topics(grade, subject_id, await self._local_date(uid))
        return topics[0] if topics else None

    async def program_passed(self, uid: int, grade: int, subject_id: str) -> List[str]:
        return programs.passed_topics(grade, subject_id, await self._local_date(uid))

    async def show_program(self, message: NormalizedMessage, grade: int, subject_id: str) -> None:
        uid = message.user_id
        quarters = programs.quarters(grade, subject_id)
        if not quarters:
            await self.ask_topic(message, subject_id, grade)
            return
        title = subject_title(grade, subject_id)
        today = await self._local_date(uid)
        current = programs.position(grade, subject_id, today)[1]
        progress = await self._load_progress(uid)
        school = programs.is_school(grade, subject_id)
        lines = [f"📚 {'Рабочая программа школы' if school else 'Программа'} · {title} · {grade} класс", ""]
        index = 0
        # Программа школы — поурочная и длинная: целиком показываем только текущую четверть
        long = sum(len(q) for q in quarters) > 40
        current_q = programs.position(grade, subject_id, today)[0]
        for q, topics in enumerate(quarters):
            if long and q != current_q:
                lines.append(f"{QUARTER_NAMES[q]}: {len(topics)} тем" + (" — пройдено" if q < current_q else ""))
                index += len(topics)
                continue
            lines.append(f"{QUARTER_NAMES[q]}:")
            for topic in topics:
                # в длинной программе — только темы рядом с текущей (сообщение MAX не длиннее 4000 знаков)
                if long and not -8 <= index - current <= 12:
                    if lines[-1] != "…":
                        lines.append("…")
                    index += 1
                    continue
                entry = progress.get(topic_progress.topic_key(grade, subject_id, topic)) or {}
                mark = STATUS_MARKS.get(entry.get("status"), "👉" if index == current else ("▫️" if index > current else "•"))
                lines.append(f"{mark} {topic}" + (" — сейчас по программе" if index == current else ""))
                index += 1
            lines.append("")
        textbook = await self._textbook(uid, subject_id)
        lines.append(f"📗 Учебник: {textbook or 'не выбран'}")
        lines.append("✅ знаешь · 🔄 изучаем · ❗ подтянуть · 👉 сейчас · ▫️ впереди\n"
                     + ("Темы и даты — из КТП учителя." if school else
                        "Темп у школ разный: если вы на другой теме — просто напиши её в предмете."))
        await self.answer(message, "\n".join(lines),
                          keyboard=_kb([[_btn("📘 Урок по программе", f"lesson_now_{subject_id}")],
                                        [_back(f"subj_{subject_id}_{grade}")]]))

    async def show_textbooks(self, message: NormalizedMessage, grade: int, subject_id: str) -> None:
        uid = message.user_id
        authors = programs.authors_for(grade, subject_id)
        title = subject_title(grade, subject_id)
        current = await self._textbook(uid, subject_id)
        rows = [[_btn(("✅ " if a == current else "") + author_label(a), f"tb_set_{subject_id}_{i}")]
                for i, a in enumerate(authors)]
        rows.append([_btn("✍️ Другой — напишу", f"tb_other_{subject_id}")])
        rows.append([_btn("🤷 Не знаю", f"tb_none_{subject_id}")])
        rows.append([_back(f"subj_{subject_id}_{grade}")])
        listing = "\n".join(f"• {a}" for a in authors)
        await self._send_with_voice(
            message,
            f"📗 По какому учебнику ты учишься по предмету «{title}»?\n"
            f"Сейчас: {current or 'не выбран'}\n\n{listing}\n\n"
            "Автор написан на обложке учебника. Если твоего нет в списке — нажми «✍️ Другой — напишу».",
            keyboard=_kb(rows),
            voice_text="По какому учебнику ты учишься? Автор написан на обложке. Выбери кнопкой или напиши.",
        )

    async def textbook_button(self, message: NormalizedMessage, payload: str) -> None:
        uid = message.user_id
        grade = await self._get_grade(uid)
        if not grade:   # данные удалены или класс не выбран
            await self.send_main_menu(message, await self.redis.get_state(f"name_{uid}") or "друг")
            return
        if payload.startswith("tb_set_"):
            subject_id, index = payload[len("tb_set_"):].rsplit("_", 1)
            authors = programs.authors_for(grade or 0, subject_id)
            if not index.isdigit() or int(index) >= len(authors):
                # кнопка от другого класса — показываем список заново (или предмет, если программы нет)
                if authors:
                    await self.show_textbooks(message, grade, subject_id)
                else:
                    await self.ask_topic(message, subject_id, grade)
                return
            await self._save_textbook(message, grade, subject_id, authors[int(index)])
        elif payload.startswith("tb_other_"):
            subject_id = payload[len("tb_other_"):]
            await self.redis.set_state(f"awaiting_textbook_{uid}", subject_id, ex=3600)
            await self._send_with_voice(
                message, "Напиши фамилию автора учебника — как на обложке (например: «Моро» или «Петерсон»).",
                keyboard=_kb([[_back(f"subj_{subject_id}_{grade}")]]),
            )
        elif payload.startswith("tb_none_"):
            subject_id = payload[len("tb_none_"):]
            books = await self._textbooks(uid)
            books.pop(subject_id, None)
            await self._save_profile(uid, textbooks=json.dumps(books, ensure_ascii=False))
            await self.ask_topic(message, subject_id, grade)
        else:
            raise ValueError(f"неизвестная кнопка учебника {payload}")

    async def textbook_from_text(self, message: NormalizedMessage, text: str, subject_id: str) -> None:
        uid = message.user_id
        await self.redis.delete_state(f"awaiting_textbook_{uid}")
        grade = await self._get_grade(uid)
        if not grade:
            return
        name = " ".join(text.split())[:60]
        # Если написали фамилию из списка — берём полное название («Моро» → «Моро (Школа России)»)
        for author in programs.authors_for(grade, subject_id):
            if name and name.lower() in author.lower():
                name = author
                break
        await self._save_textbook(message, grade, subject_id, name)

    async def _save_textbook(self, message: NormalizedMessage, grade: int, subject_id: str, author: str) -> None:
        uid = message.user_id
        books = await self._textbooks(uid)
        books[subject_id] = author
        await self._save_profile(uid, textbooks=json.dumps(books, ensure_ascii=False))
        await self.answer(message, f"📗 Запомнил: {subject_title(grade, subject_id)} — учебник «{author}». "
                                   "Объяснять и составлять задания буду в его терминах.")
        await self.ask_topic(message, subject_id, grade)

    async def lesson_now(self, message: NormalizedMessage, subject_id: str) -> None:
        """«📘 Урок по программе»: урок по теме, которая сейчас идёт по программе."""
        uid = message.user_id
        grade = await self._get_grade(uid)
        topic = await self.program_topic_now(uid, grade, subject_id) if grade else None
        if not topic:
            await self._lesson_expired(message)
            return
        await self.start_lesson(message, grade, subject_id, topic)
