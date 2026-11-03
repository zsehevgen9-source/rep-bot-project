"""Диагностика: вопросы, ответы, итоги."""
from app.handlers.tutor.common import *  # noqa: F401,F403


class DiagnosticMixin:
    """Диагностика: вопросы, ответы, итоги."""

    def _diag_questions(self, grade: int, subjects: Optional[List[str]] = None):
        questions = task_bank.diagnostic_questions(grade, [sid for _, sid in SUBJECTS.get(grade, [])])
        if subjects is not None:
            questions = [q for q in questions if q.subject in subjects]
        return questions

    def _mandatory(self, grade: int) -> List[str]:
        """Обязательные предметы диагностики: математика (с 7 класса — алгебра и геометрия) и русский язык."""
        available = self._diag_subjects(grade)
        wanted = ["math", "geo_math", "rus"] if any(s == "geo_math" for _, s in SUBJECTS.get(grade, [])) else DIAG_MANDATORY
        return [s for s in wanted if s in available]

    def _math_combined(self, grade: int) -> bool:
        return all(s in self._diag_subjects(grade) for s in MATH_PARTS)

    def _full_diag_questions(self, grade: int, subjects: List[str]):
        """Вопросы полной диагностики: алгебра и геометрия — по 2 задания, остальные предметы — по 3."""
        questions = self._diag_questions(grade, subjects)
        if not self._math_combined(grade):
            return questions
        result = []
        for q in questions:
            if q.subject in MATH_PARTS:
                same = [x for x in questions if x.subject == q.subject]
                if len(same) > len(MATH_PART_LEVELS) and q.level not in MATH_PART_LEVELS:
                    continue
            result.append(q)
        return result

    def _diag_title(self, grade: int, subject_id: str) -> str:
        title = subject_title(grade, subject_id)
        return f"Математика · {title}" if subject_id in MATH_PARTS and self._math_combined(grade) else title

    def _diag_subjects(self, grade: int) -> List[str]:
        """Предметы класса, для которых есть вопросы диагностики (в порядке кнопок)."""
        have = {q.subject for q in self._diag_questions(grade)}
        return [sid for _, sid in SUBJECTS.get(grade, []) if sid in have]

    async def _diag_state(self, user_id: int) -> Optional[Dict[str, Any]]:
        state = load_json(await self.redis.get_state(f"diag_state_{user_id}"), None)
        if not isinstance(state, dict) or not state.get("ids"):
            return None
        return state

    async def _save_diag_state(self, user_id: int, state: Dict[str, Any]) -> None:
        await self.redis.set_state(f"diag_state_{user_id}", json.dumps(state, ensure_ascii=False), ex=DIAG_TTL)

    async def _should_offer_diagnostic(self, user_id: int, grade: int) -> bool:
        if not self._diag_questions(grade):
            return False  # для этого класса вопросов пока нет
        profile = await self._load_profile(user_id)
        finished = profile.get("diagnostic_status") in ("done", "skipped")
        return not (finished and profile.get("diagnostic_grade") == grade)

    async def offer_diagnostic(self, message: NormalizedMessage, grade: int):
        if grade >= DIAG_PICK_FROM_GRADE:
            await self.redis.delete_state(f"diag_pick_{message.user_id}")
            await self.show_diag_picker(message, grade, first=True)
            return
        name = await self.redis.get_state(f"name_{message.user_id}") or "друг"
        questions = self._diag_questions(grade)
        subjects = []
        for q in questions:
            title = subject_title(grade, q.subject)
            if title not in subjects:
                subjects.append(title)
        minutes = max(3, round(len(questions) * SECONDS_PER_QUESTION / 60))
        text = (
            f"Отлично, {name}! Чтобы я понял, как тебе помочь, давай проведём быструю диагностику: "
            f"{len(questions)} вопросов по предметам: {', '.join(subjects)}. "
            f"Это займёт около {minutes} минут. Отвечай кнопками или пиши ответ сообщением. "
            "Если не знаешь ответ — смело нажимай «Я не знаю». Поехали?\n\n"
            "Как со мной заниматься, расскажу в «⚙️ Настройки → 🎓 Обучение»."
        )
        await self._send_with_voice(message, text, keyboard=_kb([
            [_btn("🚀 Начать тест", f"diag_start_{grade}")],
            [_btn("⏭ Пропустить", f"diag_skip_{grade}")],
        ]))

    async def _picked(self, uid: int, grade: int) -> List[str]:
        available = self._diag_subjects(grade)
        picked = load_json(await self.redis.get_state(f"diag_pick_{uid}"), [])
        return [s for s in picked if s in available and s not in self._mandatory(grade)]

    async def show_diag_picker(self, message: NormalizedMessage, grade: int, first: bool = False, note: str = ""):
        """5–11 классы: русский и математика обязательно, ещё 2–3 предмета — на выбор ученика."""
        uid = message.user_id
        name = await self.redis.get_state(f"name_{uid}") or "друг"
        available = self._diag_subjects(grade)
        mandatory = self._mandatory(grade)
        optional = [s for s in available if s not in mandatory]
        picked = await self._picked(uid, grade)

        count = len(self._full_diag_questions(grade, mandatory + picked))
        minutes = max(3, round(count * SECONDS_PER_QUESTION / 60))
        lines = []
        if first:
            lines.append(f"Отлично, {name}! Чтобы я понял, как тебе помочь, давай проведём быструю диагностику — "
                         "по 3 вопроса на предмет.")
        if self._math_combined(grade):
            lines.append("Обязательно: Математика (алгебра и геометрия — по 2 задания), Русский язык.")
        else:
            lines.append("Обязательно: " + ", ".join(subject_title(grade, s) for s in mandatory) + ".")
        lines.append(f"Выбери ещё {DIAG_PICK_MIN}–{DIAG_PICK_MAX} предмета, по которым хочешь проверить себя 👇")
        if picked:
            lines.append(f"\nВыбрано: {', '.join(subject_title(grade, s) for s in picked)} · "
                         f"{count} вопросов, около {minutes} минут.")
        lines.append("Остальные предметы проверим позже — когда откроешь их впервые.")
        if first:
            lines.append("Как со мной заниматься, расскажу в «⚙️ Настройки → 🎓 Обучение».")
        if note:
            lines.insert(0, note)

        rows = _fit_rows([_btn(("✅ " if s in picked else "▫️ ") + button_title(subject_title(grade, s)), f"diag_pick_{s}")
                          for s in optional], max_per_row=2)
        rows.append([_btn(f"🚀 Начать (выбрано {len(picked)})", "diag_go")])
        rows.append([_btn("⏭ Пропустить", f"diag_skip_{grade}")])
        await self._send_with_voice(
            message, "\n".join(lines), keyboard=_kb(rows),
            voice_text=("Давай проведём быструю диагностику. " if first else "")
                       + "Русский язык и математика — обязательно. Выбери ещё два или три предмета кнопками.",
        )

    async def toggle_diag_pick(self, message: NormalizedMessage, subject_id: str) -> None:
        uid = message.user_id
        grade = await self._get_grade(uid)
        if not grade or subject_id not in self._diag_subjects(grade) or subject_id in self._mandatory(grade):
            raise ValueError(f"нельзя выбрать предмет {subject_id}")
        picked = await self._picked(uid, grade)
        note = ""
        if subject_id in picked:
            picked.remove(subject_id)
        elif len(picked) >= DIAG_PICK_MAX:
            note = f"Можно выбрать не больше {DIAG_PICK_MAX} предметов — сначала сними галочку с другого 🙂\n"
        else:
            picked.append(subject_id)
        await self.redis.set_state(f"diag_pick_{uid}", json.dumps(picked), ex=DAY)
        await self.show_diag_picker(message, grade, note=note)

    async def start_picked_diagnostic(self, message: NormalizedMessage) -> None:
        uid = message.user_id
        grade = await self._get_grade(uid)
        if not grade:
            raise ValueError("класс не выбран")
        picked = await self._picked(uid, grade)
        optional_total = len([s for s in self._diag_subjects(grade) if s not in self._mandatory(grade)])
        if len(picked) < min(DIAG_PICK_MIN, optional_total):
            await self.show_diag_picker(
                message, grade, note=f"Выбери ещё {DIAG_PICK_MIN - len(picked)} — и начнём! 👇\n")
            return
        await self.redis.delete_state(f"diag_pick_{uid}")
        subjects = self._mandatory(grade) + picked
        await self.start_diagnostic(message, grade, subjects=subjects)

    async def start_diagnostic(
        self, message: NormalizedMessage, grade: int, subjects: Optional[List[str]] = None, mode: str = "full",
    ):
        """mode: "full" — диагностика при знакомстве (или «пройти заново»), "subject" — проверка одного предмета."""
        uid = message.user_id
        if subjects is None and grade >= DIAG_PICK_FROM_GRADE and mode == "full":
            await self.offer_diagnostic(message, grade)  # старшим сначала — выбор предметов
            return
        if mode == "full":
            questions = self._full_diag_questions(grade, subjects or self._diag_subjects(grade))
        else:
            questions = self._diag_questions(grade, subjects)  # быстрая проверка предмета — все 3 вопроса
        if not questions:
            await self.show_subjects(message, grade, intro="Для этого класса диагностика скоро появится 🙂")
            return
        state = {"grade": grade, "ids": [q.id for q in questions], "index": 0, "answers": [], "mode": mode}
        await self._save_diag_state(uid, state)
        if mode == "full":
            await self._save_profile(uid, diagnostic_status="in_progress", diagnostic_grade=grade)
        await self._diag_ask(message, state)

    async def _diag_ask(self, message: NormalizedMessage, state: Dict[str, Any], prefix: str = ""):
        """Задаёт текущий вопрос (или подводит итоги, если вопросы кончились)."""
        uid = message.user_id
        while state["index"] < len(state["ids"]):
            question = task_bank.get(state["ids"][state["index"]])
            if question is not None:
                break
            # Вопрос удалили из файла — пропускаем его
            state["index"] += 1
        else:
            await self._diag_finish(message, state)
            return

        # Варианты перемешиваются одинаково для этого вопроса — при «Продолжить» кнопки те же
        options = _shuffled(question.options, seed=question.id) if question.options else []
        state["shown_options"] = options
        await self._save_diag_state(uid, state)
        await self.redis.set_state(f"diag_mode_{uid}", "true", ex=DIAG_TTL)
        number, total = state["index"] + 1, len(state["ids"])
        title = self._diag_title(state["grade"], question.subject)
        text = f"{prefix}Вопрос {number} из {total} · {title}\n\n{question.text}"
        if options:
            text += _options_text(options) + "\n\n" + _options_call(options)
        await self._send_with_voice(
            message,
            text,
            keyboard=_kb(_option_rows(options) + [[_btn("🤷 Я не знаю", "diag_idk")], [_btn("⏸ Продолжу потом", "diag_pause")]]),
            voice_text=f"Вопрос {number}. {question.text} " + (_options_voice(options) if options else ""),
        )

    async def _diag_record(self, message: NormalizedMessage, answer: Optional[str]):
        """Записывает ответ (None — «Я не знаю») и задаёт следующий вопрос. Проверка — в конце, чтобы не ждать."""
        uid = message.user_id
        state = await self._diag_state(uid)
        if not state or state["index"] >= len(state["ids"]):
            await self.redis.delete_state(f"diag_mode_{uid}")
            return
        state["answers"].append({"id": state["ids"][state["index"]], "answer": answer or None})
        state["index"] += 1
        prefix = "✅ Записал!\n\n" if answer else "👌 Хорошо, идём дальше.\n\n"
        await self._diag_ask(message, state, prefix=prefix)

    async def _diag_judge(self, question, answer: Optional[str]) -> str:
        if not answer:
            return "не знаю"
        reference = question.answer or ""
        if reference and _normalize_answer(answer) == _normalize_answer(_reference_core(reference)):
            return "верно"
        if question.options and _normalize_answer(answer) in [_normalize_answer(o) for o in question.options]:
            return "неверно"  # выбран другой вариант — ИИ не нужен
        try:
            verdict = await ai_client.judge_answer(question.text, answer, reference or None)
        except Exception as e:
            logger.error("Ошибка проверки ответа диагностики: %s", e)
            return "неверно"
        return {True: "верно", False: "неверно"}.get(verdict, "не знаю")

    async def _diag_finish(self, message: NormalizedMessage, state: Dict[str, Any]):
        uid = message.user_id
        grade = state["grade"]
        name = await self.redis.get_state(f"name_{uid}") or "друг"
        await self.redis.delete_state(f"diag_mode_{uid}")
        await self.answer(message, "Подвожу итоги... ⏳")

        # Проверяем все ответы параллельно (но не больше 4 запросов к ИИ одновременно)
        limit = asyncio.Semaphore(4)
        items = [(task_bank.get(a["id"]), a.get("answer")) for a in state["answers"]]
        items = [(q, a) for q, a in items if q is not None]

        async def judge(q, a):
            async with limit:
                return await self._diag_judge(q, a)

        statuses = await asyncio.gather(*(judge(q, a) for q, a in items))

        results, scores, weak = [], {}, []
        for (q, _), status in zip(items, statuses):
            title = subject_title(grade, q.subject)
            results.append({"subject": title, "subject_id": q.subject, "topic": q.topic or title,
                            "level": q.level or 1, "status": status})
            score = scores.setdefault(title, {"correct": 0, "total": 0})
            score["total"] += 1
            if status == "верно":
                score["correct"] += 1
            elif q.topic and not any(w["topic"] == q.topic and w["subject_id"] == q.subject for w in weak):
                weak.append({"subject_id": q.subject, "subject": title, "topic": q.topic})

        profile = await self._load_profile(uid)
        same_grade = profile.get("diagnostic_grade") == grade
        old_scores = load_json(profile.get("diagnostic_scores"), {}) if same_grade else {}
        old_weak = load_json(profile.get("weak_topics"), []) if same_grade else []
        checked_now = {r["subject"] for r in results}
        merged_scores = {**old_scores, **scores}
        merged_weak = [w for w in old_weak if w.get("subject") not in checked_now] + weak

        if state.get("mode") == "subject":
            await self._finish_subject_check(message, state, results, scores, weak, merged_scores, merged_weak)
            return

        summary = ""
        try:
            summary = await ai_client.diagnostic_summary(name, grade, results)
        except Exception as e:
            logger.error("Ошибка итогов диагностики: %s", e)
        if not summary or summary.startswith(("Ошибка при обращении к ИИ", "Ошибка сети")):
            strong = [s for s, v in scores.items() if v["correct"] == v["total"]]
            summary = f"{name}, диагностика пройдена! 🎉"
            if strong:
                summary += f" Отлично получается: {', '.join(strong)}."
            if weak:
                summary += " Похоже, стоит повторить: " + ", ".join(f"{w['topic']} ({w['subject']})" for w in weak) + "."
            summary += " Я запомнил это и буду чаще давать задания на эти темы."

        progress = await self._load_progress(uid)
        topic_progress.mark_diagnostic(progress, grade, results)
        await self._save_profile(
            uid,
            topic_progress=json.dumps(progress, ensure_ascii=False),
            diagnostic_status="done",
            diagnostic_grade=grade,
            diagnostic_summary=summary,
            diagnostic_scores=json.dumps(merged_scores, ensure_ascii=False),
            weak_topics=json.dumps(merged_weak, ensure_ascii=False),
        )
        await self.redis.delete_state(f"diag_state_{uid}")
        summary += await self._reward(uid, "diag_done")
        await self.show_subjects(message, grade, intro=summary)

    async def _finish_subject_check(self, message, state, results, scores, weak, merged_scores, merged_weak) -> None:
        """Итоги короткой проверки одного предмета: дописываем к диагностике и сразу ведём к заданиям."""
        uid = message.user_id
        grade = state["grade"]
        progress = await self._load_progress(uid)
        topic_progress.mark_diagnostic(progress, grade, results)
        await self._save_profile(
            uid,
            topic_progress=json.dumps(progress, ensure_ascii=False),
            diagnostic_scores=json.dumps(merged_scores, ensure_ascii=False),
            weak_topics=json.dumps(merged_weak, ensure_ascii=False),
        )
        await self.redis.delete_state(f"diag_state_{uid}")
        subject_id = results[0]["subject_id"] if results else None
        lines = [f"Готово! {s}: {v['correct']} из {v['total']}." for s, v in scores.items()]
        if weak:
            lines.append("Похоже, стоит повторить: " + ", ".join(w["topic"] for w in weak) + ". "
                         "Я запомнил — «🎲 Тест по предмету» обязательно включит эти темы.")
        else:
            lines.append("Отлично, пробелов не видно! 💪")
        await self.answer(message, " ".join(lines))
        if subject_id:
            await self.ask_topic(message, subject_id, grade)
        else:
            await self.show_subjects(message, grade)

    async def subject_needs_check(self, uid: int, grade: int, subject_id: str) -> bool:
        """Предмет этого класса ещё не проверяли (ни в диагностике, ни входным тестом)."""
        profile = await self._load_profile(uid)
        if profile.get("diagnostic_status") == "in_progress":
            return False
        scores = load_json(profile.get("diagnostic_scores"), {}) if profile.get("diagnostic_grade") == grade else {}
        return subject_title(grade, subject_id) not in scores
