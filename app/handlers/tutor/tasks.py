"""Задания: подбор, выдача, проверка ответа."""
from app.handlers.tutor.common import *  # noqa: F401,F403
from app.services.task_utils import PRAISE


class TasksMixin:
    """Задания: подбор, выдача, проверка ответа."""

    async def _fetch_task(
        self,
        kind: str,
        grade: int,
        subject_id: str,
        exam: Optional[str],
        topic: Optional[str],
        previous_id: Optional[str] = None,
        previous_text: Optional[str] = None,
        level: Optional[int] = None,
        uid: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Где берём задание: 1) банк, 2) sdamgia (только ЕГЭ), 3) ИИ (если разрешено в .env)."""
        bank_task = task_bank.pick(kind, grade, subject_id, exam=exam, topic=topic, exclude_id=previous_id)
        if bank_task:
            label = "📚 Задание из банка"
            if bank_task.source:
                label += f" ({bank_task.source})"
            if bank_task.url:
                label += f"\nИсточник: {bank_task.url}"
            return {"id": bank_task.id, "text": bank_task.text, "answer": bank_task.answer or None,
                    "label": label, "image": bank_task.image or None,
                    "topic": bank_task.topic or topic, "options": bank_task.options}

        if kind == "exam" and exam == "ege":
            data = await asyncio.to_thread(get_sdamgia_task, subject_id)
            if data:
                return {"text": data["text"], "answer": data["answer"], "label": f"📚 Задание: {data['source']}",
                        "topic": topic}

        if not settings.ALLOW_AI_TASKS:
            return None

        title = subject_title(grade, subject_id)
        full_subject = f"{EXAM_NAMES[exam]} {title}" if kind == "exam" and exam else title
        extra: Dict[str, Any] = {"level": level} if level else {}
        if kind != "exam" and uid:
            if not topic:
                # Тема не задана — берём из пройденного по программе (чаще — из последних тем)
                passed = await self.program_passed(uid, grade, subject_id)
                if passed:
                    topic = random.choice(passed[-6:])
            textbook = await self._textbook(uid, subject_id)
            if textbook:
                extra["textbook"] = textbook
            # Сначала — свой банк проверенных заданий: мгновенно и без запроса к ИИ
            cached = await self.bank.take(uid, grade, subject_id, "task", 1, topics=[topic] if topic else None, level=level)
            if cached:
                await self.bank.mark_seen(uid, [cached[0]["id"]])
                return {"text": cached[0]["text"], "answer": cached[0]["answer"], "options": cached[0].get("options") or [],
                        "topic": cached[0].get("topic") or topic, "label": "📝 Тренировочное задание (проверено)"}
        if uid and not await self._ai_spend(uid):
            return {"limit": True}
        text, answer, meta = await ai_client.generate_task(
            grade, full_subject, topic, avoid=previous_text, with_options=grade <= OPTIONS_MAX_GRADE, **extra,
        )
        if not text or text.startswith(("Ошибка при обращении к ИИ", "Ошибка сети")):
            return None
        if kind != "exam" and uid and answer:
            # Проверенное задание — в банк, следующим ученикам оно достанется сразу
            item = {"text": text, "answer": answer, "options": meta.get("options") or [], "topic": topic or meta.get("topic")}
            await self.bank.add(grade, subject_id, [item], "task", level or 2)
            await self.bank.mark_seen(uid, [self.bank.id_of(text)])
        if kind == "exam" and exam:
            label = f"📝 Тренировочное задание в формате {EXAM_NAMES[exam]} (составлено ИИ)"
        else:
            label = "📝 Тренировочное задание (составлено ИИ)"
        return {"text": text, "answer": answer, "label": label,
                "topic": meta.get("topic") or topic, "options": meta.get("options") or []}

    async def _deliver_task(
        self,
        message: NormalizedMessage,
        kind: str,
        grade: int,
        subject_id: str,
        back_payload: str,
        exam: Optional[str] = None,
        topic: Optional[str] = None,
        note: Optional[str] = None,
        plan: bool = False,
        given: Optional[Dict[str, Any]] = None,
        lesson: Optional[str] = None,
    ):
        """Выдаёт задание. given — готовое задание (например, вопрос из теста для разбора ошибки)."""
        uid = message.user_id
        if given:
            task = {"id": None, "image": None, **given}
        else:
            await self.answer(message, "Секунду, подбираю задание... ⏳")
            previous_id = await self.redis.get_state(f"last_task_id_{uid}")
            previous_text = await self.redis.get_state(f"current_task_{uid}")
            level = await self._subject_level(uid, subject_id)
            task = await self._fetch_task(kind, grade, subject_id, exam, topic, previous_id, previous_text, level, uid=uid)
            if task and task.get("limit"):
                await self._ai_limit_reached(message)
                return
        if task is None:
            rows = [[_btn("⏭ Следующее задание", "plan_skip")]] if plan else []
            await self._send_with_voice(
                message,
                "Не получилось подобрать задание: в моей базе по этому предмету и теме пока ничего нет. "
                "Попробуй другую тему или предмет, либо повтори чуть позже.",
                keyboard=_kb(rows + [[_back(back_payload)]]),
            )
            return

        attachments = None
        if task.get("image"):
            token = await self._upload_task_image(task["image"])
            if not token:
                await self._send_with_voice(
                    message,
                    "Не получилось отправить картинку к заданию 😕 Попробуй ещё раз чуть позже или выбери другую тему.",
                    keyboard=_kb([[_back(back_payload)]]),
                )
                return
            attachments = [{"type": "image", "payload": {"token": token}}]

        await self.redis.set_state(f"task_active_{uid}", "true", ex=3600)
        await self.redis.set_state(f"task_kind_{uid}", kind, ex=3600)
        await self.redis.delete_state(f"attempts_{uid}")
        await self.redis.set_state(f"current_task_{uid}", task["text"], ex=3600)
        await self.redis.set_state(f"back_{uid}", back_payload, ex=3600)
        await self.redis.set_state(f"current_subj_{uid}", subject_title(grade, subject_id), ex=3600)
        await self.redis.set_state(f"last_task_id_{uid}", task.get("id") or "", ex=3600)
        # Всё, что нужно, чтобы кнопка «Ещё задание» выдала задание того же типа
        task_ctx = {"kind": kind, "grade": grade, "subject_id": subject_id, "exam": exam, "topic": topic, "back": back_payload,
                    "progress_topic": task.get("topic") or "", "plan": plan, "lesson": lesson}
        await self.redis.set_state(f"task_ctx_{uid}", json.dumps(task_ctx, ensure_ascii=False), ex=3600)
        if task["answer"]:
            await self.redis.set_state(f"ref_answer_{uid}", task["answer"], ex=3600)
        else:
            await self.redis.delete_state(f"ref_answer_{uid}")

        options = _shuffled(task.get("options") or [])
        if options:
            await self.redis.set_state(f"options_{uid}", json.dumps(options, ensure_ascii=False), ex=3600)
        else:
            await self.redis.delete_state(f"options_{uid}")

        label = f"{note}\n{task['label']}" if note else task["label"]
        call = _options_call(options)[:-2] + " или напиши его 👇" if options else "Напиши свой ответ!"
        call = _options_text(options).lstrip("\n") + "\n\n" + call if _options_text(options) else call
        await self._send_with_voice(
            message,
            f"{label}\n\n{task['text']}\n\n{call}",
            keyboard=_kb(_option_rows(options) + self._explain_row(task.get("topic")) + self._task_extra_rows(plan)
                         + [[_back(back_payload)]]),
            voice_text=f"Держи задание. {task['text']} " + (_options_voice(options) if options else "Напиши свой ответ!"),
            attachments=attachments,
        )

    async def _upload_task_image(self, path: str) -> Optional[str]:
        """Загружает картинку задания в MAX и возвращает токен (или None)."""
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            logger.error("Не удалось прочитать картинку %s: %s", path, e)
            return None
        return await self.api.upload_image(data, filename=path.rsplit("/", 1)[-1])

    async def _check_answer(self, message: NormalizedMessage, text: str):
        uid = message.user_id
        task_text = await self.redis.get_state(f"current_task_{uid}") or ""
        reference = await self.redis.get_state(f"ref_answer_{uid}") or None
        grade = await self._get_grade(uid)
        back = await self.redis.get_state(f"back_{uid}") or (f"subjects_{grade}" if grade else "back_to_menu")

        attempt = int(await self.redis.get_state(f"attempts_{uid}") or 0) + 1

        options = load_json(await self.redis.get_state(f"options_{uid}"), [])
        normalized = [_normalize_answer(o) for o in options]
        # Ответ — один из вариантов, и верный вариант среди них есть: вердикт ясен без ИИ
        known = (False if reference and _normalize_answer(reference) in normalized
                 and _normalize_answer(text) in normalized else None)
        if reference and _normalize_answer(text) == _normalize_answer(reference):
            correct, feedback = True, random.choice(PRAISE)
        else:
            try:
                correct, feedback = await ai_client.check_answer(
                    task_text, text, reference=reference, context=await self._context(uid), attempt=attempt,
                    known=known,
                )
            except Exception as e:
                logger.error("Ошибка проверки ответа: %s", e)
                correct, feedback = None, "Извини, не получилось проверить ответ. Попробуй ещё раз чуть позже."
                attempt -= 1  # сбой проверки не считаем попыткой

        await self.redis.set_state(f"attempts_{uid}", str(attempt), ex=3600)

        ctx = load_json(await self.redis.get_state(f"task_ctx_{uid}"), {})
        trial = ctx.get("lesson") == "trial"      # пробный пример урока: ошибки не считаются
        if not trial:
            await self._record_task_progress(uid, attempt, correct)
        if attempt == 1 and correct is not None and not trial:
            # Сложность подстраивается: 3 верных с первой попытки подряд — сложнее, 2 ошибки подряд — проще
            ctx = load_json(await self.redis.get_state(f"task_ctx_{uid}"), {})
            if ctx.get("subject_id"):
                await self._update_level(uid, ctx["subject_id"], first_try=bool(correct))

        in_plan = bool(load_json(await self.redis.get_state(f"task_ctx_{uid}"), {}).get("plan"))
        if correct:
            # Задание решено: следующие сообщения идут в обычный диалог
            await self.redis.delete_state(f"task_active_{uid}")
            await self.redis.delete_state(f"options_{uid}")
            feedback += await self._count_solved(uid, attempt)
            plan = await self._plan_task_solved(uid, first_try=attempt == 1)
            if trial:
                feedback += "\n\nПробный пример решён! Теперь — задания на оценку, уже без подсказок 📝"
                keyboard = _kb([[_btn("📝 Задания на оценку", "lesson_graded")],
                                [_btn("✍️ Ещё пробный пример", "lesson_trial")], [_back(back)]])
            elif plan is not None:
                left = len(plan["items"]) - plan["index"]
                label = f"➡️ Дальше (ещё {left})" if left else "🏁 Завершить на сегодня"
                keyboard = _kb([[_btn(label, "plan_next")], [_back(back)]])
            else:
                keyboard = _kb([[_btn(MORE_TEXT, "more")], [_back(back)]])
        else:
            # Варианты остаются под подсказкой — можно сразу попробовать ещё раз
            options = load_json(await self.redis.get_state(f"options_{uid}"), [])
            feedback += _options_text(options)   # на кнопках только номера — напоминаем варианты
            topic = load_json(await self.redis.get_state(f"task_ctx_{uid}"), {}).get("progress_topic")
            keyboard = _kb(_option_rows(options) + self._explain_row(topic) + self._task_extra_rows(in_plan)
                           + [[_back(back)]])

        await self._send_with_voice(message, feedback, keyboard=keyboard)

    async def _record_task_progress(self, uid: int, attempt: int, correct: Optional[bool]) -> None:
        """Прогресс по теме: первая попытка решает уровень темы; верно после подсказок — тема ещё изучается."""
        if correct is None:
            return  # ученик не ответил, а спросил — не считаем
        if attempt > 1 and not correct:
            return  # уже учли ошибку на первой попытке
        ctx = load_json(await self.redis.get_state(f"task_ctx_{uid}"), {})
        topic = (ctx.get("progress_topic") or "").strip()
        if not topic or not ctx.get("subject_id") or not ctx.get("grade"):
            return
        outcome = "correct" if correct and attempt == 1 else ("hinted" if correct else "wrong")
        progress = await self._load_progress(uid)
        topic_progress.record_answer(
            progress, int(ctx["grade"]), ctx["subject_id"], subject_title(int(ctx["grade"]), ctx["subject_id"]),
            topic, outcome,
        )
        await self._save_progress(uid, progress)

    async def answer_with_option(self, message: NormalizedMessage, index: int) -> bool:
        """Ученик нажал кнопку с вариантом ответа. Возвращает False, если кнопка устарела."""
        uid = message.user_id
        if await self.redis.get_state(f"diag_mode_{uid}") == "true":
            state = await self._diag_state(uid)
            shown = (state or {}).get("shown_options") or []
            if 0 <= index < len(shown):
                await self._diag_record(message, shown[index])
                return True
            return False
        if await self.redis.get_state(f"task_active_{uid}") == "true":
            options = load_json(await self.redis.get_state(f"options_{uid}"), [])
            if 0 <= index < len(options):
                await self._check_answer(message, options[index])
                return True
        return False

    async def _pick_review_topic(self, uid: int, grade: int, subject_id: str):
        """Тема для «Теста по предмету»: пора повторить → слабая из диагностики → (None) тема на выбор ИИ."""
        progress = await self._load_progress(uid)
        due = topic_progress.due_topics(progress, grade, subject_id)
        if due:
            entry = due[0]
            if entry["status"] == "weak":
                return entry["topic"], f"🎯 Тренируем тему «{entry['topic']}» — по итогам диагностики"
            return entry["topic"], f"🔁 Повторяем тему «{entry['topic']}» — пора закрепить"
        # Старые профили: слабые темы есть, а прогресса по темам ещё нет
        if not progress:
            weak = weak_topics_for(await self._load_profile(uid), subject_id)
            if weak:
                topic = random.choice(weak)
                return topic, f"🎯 Тренируем тему «{topic}» — по итогам диагностики"
        return None, None


    @staticmethod
    def _explain_row(topic: Optional[str]) -> List[List[Dict[str, str]]]:
        return [[_btn("📖 Объясни тему", "explain")]] if topic else []

    async def explain_current_topic(self, message: NormalizedMessage) -> bool:
        """Кнопка «📖 Объясни тему» во время задания: объяснение на другом примере, задание остаётся активным."""
        uid = message.user_id
        if await self.redis.get_state(f"task_active_{uid}") != "true":
            return False
        ctx = load_json(await self.redis.get_state(f"task_ctx_{uid}"), {})
        topic = ctx.get("progress_topic")
        if not topic or not ctx.get("grade"):
            return False
        grade = int(ctx["grade"])
        if not await self._ai_spend(uid):
            await self._ai_limit_reached(message)
            return True
        await self.answer(message, "Секунду, готовлю объяснение... 📖")
        text = await ai_client.explain_topic(
            grade, subject_title(grade, ctx["subject_id"]), topic, context=await self._context(uid),
            task=await self.redis.get_state(f"current_task_{uid}"),
            reference=await self.redis.get_state(f"ref_answer_{uid}"),
        )
        await self._mark_explained(uid, grade, ctx["subject_id"], topic)
        await self._set_via(uid, grade, ctx["subject_id"], topic, "task")
        options = load_json(await self.redis.get_state(f"options_{uid}"), [])
        back = await self.redis.get_state(f"back_{uid}") or f"subjects_{grade}"
        await self._send_with_voice(
            message, f"📖 Тема «{topic}»\n\n{text}\n\nТеперь попробуй решить задание 💪" + _options_text(options),
            keyboard=_kb(_option_rows(options) + self._task_extra_rows(bool(ctx.get("plan"))) + self._via_row()
                         + [[_back(back)]]),
        )
        return True

    async def _mark_explained(self, uid: int, grade: int, subject_id: str, topic: str) -> None:
        progress = await self._load_progress(uid)
        entry = progress.get(topic_progress.topic_key(grade, subject_id, topic))
        if entry is not None and not entry.get("explained"):
            entry["explained"] = True
            await self._save_progress(uid, progress)

    @staticmethod
    def _task_extra_rows(plan: bool) -> List[List[Dict[str, str]]]:
        """В задании из плана на день можно перейти к следующему, если совсем не получается (ответ не показываем)."""
        return [[_btn("⏭ Пропустить", "plan_skip")]] if plan else []
