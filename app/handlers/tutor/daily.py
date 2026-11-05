"""Ежедневные занятия: задание на день, вечерний отчёт, «Всё в порядке?», настройки уведомлений, статистика."""
from datetime import datetime, timedelta

from app.handlers.tutor.common import *  # noqa: F401,F403
from app.services import timezones

PLAN_TTL = 2 * DAY


class DailyMixin:
    """Ежедневные занятия: план на день, задания по плану, отчёт, уведомления."""

    # ---------- время и активность ----------

    async def _local_today(self, uid: int) -> str:
        profile = await self._load_profile(uid)
        return daily_rules.local_now(topic_progress.now(), profile.get("tz_offset")).date().isoformat()

    async def _touch_activity(self, message: NormalizedMessage) -> None:
        """Ученик что-то написал или нажал: запоминаем время (для правила «3 дня тишины») и чат для голоса."""
        uid = message.user_id
        if await self.redis.get_state(f"seen_{uid}"):
            return  # не чаще раза в 10 минут — чтобы не писать в базу на каждое сообщение
        await self.redis.set_state(f"seen_{uid}", "1", ex=600)
        fields: Dict[str, Any] = {"last_active_at": topic_progress.now(), "notify_paused": False}
        if message.chat_id:
            fields["chat_id"] = message.chat_id
        await self._save_profile(uid, **fields)

    async def _count_solved(self, uid: int, attempt: int) -> str:
        """Решённое задание: в статистику дня, в серию учебных дней и в награды. Возвращает текст награды."""
        first_try = attempt == 1
        profile = await self._load_profile(uid)
        today_date = daily_rules.local_now(topic_progress.now(), profile.get("tz_offset")).date()
        today = today_date.isoformat()
        activity = load_json(profile.get("activity"), {})
        daily_rules.record_activity(activity, today, first_try)
        streak = await self._bump_streak(uid, profile, today_date)
        await self._save_profile(
            uid, activity=json.dumps(activity, ensure_ascii=False), streak_days=streak, last_study_date=today,
        )
        reward = await self._reward(uid, "first_try" if first_try else "hinted", attempt=attempt)
        note = await self._lighthouse_note(uid) + await self._chest_note(uid) + await self._goal_check(uid)
        return (reward + note) if reward else (f"\n{note}" if note else "")

    # ---------- план на день ----------

    async def _daily_plan(self, uid: int) -> Optional[Dict[str, Any]]:
        plan = load_json(await self.redis.get_state(f"daily_plan_{uid}"), None)
        if not isinstance(plan, dict) or not plan.get("items"):
            return None
        if plan.get("date") != await self._local_today(uid):
            return None  # вчерашний план не продолжаем
        return plan

    async def _save_plan(self, uid: int, plan: Dict[str, Any]) -> None:
        await self.redis.set_state(f"daily_plan_{uid}", json.dumps(plan, ensure_ascii=False), ex=PLAN_TTL)

    async def _week_test_choice(self, uid: int, grade: int) -> Optional[Dict[str, Any]]:
        """Предмет для мини-теста недели: тот, по которому за неделю было больше всего тем (минимум 2)."""
        progress = await self._load_progress(uid)
        week_ago = topic_progress.now() - timedelta(days=7)
        subject_ids = [sid for _, sid in SUBJECTS[grade]]
        topics: Dict[str, List[str]] = {}
        for entry in progress.values():
            seen = entry.get("last_seen")
            try:
                recent = seen and datetime.fromisoformat(seen) >= week_ago
            except ValueError:
                recent = False
            if entry.get("grade") == grade and entry.get("subject_id") in subject_ids and recent:
                topics.setdefault(entry["subject_id"], []).append(entry["topic"])
        if not topics:
            return None
        subject_id, items = max(topics.items(), key=lambda kv: len(kv[1]))
        return {"subject_id": subject_id, "topics": items[:4]} if len(items) >= 2 else None

    async def send_daily(self, message: NormalizedMessage, regular: bool = False, force_week: bool = False) -> None:
        """Задание на день: личный план по слабым темам и повторениям. По пятницам — мини-тест недели.

        force_week — показать мини-тест недели в любой день (команда /demo_friday для презентации).
        """
        uid = message.user_id
        grade = await self._get_grade(uid)
        if not grade:
            return
        name = await self.redis.get_state(f"name_{uid}") or (await self._load_profile(uid)).get("name") or "друг"
        profile = await self._load_profile(uid)
        weekday = daily_rules.local_now(topic_progress.now(), profile.get("tz_offset")).weekday()
        choice = await self._week_test_choice(uid, grade) if (weekday == 4 or force_week) and not regular else None
        if force_week and not choice:
            # За неделю тем мало (например, новый ученик) — берём предмет с темами из прогресса или математику
            progress = await self._load_progress(uid)
            subject_ids = [sid for _, sid in SUBJECTS[grade]]
            by_subject: Dict[str, List[str]] = {}
            for entry in progress.values():
                if entry.get("grade") == grade and entry.get("subject_id") in subject_ids:
                    by_subject.setdefault(entry["subject_id"], []).append(entry["topic"])
            if by_subject:
                sid, topics = max(by_subject.items(), key=lambda kv: len(kv[1]))
            else:
                sid = next((s for s in ("math", "algebra") if s in subject_ids), subject_ids[0])
                topics = []
            choice = {"subject_id": sid, "topics": topics[:4]}
        lessons = await self.today_lessons(uid) if not regular and not choice and not force_week else []
        if lessons:
            # По расписанию: закрепляем то, что было на сегодняшних уроках
            titles = [subject_title(grade, sid) for sid in lessons]
            buttons = [_btn(button_title(t), f"lesson_ask_{sid}") for t, sid in zip(titles, lessons)]
            rows = _fit_rows(buttons, max_per_row=2)
            rows += [[_btn("🔁 Повторение", "plan_regular")], [_btn("⏰ Позже", f"subjects_{grade}")]]
            await self._send_with_voice(
                message,
                f"Привет, {name}! 📚 Сегодня по расписанию: {', '.join(titles)}.\n\n"
                "Давай закрепим то, что было на уроках: выбери предмет — я спрошу тему, объясню её, "
                "дам пробный пример и задания на оценку. Или нажми «🔁 Повторение» — задания по слабым темам.",
                keyboard=_kb(rows),
            )
            return
        if choice:
            await self.redis.set_state(f"weektest_{uid}", json.dumps(choice, ensure_ascii=False), ex=PLAN_TTL)
            title = subject_title(grade, choice["subject_id"])
            await self._send_with_voice(
                message,
                f"Привет, {name}! 🎯 Сегодня пятница — время мини-теста недели.\n\n"
                f"Проверим, что запомнилось по предмету «{title}»"
                + (": " + ", ".join(f"«{t}»" for t in choice["topics"]) if choice["topics"] else "")
                + ".\nЭто займёт около 10 минут. Начнём?",
                keyboard=_kb([[_btn("🎯 Начать мини-тест", "weektest")], [_btn("📅 Обычные задания", "plan_regular")],
                              [_btn("⏰ Позже", f"subjects_{grade}")]]),
            )
            return
        progress = await self._load_progress(uid)
        subject_ids = [sid for _, sid in SUBJECTS[grade]]
        items = daily_rules.build_daily_plan(progress, grade, subject_ids, daily_rules.tasks_per_day(grade))
        plan = {"date": await self._local_today(uid), "grade": grade, "items": items, "index": 0,
                "solved": 0, "first_try": 0, "skipped": 0}
        await self._save_plan(uid, plan)

        lines = [f"Привет, {name}! 📚 Задание на сегодня — {len(items)} {_tasks_word(len(items))}:"]
        for item in items:
            title = subject_title(grade, item["subject_id"])
            if item["topic"]:
                extra = " (подтянем)" if item["reason"] == "weak" else " (повторим)"
                lines.append(f"• {title} — «{item['topic']}»{extra}")
            else:
                lines.append(f"• {title} — новое задание")
        minutes = max(5, len(items) * 3)
        lines.append(f"\nЭто займёт около {minutes} минут. Начнём?")
        await self._send_with_voice(
            message, "\n".join(lines),
            keyboard=_kb([[_btn("▶️ Начать", "plan_next")], [_btn("⏰ Позже", f"subjects_{grade}")]]),
        )

    async def continue_plan(self, message: NormalizedMessage, skipped: bool = False) -> None:
        uid = message.user_id
        plan = await self._daily_plan(uid)
        if not plan:
            grade = await self._get_grade(uid)
            if grade:
                await self.show_subjects(message, grade, intro="Задания на сегодня уже закончились 🙂")
            else:   # старая кнопка, а класс уже не выбран (например, данные удалены) — начнём с класса
                await self.send_main_menu(message, await self.redis.get_state(f"name_{uid}") or "")
            return
        if skipped:
            plan["skipped"] += 1
            plan["index"] += 1
        if plan["index"] >= len(plan["items"]):
            await self._finish_plan(message, plan)
            return
        item = plan["items"][plan["index"]]
        grade = plan["grade"]
        if item["reason"] == "weak" and item["topic"] and not item.get("explained"):
            # Слабая тема: перед первым заданием — короткое объяснение (один раз для каждой темы)
            item["explained"] = True
            await self._save_plan(uid, plan)
            entry = (await self._load_progress(uid)).get(topic_progress.topic_key(grade, item["subject_id"], item["topic"]))
            if not (entry or {}).get("explained"):
                await self.explain_before_task(message, grade, item["subject_id"], item["topic"])
                return
        await self._save_plan(uid, plan)
        note = f"📅 Задание {plan['index'] + 1} из {len(plan['items'])} на сегодня\n" + \
            daily_rules.reason_note(item["reason"], item["topic"])
        await self._deliver_task(
            message, "regular", grade, item["subject_id"], back_payload=f"subjects_{grade}",
            topic=item["topic"], note=note, plan=True,
        )

    async def explain_before_task(self, message: NormalizedMessage, grade: int, subject_id: str, topic: str) -> None:
        uid = message.user_id
        title = subject_title(grade, subject_id)
        # Короткое объяснение темы одинаково для всех учеников класса — кешируем, как объяснения уроков
        cache_key = f"expl_short:{grade}:{subject_id}:{topic.lower()}"
        text = await self.redis.get_state(cache_key)
        if not text:
            if not await self._ai_spend(uid):
                await self._ai_limit_reached(message)
                return
        await self.answer(message, f"Тема «{topic}» в прошлый раз далась непросто — сначала коротко разберём её 📖")
        if not text:
            # Без имени ученика: объяснение из кеша увидят и другие ребята
            text = await ai_client.explain_topic(grade, title, topic, context={"grade": grade, "subject": title})
            if text and not text.startswith(("Ошибка при обращении к ИИ", "Ошибка сети")):
                await self.redis.set_state(cache_key, text, ex=30 * DAY)
        await self._mark_explained(uid, grade, subject_id, topic)
        await self._set_via(uid, grade, subject_id, topic, "plan")
        await self._send_with_voice(
            message, f"📖 {title} · {topic}\n\n{text}",
            keyboard=_kb([[_btn("✅ Понятно, к заданию", "plan_next")], *self._via_row(),
                          [_btn("⏰ Позже", f"subjects_{grade}")]]),
        )

    async def _plan_task_solved(self, uid: int, first_try: bool) -> Optional[Dict[str, Any]]:
        """Задание из плана решено: двигаем план. Возвращает план (или None, если задание было не из плана)."""
        ctx = load_json(await self.redis.get_state(f"task_ctx_{uid}"), {})
        if not ctx.get("plan"):
            return None
        plan = await self._daily_plan(uid)
        if not plan:
            return None
        plan["solved"] += 1
        plan["first_try"] += int(first_try)
        plan["index"] += 1
        await self._save_plan(uid, plan)
        return plan

    async def _finish_plan(self, message: NormalizedMessage, plan: Dict[str, Any]) -> None:
        uid = message.user_id
        await self.redis.delete_state(f"daily_plan_{uid}")
        profile = await self._load_profile(uid)
        streak = profile.get("streak_days") or 0
        if plan["solved"]:
            text = (f"🎉 Все задания на сегодня выполнены! Решено: {plan['solved']} из {len(plan['items'])}, "
                    f"с первой попытки: {plan['first_try']}.")
        else:
            text = "Задания на сегодня закончились. Ничего страшного, завтра получится лучше! 💪"
        if streak > 1:
            text += f" Ты занимаешься {streak} {_days_word(streak)} подряд 🔥"
        if plan["solved"]:
            text += await self._reward(uid, "plan_done")
        await self.show_subjects(message, plan["grade"], intro=text)

    # ---------- вечерний отчёт и «Всё в порядке?» ----------

    async def send_report(self, message: NormalizedMessage) -> None:
        uid = message.user_id
        grade = await self._get_grade(uid)
        if not grade:
            return
        profile = await self._load_profile(uid)
        name = profile.get("name") or await self.redis.get_state(f"name_{uid}") or "друг"
        today = await self._local_today(uid)
        stats = load_json(profile.get("activity"), {}).get(today, {})
        progress = load_json(profile.get("topic_progress"), {})
        info = topic_progress.summary(progress, grade)
        streak = profile.get("streak_days") or 0

        if stats.get("solved"):
            lines = [f"🌙 {name}, итоги дня:",
                     f"• решено заданий: {stats['solved']}, с первой попытки: {stats.get('first_try', 0)}"]
            if info["was_weak"]:
                lines.append(f"• закреплено слабых тем: {info['weak_now_mastered']} из {info['was_weak']}")
            mode = rewards_rules.mode_for_grade(grade)
            if stats.get("points") and rewards_rules.gained_label(stats["points"], mode):
                lines.append(f"• заработано сегодня: {rewards_rules.gained_label(stats['points'], mode)}")
            if mode != rewards_rules.PLAY:
                goal = rewards_rules.WEEKLY_GOAL[mode]
                week = rewards_rules.week_stats(load_json(profile.get("activity"), {}),
                                                daily_rules.local_now(topic_progress.now(), profile.get("tz_offset")).date())
                lines.append(f"• цель недели: {min(week['solved'], goal)}/{goal} заданий")
            if streak > 1:
                lines.append(f"• занимаешься {streak} {_days_word(streak)} подряд 🔥")
            if info["due"]:
                lines.append("Завтра повторим: " + ", ".join(e["topic"] for e in info["due"][:3]) + ".")
            lines.append("Молодец! Отдыхай 😊")
        else:
            lines = [f"🌙 {name}, сегодня позаниматься не получилось — ничего страшного!",
                     "Завтра будет новое задание. Можно начать и прямо сейчас 🙂"]
        await self._send_with_voice(message, "\n".join(lines), keyboard=_kb([[_btn("📚 К предметам", f"subjects_{grade}")]]))

    async def send_ping(self, message: NormalizedMessage) -> None:
        uid = message.user_id
        grade = await self._get_grade(uid)
        name = (await self._load_profile(uid)).get("name") or "друг"
        await self._save_profile(uid, notify_paused=True)
        await self._send_with_voice(
            message,
            f"Привет, {name}! Давно тебя не видно 🙂 Всё в порядке? Я не буду писать каждый день — "
            "когда захочешь продолжить, просто напиши мне или нажми кнопку.",
            keyboard=_kb([[_btn("▶️ Продолжить учёбу", "plan_resume")]]),
        )

    async def run_scheduled(self, message: NormalizedMessage, action: str) -> None:
        """Вызывается планировщиком: отправить ученику задание на день, отчёт или «Всё в порядке?»."""
        today = await self._local_today(message.user_id)
        if action == "daily":
            await self.send_daily(message)
            await self._save_profile(message.user_id, last_daily_date=today)
        elif action == "ping":
            await self.send_ping(message)
            await self._save_profile(message.user_id, last_daily_date=today)
        elif action == "report":
            await self.send_report(message)
            await self._save_profile(message.user_id, last_report_date=today)
        elif action == "parent_report":
            await self.send_parent_report(message)
        elif action == "family_check":
            await self.family_check(message)
        elif action == "class_check":
            await self.class_check(message)

    # ---------- настройки уведомлений ----------

    async def show_notify_settings(self, message: NormalizedMessage) -> None:
        profile = await self._load_profile(message.user_id)
        enabled = profile.get("notify_enabled") is not False
        daily_time = profile.get("daily_time") or daily_rules.DEFAULT_DAILY_TIME
        report_time = profile.get("report_time") or daily_rules.DEFAULT_REPORT_TIME
        grade = await self._get_grade(message.user_id)
        rows = [[_btn("🔕 Выключить" if enabled else "🔔 Включить", "notify_toggle")]]
        rows += _fit_rows([_btn(("✅ " if t == daily_time else "") + "📚 " + t, "dtime_" + t.replace(":", ""))
                           for t in daily_rules.DAILY_TIME_CHOICES], max_per_row=2)
        rows += _fit_rows([_btn(("✅ " if t == report_time else "") + "🌙 " + t, "rtime_" + t.replace(":", ""))
                           for t in daily_rules.REPORT_TIME_CHOICES], max_per_row=2)
        rows.append([_btn("🕐 Часовой пояс", "tzset")])
        rows.append([_back(f"settings_{grade}" if grade else "back_to_menu")])
        status = "включены" if enabled else "выключены"
        zone = timezones.zone_name(profile.get("tz_offset"))
        await self._send_with_voice(
            message,
            f"🔔 Уведомления {status}.\n\nПо будням я присылаю задание на день и вечером — отчёт.\n"
            f"📚 — время задания (сейчас {daily_time}), 🌙 — время отчёта (сейчас {report_time}).\n"
            f"🕐 Часовой пояс: {zone}.",
            keyboard=_kb(rows),
            voice_text=f"Уведомления {status}. Задание приходит в {daily_time}, отчёт — в {report_time}.",
        )

    async def set_notify(self, message: NormalizedMessage, payload: str) -> None:
        uid = message.user_id
        if payload == "notify_toggle":
            enabled = (await self._load_profile(uid)).get("notify_enabled") is not False
            await self._save_profile(uid, notify_enabled=not enabled, notify_paused=False)
        elif payload.startswith("dtime_"):
            value = f"{payload[6:8]}:{payload[8:10]}"
            if value not in daily_rules.DAILY_TIME_CHOICES:
                raise ValueError(f"неверное время {value}")
            await self._save_profile(uid, daily_time=value)
        elif payload.startswith("rtime_"):
            value = f"{payload[6:8]}:{payload[8:10]}"
            if value not in daily_rules.REPORT_TIME_CHOICES:
                raise ValueError(f"неверное время {value}")
            await self._save_profile(uid, report_time=value)
        await self.show_notify_settings(message)

    # ---------- администратор ----------

    def _is_admin(self, uid: int) -> bool:
        return uid in settings.admin_ids

    async def show_stats(self, message: NormalizedMessage) -> None:
        profiles = await self.profiles.all_profiles()
        s = daily_rules.collect_stats(profiles, topic_progress.now())
        retention = f"{s['retained']} из {s['retention_base']}" if s["retention_base"] else "пока рано считать"
        lines = [
            "📈 Статистика бота",
            f"Учеников: {s['students']}, прошли диагностику: {s['diagnostic_done']}",
            f"Занимались сегодня: {s['active_today']}, за 7 дней: {s['active_week']}",
            f"Решено заданий сегодня: {s['solved_today']}, за 7 дней: {s['solved_week']} "
            f"(с первой попытки: {s['first_try_week']})",
            f"Слабых тем закреплено: {s['weak_mastered']} из {s['weak_topics']}",
            f"Вернулись через неделю после первого занятия: {retention}",
            f"🤖 Запросов к ИИ сегодня (тесты, уроки, фото, задания): {await self._ai_spent_today()}"
            + (f", лимит на ученика: {settings.AI_DAILY_LIMIT} в день" if getattr(settings, "AI_DAILY_LIMIT", 0) else ""),
            f"📚 В своём банке проверенных заданий: {await self.bank.size()}",
            "",
            *ai_client.usage_report(),
        ]
        if not profiles:
            lines.append("\n(Список учеников пуст или база недоступна.)")
        await self.answer(message, "\n".join(lines))


def _tasks_word(n: int) -> str:
    return "задание" if n % 10 == 1 and n % 100 != 11 else ("задания" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "заданий")


def _days_word(n: int) -> str:
    return "день" if n % 10 == 1 and n % 100 != 11 else ("дня" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "дней")
