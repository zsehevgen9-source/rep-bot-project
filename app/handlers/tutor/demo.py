"""/demo_student — демо-ученик для презентации: готовая история занятий за две недели.

Заполняет профиль АДМИНИСТРАТОРА (только его и только после подтверждения): входные тесты, темы с разными статусами,
оценки за уроки, активность по дням, серию, награды, расписание, учебники. Так на сцене сразу видно
«📊 Прогресс», «🏆 Награды», вечерний отчёт и отчёт родителю — не нужно заниматься две недели.
"""
from datetime import timedelta

from app.handlers.tutor.common import *  # noqa: F401,F403
from app.services.programs import programs


def build_demo(grade: int, today, seed: int = 7) -> Dict[str, Any]:
    """Данные демо-ученика (без MAX и базы) — детерминированно, чтобы демо всегда выглядело одинаково."""
    rnd = random.Random(seed + grade)
    subjects = [sid for _, sid in SUBJECTS[grade]]
    moment = topic_progress.now()
    progress: Dict[str, Any] = {}
    scores, weak_topics = {}, []
    for sid in subjects:
        title = subject_title(grade, sid)
        passed = programs.passed_topics(grade, sid, today) or [title]
        topics = passed[-6:]
        right = 0
        for i, topic in enumerate(topics):
            status = ["mastered", "known", "learning", "mastered", "weak", "learning"][(i + rnd.randint(0, 2)) % 6]
            box = {"mastered": 3, "known": 2, "learning": 1, "weak": 0}[status]
            entry = {
                "grade": grade, "subject_id": sid, "subject": title, "topic": topic, "status": status, "box": box,
                "correct": box + 1, "wrong": 1 if status in ("weak", "learning") else 0,
                "was_weak": status == "weak" or (status == "mastered" and i % 2 == 0),
                "next_review": (moment + timedelta(days=rnd.choice([-1, 0, 1, 3, 7]))).isoformat(),
                "last_seen": (moment - timedelta(days=rnd.randint(0, 12))).isoformat(), "explained": True,
            }
            progress[topic_progress.topic_key(grade, sid, topic)] = entry
            if status != "weak":
                right += 1
            else:
                weak_topics.append({"subject_id": sid, "subject": title, "topic": topic})
        scores[title] = {"correct": min(right, 5), "total": 5}

    activity, solved_total, first_total, points_total = {}, 0, 0, 0
    study_days = [today - timedelta(days=d) for d in range(13, -1, -1) if (today - timedelta(days=d)).weekday() < 5]
    for day in study_days:
        solved = rnd.randint(3, 6)
        first = max(1, int(solved * rnd.uniform(0.6, 0.9)))
        points = first * 2 + (solved - first) + 3
        activity[day.isoformat()] = {"solved": solved, "first_try": first, "points": points}
        solved_total, first_total, points_total = solved_total + solved, first_total + first, points_total + points

    marks = []
    for n, day in enumerate(study_days[-8:]):
        sid = subjects[n % min(3, len(subjects))]
        topic = (programs.passed_topics(grade, sid, today) or [subject_title(grade, sid)])[-1 - n % 3]
        marks.append({"date": day.isoformat(), "grade": grade, "subject_id": sid, "topic": topic,
                      "mark": [5, 4, 5, 3, 4, 5, 4, 5][n]})

    schedule = {}
    others = [s for s in subjects if s not in ("math", "rus")]
    for day in range(5):
        lessons = ["math", "rus"] + [others[(day * 2 + k) % len(others)] for k in range(2)] if others else ["math", "rus"]
        schedule[str(day)] = lessons

    rewards = {"points": points_total + 5 + 1, "solved": solved_total, "first_try": first_total,
               "plans": len(study_days) - 2, "persistent": 2}
    return {
        "progress": progress, "scores": scores, "weak": weak_topics[:6], "activity": activity, "marks": marks,
        "schedule": schedule, "rewards": rewards, "streak": len(study_days[-8:]),
        "textbooks": {sid: programs.authors_for(grade, sid)[0] for sid in subjects if programs.authors_for(grade, sid)},
    }


class DemoMixin:
    async def ask_demo_student(self, message: NormalizedMessage, arg: str) -> None:
        uid = message.user_id
        grade = int(arg) if arg.isdigit() and int(arg) in SUBJECTS else (await self._get_grade(uid) or 4)
        await self.answer(
            message,
            f"🎭 Демо-ученик, {grade} класс: заполню ТВОЙ профиль историей занятий за две недели — входные тесты, "
            "темы, оценки, награды, серия, расписание и учебники.\n\n"
            "⚠️ Твои настоящие прогресс, оценки и расписание будут заменены. Продолжить?",
            keyboard=_kb([[_btn("✅ Да, заполнить", f"demo_yes_{grade}")], [_btn("Отмена", "menu")]]),
        )

    async def fill_demo_student(self, message: NormalizedMessage, grade: int) -> None:
        uid = message.user_id
        if not self._is_admin(uid) or grade not in SUBJECTS:
            raise ValueError("демо-ученик доступен только администратору")
        profile = await self._load_profile(uid)
        today = daily_rules.local_now(topic_progress.now(), profile.get("tz_offset")).date()
        demo = build_demo(grade, today)
        name = profile.get("name") or await self.redis.get_state(f"name_{uid}") or "Ваня"
        rewards = dict(demo["rewards"])
        mastered = sum(1 for e in demo["progress"].values() if e["status"] == "mastered")
        rewards_rules.new_achievements(rewards, {
            "streak": demo["streak"], "mastered": mastered,
            "weak_mastered": sum(1 for e in demo["progress"].values() if e["was_weak"] and e["status"] == "mastered"),
            "diag_done": True, "tutorial_done": True, "weekly_goal_done": True,
        })
        await self._save_profile(
            uid, name=name, grade=grade, tz_offset=profile.get("tz_offset") or 300, tutorial_done=True,
            diagnostic_status="done", diagnostic_grade=grade,
            diagnostic_summary="Входные тесты по предметам — итоги выше.",
            diagnostic_scores=json.dumps(demo["scores"], ensure_ascii=False),
            weak_topics=json.dumps(demo["weak"], ensure_ascii=False),
            topic_progress=json.dumps(demo["progress"], ensure_ascii=False),
            activity=json.dumps(demo["activity"], ensure_ascii=False),
            lesson_marks=json.dumps(demo["marks"], ensure_ascii=False),
            schedule=json.dumps(demo["schedule"], ensure_ascii=False),
            textbooks=json.dumps(demo["textbooks"], ensure_ascii=False),
            rewards=json.dumps(rewards, ensure_ascii=False),
            streak_days=demo["streak"], last_study_date=today.isoformat(),
        )
        await self.redis.set_state(f"grade_{uid}", str(grade), ex=GRADE_TTL)
        await self.redis.set_state(f"name_{uid}", name, ex=GRADE_TTL)
        levels = {sid: {"level": 3 if i % 3 == 0 else 2, "run": 0} for i, (_, sid) in enumerate(SUBJECTS[grade])}
        await self.redis.set_state(f"levels_{uid}", json.dumps(levels), ex=GRADE_TTL)
        for key in ("daily_plan", "stest", "lesson", "diag_state"):
            await self.redis.delete_state(f"{key}_{uid}")
        await self.show_subjects(
            message, grade,
            intro="🎭 Готово! Демо-ученик с историей за две недели. Загляни в «📊 Прогресс», «🏆 Награды», "
                  "«🗓 Расписание»; /demo — задание на день, /demo_report — вечерний отчёт, /demo_parent — отчёт родителю.",
        )
