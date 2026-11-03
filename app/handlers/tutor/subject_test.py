"""«🎲 Тест по предмету»: каждый раз новый тест, который ИИ составляет и сам же перепроверяет.

Как устроено:
- 5 вопросов в 1–4 классах (3 варианта ответа), 7 вопросов в 5–11 классах (4 варианта);
- в тест попадают темы, которые пора повторить, и слабые темы из диагностики;
- сложность подстраивается под ученика по каждому предмету: базовый → повышенный → высокий;
- во время теста ответы просто записываются, в конце — итог «✅/❌» по каждому вопросу.
  Правильные ответы бот НЕ показывает: ошибки можно разобрать с подсказками.
"""
from typing import Tuple

from app.handlers.tutor.common import *  # noqa: F401,F403
from app.services import exam_forecast
from app.services import cards
from app.services import schedule as schedule_rules
from app.services.levels import DEFAULT_LEVEL, LEVEL_NAMES

TEST_TTL = 3 * 3600
_PREFETCH: Dict[int, "asyncio.Task"] = {}   # фоновая подготовка заданий урока: ученик → задача
LEVEL_UP_RUN, LEVEL_DOWN_RUN = 3, 2   # одиночные задания: 3 верных с первой попытки подряд — сложнее, 2 ошибки — проще


def test_size(grade: int) -> Tuple[int, int]:
    """(сколько вопросов, сколько вариантов ответа)."""
    return (5, 3) if grade <= OPTIONS_MAX_GRADE else (7, 4)


class SubjectTestMixin:
    """Сгенерированный тест по предмету и уровень сложности ученика."""

    # ---------- уровень сложности по предметам ----------

    async def _levels(self, uid: int) -> Dict[str, Any]:
        data = load_json(await self.redis.get_state(f"levels_{uid}"), {})
        return data if isinstance(data, dict) else {}

    async def _subject_level(self, uid: int, subject_id: str) -> int:
        entry = (await self._levels(uid)).get(subject_id) or {}
        level = entry.get("level", DEFAULT_LEVEL)
        return level if level in LEVEL_NAMES else DEFAULT_LEVEL

    async def _update_level(
        self, uid: int, subject_id: str, first_try: Optional[bool] = None, ratio: Optional[float] = None,
    ) -> Tuple[int, int]:
        """Меняет уровень по результату задания (first_try) или теста (ratio). Возвращает (было, стало)."""
        levels = await self._levels(uid)
        entry = levels.get(subject_id) or {"level": DEFAULT_LEVEL, "run": 0}
        before = entry.get("level", DEFAULT_LEVEL)
        level, run = before, entry.get("run", 0)
        if ratio is not None:
            if ratio >= 0.8:
                level = min(level + 1, max(LEVEL_NAMES))
            elif ratio < 0.5:
                level = max(level - 1, min(LEVEL_NAMES))
            run = 0
        elif first_try is not None:
            run = max(run, 0) + 1 if first_try else min(run, 0) - 1
            if run >= LEVEL_UP_RUN:
                level, run = min(level + 1, max(LEVEL_NAMES)), 0
            elif run <= -LEVEL_DOWN_RUN:
                level, run = max(level - 1, min(LEVEL_NAMES)), 0
        levels[subject_id] = {"level": level, "run": run}
        await self.redis.set_state(f"levels_{uid}", json.dumps(levels, ensure_ascii=False), ex=GRADE_TTL)
        return before, level

    # ---------- тест ----------

    async def _test_state(self, uid: int) -> Optional[Dict[str, Any]]:
        state = load_json(await self.redis.get_state(f"stest_{uid}"), None)
        return state if isinstance(state, dict) and state.get("questions") else None

    async def _save_test_state(self, uid: int, state: Dict[str, Any]) -> None:
        await self.redis.set_state(f"stest_{uid}", json.dumps(state, ensure_ascii=False), ex=TEST_TTL)

    async def _test_focus_topics(self, uid: int, grade: int, subject_id: str) -> List[str]:
        """Темы, которые пора повторить (сначала слабые из диагностики)."""
        progress = await self._load_progress(uid)
        topics = [e["topic"] for e in topic_progress.due_topics(progress, grade, subject_id)]
        if not progress:
            topics += weak_topics_for(await self._load_profile(uid), subject_id)
        unique: List[str] = []
        for t in topics:
            if t and t.lower() not in [u.lower() for u in unique]:
                unique.append(t)
        return unique[:3]

    async def start_subject_test(
        self, message: NormalizedMessage, grade: int, subject_id: str,
        focus: Optional[List[str]] = None, weekly: bool = False, mode: str = "test",
    ) -> None:
        """Тест по предмету.

        mode: "test" — обычный тест; "entry" — входной тест по пройденным темам (при первом входе в предмет);
        "lesson" — задания на оценку после урока (все вопросы по одной теме focus[0]).
        weekly — пятничный мини-тест недели по темам, которые проходили за неделю.
        """
        uid = message.user_id
        title = subject_title(grade, subject_id)
        back = f"subj_{subject_id}_{grade}"
        count, n_options = test_size(grade)
        level = await self._subject_level(uid, subject_id)
        if mode == "lesson":
            count = 4 if grade <= OPTIONS_MAX_GRADE else 5
        exam = None
        if mode.startswith("exam_"):             # пробный вариант ОГЭ/ЕГЭ: 10 заданий в формате экзамена, на время
            mode, exam = "exam", mode[5:]
            count, n_options, level, focus = exam_forecast.VARIANT_SIZE, 4, 3, []
        if focus is None and mode == "entry":
            focus = await self.program_passed(uid, grade, subject_id)   # входной тест — по пройденному по программе
        if focus is None:
            focus = await self._test_focus_topics(uid, grade, subject_id)
        scope = {"entry": "passed", "lesson": "topic", "exam": f"exam_{exam}"}.get(mode, "default")
        retry = {"entry": f"entry_{subject_id}_{grade}", "lesson": "lesson_graded",
                 "exam": f"exvar_{exam}_{grade}_{subject_id}"}.get(mode, f"subjtest_{subject_id}_{grade}")

        # 1) Свой банк проверенных вопросов: если хватает — тест готов сразу, без ИИ
        bank_topics = focus if focus and (scope != "default" or weekly) else None
        questions = [] if mode == "exam" else await self.bank.take(
            uid, grade, subject_id, "question", count, topics=bank_topics, level=level, n_options=n_options)
        prefetch = _PREFETCH.get(uid)
        if len(questions) < count and mode == "lesson" and prefetch and not prefetch.done():
            # Задания на оценку уже готовятся в фоне (с начала урока) — дожидаемся их
            await self.answer(message, "📝 Задания почти готовы… ⏳")
            await asyncio.wait({prefetch}, timeout=90)
            questions = await self.bank.take(uid, grade, subject_id, "question", count, topics=bank_topics,
                                             level=level, n_options=n_options)
        if len(questions) >= count:
            questions = [dict(q) for q in questions]
        else:
            # 2) Новый тест от ИИ (с перепроверкой) — и в банк для следующих учеников
            if not await self._ai_spend(uid):
                await self._ai_limit_reached(message)
                return
            await self.answer(message, f"✍️ Составляю для тебя {'задания' if mode == 'lesson' else 'новый тест'} "
                                       f"по предмету «{title}»… ⏳ Это займёт до минуты.")
            await self._typing(message)

            async def on_progress(stage: str, done: int, total: int) -> None:
                # Ребёнок видит, что бот не завис: вопросы составлены — идёт проверка
                if stage == "generated" and done:
                    await self.answer(message, f"🔍 Вопросы составлены ({done}). Проверяю каждый — решаю заново, "
                                               "чтобы в тесте не было ошибок…")
                    await self._typing(message)

            questions = await self._generate_questions(uid, grade, subject_id, count, n_options, level, focus, scope,
                                                       on_progress=on_progress)
        await self.bank.mark_seen(uid, [q.get("id") or self.bank.id_of(q["q"]) for q in questions])
        if len(questions) < min(3, count):
            await self._send_with_voice(
                message,
                "Не получилось составить тест — я проверяю каждый вопрос, и в этот раз слишком многие не прошли проверку. "
                "Давай попробуем ещё раз!",
                keyboard=_kb([[_btn("🔄 Попробовать ещё раз", retry)], [_back(back)]]),
            )
            return

        for q in questions:
            q["options"] = _shuffled(q["options"])
        state = {"grade": grade, "subject_id": subject_id, "level": level, "index": 0,
                 "questions": questions, "answers": [], "weekly": weekly, "mode": mode,
                 "topic": focus[0] if mode == "lesson" and focus else None,
                 "exam": exam, "started": topic_progress.now().timestamp()}
        await self._save_test_state(uid, state)
        await self.redis.set_state(f"stest_mode_{uid}", "true", ex=TEST_TTL)

        intro = (f"🎲 Тест по предмету «{title}»: {len(questions)} вопросов, уровень — {LEVEL_NAMES[level]}.\n"
                 "Отвечай кнопками. Ответы я запишу, а итоги покажу в конце.")
        if weekly:
            intro = intro.replace("🎲 Тест по предмету", "🎯 Мини-тест недели")
        if mode == "entry":
            intro = (f"🧭 Входной тест по предмету «{title}»: {len(questions)} вопросов по темам, которые вы уже прошли.\n"
                     "Отвечай кнопками. Так я пойму, что ты уже знаешь, а что стоит подтянуть.")
        elif mode == "exam":
            intro = (f"🧪 Пробный вариант {EXAM_NAMES[exam]} · {title}: {len(questions)} заданий в формате экзамена.\n"
                     "Время засекаю ⏱ Отвечай кнопками, в конце — прогноз результата и что подтянуть.")
        elif mode == "lesson":
            intro = (f"📝 Задания на оценку по теме «{focus[0]}»: {len(questions)} вопросов, без подсказок.\n"
                     "Отвечай кнопками. Оценку поставлю в конце.")
        elif weekly and focus:
            intro += "\n📅 Проверяем темы, которые мы прошли за неделю: " + ", ".join(f"«{t}»" for t in focus) + "."
        elif focus:
            intro += "\n🎯 Добавил темы, которые пора повторить: " + ", ".join(f"«{t}»" for t in focus) + "."
        await self.answer(message, intro)
        await self._test_ask(message, state, first=True)

    async def _generate_questions(self, uid: int, grade: int, subject_id: str, count: int, n_options: int, level: int,
                                  focus: List[str], scope: str, on_progress=None) -> List[Dict[str, Any]]:
        """Тест от ИИ + сохранение проверенных вопросов в банк. В уроке все вопросы записываются на тему урока."""
        title = subject_title(grade, subject_id)
        textbook = await self._textbook(uid, subject_id)
        questions = await ai_client.build_test(grade, title, count, n_options, level=level, focus_topics=focus, scope=scope,
                                               **({"on_progress": on_progress} if on_progress else {}),
                                               **({"textbook": textbook} if textbook else {}))
        to_bank = [{**q, "topic": focus[0]} if scope == "topic" and focus else q for q in questions]
        await self.bank.add(grade, subject_id, to_bank, "question", level)
        return questions

    def start_prefetch(self, uid: int, grade: int, subject_id: str, topic: str) -> None:
        """Пока ученик читает урок и решает пробный пример — готовим задания на оценку в фоне."""
        current = _PREFETCH.get(uid)
        if current and not current.done():
            return
        count, n_options = (4, 3) if grade <= OPTIONS_MAX_GRADE else (5, 4)

        async def job() -> None:
            try:
                level = await self._subject_level(uid, subject_id)
                have = await self.bank.take(uid, grade, subject_id, "question", count, topics=[topic],
                                            level=level, n_options=n_options)
                if len(have) < count:
                    await self._generate_questions(uid, grade, subject_id, count, n_options, level, [topic], "topic")
            except Exception:
                logger.exception("Не удалось заранее подготовить задания урока")

        _PREFETCH[uid] = asyncio.create_task(job())

    def _test_keyboard(self, state: Dict[str, Any]) -> Dict[str, Any]:
        i = state["index"]
        options = state["questions"][i]["options"]
        rows = _option_rows(options, prefix=f"sta_{i}_")
        back = f"subj_{state['subject_id']}_{state['grade']}"
        return _kb(rows + [[_btn("⏹ Закончить тест", "st_stop")], [_back(back)]])

    async def _test_ask(self, message: NormalizedMessage, state: Dict[str, Any], prefix: str = "", first: bool = False):
        i, total = state["index"], len(state["questions"])
        q = state["questions"][i]
        text = f"{prefix}Вопрос {i + 1} из {total}\n\n{q['q']}{_options_text(q['options'])}\n\n{_options_call(q['options'])}"
        voice = ("Первый вопрос. " if first else f"Вопрос {i + 1}. ") + q["q"] + " " + _options_voice(q["options"])
        await self._send_with_voice(message, text, keyboard=self._test_keyboard(state), voice_text=voice)

    async def test_answer_button(self, message: NormalizedMessage, payload: str) -> bool:
        """Кнопка «sta_<вопрос>_<вариант>». False — кнопка от старого теста или вопроса."""
        uid = message.user_id
        state = await self._test_state(uid)
        try:
            qi, k = (int(x) for x in payload[len("sta_"):].split("_"))
        except ValueError:
            return False
        if not state or state.get("done") or qi != state["index"]:
            return False
        options = state["questions"][qi]["options"]
        if not 0 <= k < len(options):
            return False
        await self.redis.set_state(f"stest_mode_{uid}", "true", ex=TEST_TTL)
        await self._test_record(message, state, options[k])
        return True

    async def test_answer_text(self, message: NormalizedMessage, text: str) -> None:
        """Ответ текстом: номер варианта или сам вариант."""
        uid = message.user_id
        state = await self._test_state(uid)
        if not state or state.get("done"):
            await self.redis.delete_state(f"stest_mode_{uid}")
            return
        options = state["questions"][state["index"]]["options"]
        value = text.strip().lower().rstrip(".")
        words = {"один": 1, "первый": 1, "два": 2, "второй": 2, "три": 3, "третий": 3, "четыре": 4, "четвёртый": 4,
                 "четвертый": 4}
        chosen = None
        by_norm = {normalize_answer(o): o for o in options}
        if normalize_answer(value) in by_norm:
            chosen = by_norm[normalize_answer(value)]
        elif value.isdigit() and 1 <= int(value) <= len(options):
            chosen = options[int(value) - 1]
        elif words.get(value) and words[value] <= len(options):
            chosen = options[words[value] - 1]
        if chosen is None:
            await self._send_with_voice(
                message,
                "В тесте отвечай кнопкой с вариантом 👇 или напиши номер варианта (1, 2, 3…).",
                keyboard=self._test_keyboard(state),
            )
            return
        await self._test_record(message, state, chosen)

    async def _test_record(self, message: NormalizedMessage, state: Dict[str, Any], chosen: str) -> None:
        q = state["questions"][state["index"]]
        state["answers"].append({"chosen": chosen, "correct": normalize_answer(chosen) == normalize_answer(q["answer"])})
        state["index"] += 1
        if state["index"] >= len(state["questions"]):
            await self._test_finish(message, state)
            return
        await self._save_test_state(message.user_id, state)
        await self._test_ask(message, state, prefix="✅ Записал!\n\n")

    async def stop_test(self, message: NormalizedMessage) -> None:
        uid = message.user_id
        state = await self._test_state(uid)
        if not state or state.get("done"):
            grade = await self._get_grade(uid)
            if grade:
                await self.show_subjects(message, grade)
            return
        if not state["answers"]:
            await self.redis.delete_state(f"stest_{uid}")
            await self.redis.delete_state(f"stest_mode_{uid}")
            await self.ask_topic(message, state["subject_id"], state["grade"])
            return
        state["questions"] = state["questions"][: len(state["answers"])]
        await self._test_finish(message, state, stopped=True)

    async def _test_finish(self, message: NormalizedMessage, state: Dict[str, Any], stopped: bool = False) -> None:
        uid = message.user_id
        grade, subject_id = state["grade"], state["subject_id"]
        title = subject_title(grade, subject_id)
        total = len(state["answers"])
        right = sum(1 for a in state["answers"] if a["correct"])
        state["done"] = True
        await self._save_test_state(uid, state)
        await self.redis.delete_state(f"stest_mode_{uid}")

        # Прогресс по темам: каждый вопрос — как задание с первой попытки (входной тест — как диагностика, ниже)
        progress = await self._load_progress(uid)
        for q, a in zip(state["questions"], state["answers"]):
            topic = state.get("topic") if state.get("mode") == "lesson" else q.get("topic")   # урок — всё по его теме
            if topic and state.get("mode") != "entry":
                topic_progress.record_answer(progress, grade, subject_id, title, topic,
                                             "correct" if a["correct"] else "wrong")
        await self._save_progress(uid, progress)

        ratio = right / total if total else 0
        # Пробный вариант экзамена нарочно сложный — уровень заданий по предмету по нему не меняем
        before, after = await self._update_level(uid, subject_id, ratio=ratio) \
            if total >= 3 and state.get("mode") != "exam" else (state["level"],) * 2
        reward = await self._count_test(uid, right) if right else ""
        mode = state.get("mode") or "test"
        extra = ""
        if mode == "entry" and not stopped:
            extra = await self._finish_entry_test(uid, grade, subject_id, state)
        elif mode == "exam" and total:
            extra = await self._finish_exam_variant(uid, state, right, total, stopped)
        elif mode == "lesson" and not stopped:
            extra = await self._save_lesson_mark(uid, grade, subject_id, state.get("topic") or "", right, total)
            if hasattr(self, "_assignment_done"):   # задание учителя — результат уходит учителю
                extra += await self._assignment_done(uid, state.get("topic") or "", right, total)
        if state.get("weekly") and not stopped:
            await self.redis.delete_state(f"weektest_{uid}")
            reward += await self._reward(uid, "plan_done")      # мини-тест недели засчитывается как план на день

        head = {"entry": "Входной тест готов", "lesson": "Задания на оценку выполнены",
                "exam": "Пробный вариант готов"}.get(mode, "Тест готов")
        lines = [f"🏁 {'Тест остановлен' if stopped else head}! {title}: {right} из {total}.", ""]
        for n, (q, a) in enumerate(zip(state["questions"], state["answers"]), 1):
            lines.append(f"{n}. {'✅' if a['correct'] else '❌'} {q.get('topic') or 'вопрос'}")
        if ratio == 1:
            comment = "Безупречно! Ни одной ошибки 🔥"
        elif ratio >= 0.8:
            comment = "Очень сильный результат 💪"
        elif ratio >= 0.5:
            comment = "Неплохо! Ошибки — это темы, которые стоит разобрать."
        else:
            comment = "Тест был непростой. Давай разберём ошибки — с подсказками всё получится."
        if after > before:
            comment += f"\nСледующие задания будут сложнее: уровень «{LEVEL_NAMES[after]}» 🚀"
        elif after < before:
            comment += f"\nНемного снизим сложность — уровень «{LEVEL_NAMES[after]}», закрепим основу."
        wrong = [n for n, a in enumerate(state["answers"]) if not a["correct"]]
        if wrong:
            comment += "\nПравильные ответы не показываю — разбери ошибки сам с моими подсказками 👇"
        lines += ["", comment]
        if extra:
            lines += ["", extra]

        rows = [[_btn(f"🔧 Разобрать вопрос {n + 1}", f"st_fix_{n}")] for n in wrong[:4]]
        if mode == "test":
            rows += [[_btn("🎲 Ещё тест", f"subjtest_{subject_id}_{grade}")]]
        elif mode == "exam":
            rows += [[_btn("🧪 Ещё вариант", f"exvar_{state['exam']}_{grade}_{subject_id}")],
                     [_btn(f"📝 К подготовке к {EXAM_NAMES[state['exam']]}", f"exam_{state['exam']}_{grade}")]]
        elif mode == "lesson" and wrong:
            rows += [[_btn("🔄 Ещё раз на оценку", "lesson_graded")]]
        rows += [[_back(f"subj_{subject_id}_{grade}")]]
        voice = f"Тест закончен. {right} из {total}. {extra} {comment}".replace("  ", " ")
        if total:
            card_title = f"Урок: {state.get('topic')}" if mode == "lesson" and state.get("topic") else \
                {"entry": "Входной тест", "test": "Тест",
                 "exam": f"Пробный {EXAM_NAMES.get(state.get('exam'), '')}"}.get(mode, "Тест")
            await self._send_card(
                message, cards.result_card, card_title, f"{title} · {grade} класс", right, total,
                [a["correct"] for a in state["answers"]],
                schedule_rules.mark_for(right, total) if mode == "lesson" and not stopped else None,
            )
        await self._send_with_voice(message, "\n".join(lines) + reward, keyboard=_kb(rows), voice_text=voice)

    async def _finish_exam_variant(self, uid: int, state: Dict[str, Any], right: int, total: int,
                                   stopped: bool) -> str:
        """Пробный вариант: время, прогноз оценки/балла, сравнение с прошлым разом, темы, которые тянут вниз."""
        exam, subject_id = state["exam"], state["subject_id"]
        spent = topic_progress.now().timestamp() - state.get("started", topic_progress.now().timestamp())
        fc = exam_forecast.forecast(exam, right, total)
        lines = [f"⏱ Время: {exam_forecast.duration_text(spent)}"]
        if stopped and total < len(state["questions"]):
            lines.append("Вариант решён не до конца — прогноз только по отвеченным заданиям.")
        lines.append(f"🔮 Прогноз {EXAM_NAMES[exam]}: {fc['text']} (ориентир по заданиям с выбором ответа, "
                     "не официальная шкала ФИПИ)")
        history = load_json(await self.redis.get_state(f"exvar_{uid}"), {})
        today = (await self._local_date(uid)).isoformat()
        prev = exam_forecast.add_history(history, subject_id, {"date": today, "share": round(fc["share"], 3),
                                                               "value": fc["value"]})
        await self.redis.set_state(f"exvar_{uid}", json.dumps(history, ensure_ascii=False))
        if prev:
            was, now = round(prev["share"] * 100), round(fc["share"] * 100)
            arrow = "📈" if now > was else ("📉" if now < was else "➡️")
            lines.append(f"{arrow} Прошлый вариант: {was}% → сейчас: {now}%")
        weak = list(dict.fromkeys(q.get("topic") for q, a in zip(state["questions"], state["answers"])
                                  if not a["correct"] and q.get("topic")))
        if weak:
            lines.append("🎯 Что подтянуть: " + ", ".join(f"«{t}»" for t in weak[:4]))
        if fc["low"]:
            lines.append("Пока маловато — но это пробник, для того и нужен. Разбери ошибки и попробуй ещё вариант 💪")
        return "\n".join(lines)

    async def _finish_entry_test(self, uid: int, grade: int, subject_id: str, state: Dict[str, Any]) -> str:
        """Входной тест: ошибки — темы «стоит подтянуть», верные — «знает». Предмет считается проверенным."""
        title = subject_title(grade, subject_id)
        results = [{"subject_id": subject_id, "subject": title, "topic": q.get("topic") or title,
                    "status": "верно" if a["correct"] else "неверно"}
                   for q, a in zip(state["questions"], state["answers"])]
        progress = await self._load_progress(uid)
        topic_progress.mark_diagnostic(progress, grade, results)
        profile = await self._load_profile(uid)
        same_grade = profile.get("diagnostic_grade") == grade
        scores = load_json(profile.get("diagnostic_scores"), {}) if same_grade else {}
        weak_all = load_json(profile.get("weak_topics"), []) if same_grade else []
        right = sum(1 for r in results if r["status"] == "верно")
        scores[title] = {"correct": right, "total": len(results)}
        weak = []
        for r in results:
            if r["status"] != "верно" and r["topic"] not in [w["topic"] for w in weak]:
                weak.append({"subject_id": subject_id, "subject": title, "topic": r["topic"]})
        weak_all = [w for w in weak_all if w.get("subject_id") != subject_id] + weak
        fields = {"topic_progress": json.dumps(progress, ensure_ascii=False),
                  "diagnostic_scores": json.dumps(scores, ensure_ascii=False),
                  "weak_topics": json.dumps(weak_all, ensure_ascii=False), "diagnostic_grade": grade}
        if profile.get("diagnostic_status") != "done" or not same_grade:
            fields.update(diagnostic_status="done",
                          diagnostic_summary=profile.get("diagnostic_summary") if same_grade and profile.get(
                              "diagnostic_summary") else "Входные тесты по предметам — итоги выше.")
        await self._save_profile(uid, **fields)
        if weak:
            return ("🎯 Запомнил темы, которые стоит подтянуть: " + ", ".join(f"«{w['topic']}»" for w in weak)
                    + ". Буду чаще давать задания на них, а перед первым — объясню тему.")
        return "Отлично, пробелов не видно! Дальше будем идти по новым темам 💪"

    async def _save_lesson_mark(self, uid: int, grade: int, subject_id: str, topic: str, right: int, total: int) -> str:
        mark = schedule_rules.mark_for(right, total)
        profile = await self._load_profile(uid)
        marks = load_json(profile.get("lesson_marks"), [])
        today = daily_rules.local_now(topic_progress.now(), profile.get("tz_offset")).date().isoformat()
        marks.append({"date": today, "grade": grade, "subject_id": subject_id, "topic": topic, "mark": mark})
        await self._save_profile(uid, lesson_marks=json.dumps(marks[-200:], ensure_ascii=False))
        words = {5: "Отлично! 🌟", 4: "Хорошо! 👍", 3: "Удовлетворительно. Тему стоит повторить.",
                 2: "Пока не получилось — давай разберём ошибки и попробуем ещё раз."}
        return f"📒 Оценка за урок «{topic}»: {mark}. {words[mark]}"

    async def _count_test(self, uid: int, right: int) -> str:
        """Верные ответы теста — в статистику дня, серию и награды (как задания с первой попытки)."""
        profile = await self._load_profile(uid)
        today_date = daily_rules.local_now(topic_progress.now(), profile.get("tz_offset")).date()
        activity = load_json(profile.get("activity"), {})
        for _ in range(right):
            daily_rules.record_activity(activity, today_date.isoformat(), True)
        streak = await self._bump_streak(uid, profile, today_date)
        await self._save_profile(uid, activity=json.dumps(activity, ensure_ascii=False), streak_days=streak,
                                 last_study_date=today_date.isoformat())
        reward = await self._reward(uid, "first_try", times=right)
        note = await self._lighthouse_note(uid) + await self._chest_note(uid)
        return (reward + note) if reward else (f"\n{note}" if note else "")

    async def fix_test_question(self, message: NormalizedMessage, n: int) -> None:
        """«Разобрать вопрос N»: тот же вопрос как задание с подсказками (ответ бот не называет)."""
        uid = message.user_id
        state = await self._test_state(uid)
        if not state or not state.get("done") or not 0 <= n < len(state["answers"]):
            raise ValueError("тест устарел")
        q = state["questions"][n]
        await self._deliver_task(
            message, "regular", state["grade"], state["subject_id"],
            back_payload=f"subj_{state['subject_id']}_{state['grade']}", topic=q.get("topic") or None,
            given={"text": q["q"], "answer": q["answer"], "options": q["options"], "topic": q.get("topic"),
                   "label": f"🔧 Разбираем вопрос {n + 1} из теста. Подумай ещё раз — я подскажу, если нужно."},
        )
