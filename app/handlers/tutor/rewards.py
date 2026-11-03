"""Награды: очки (⭐ / ⚡), уровни, цель недели и достижения — по режиму «Играйка / Тренер / Стратег»."""
from app.handlers.tutor.common import *  # noqa: F401,F403


class RewardsMixin:
    """Начисление наград и экран «🏆 Награды»."""

    async def _mode(self, uid: int) -> str:
        return rewards_rules.mode_for_grade(await self._get_grade(uid))

    async def _reward_facts(self, uid: int, profile: Dict[str, Any], grade: Optional[int]) -> Dict[str, Any]:
        progress = load_json(profile.get("topic_progress"), {})
        today = daily_rules.local_now(topic_progress.now(), profile.get("tz_offset")).date()
        week = rewards_rules.week_stats(load_json(profile.get("activity"), {}), today)
        mode = rewards_rules.mode_for_grade(grade)
        return {
            "streak": profile.get("streak_days") or 0,
            "mastered": sum(1 for e in progress.values() if e.get("status") == "mastered"),
            "weak_mastered": sum(1 for e in progress.values() if e.get("was_weak") and e.get("status") == "mastered"),
            "diag_done": profile.get("diagnostic_status") == "done",
            "tutorial_done": bool(profile.get("tutorial_done")),
            "weekly_goal_done": week["solved"] >= rewards_rules.WEEKLY_GOAL[mode],
            "week": week,
        }

    async def _reward(self, uid: int, event: str, attempt: Optional[int] = None, times: int = 1) -> str:
        """Начисляет очки за событие, открывает достижения. Возвращает текст для ученика («+2 ⭐ …»)."""
        profile = await self._load_profile(uid)
        grade = await self._get_grade(uid)
        mode = rewards_rules.mode_for_grade(grade)
        rewards = load_json(profile.get("rewards"), {})
        level_before, _ = rewards_rules.level(rewards.get("points", 0), mode)
        gained = sum(rewards_rules.add_points(rewards, event) for _ in range(max(1, times)))
        if attempt and attempt >= 3:
            rewards["persistent"] = rewards.get("persistent", 0) + 1

        # Очки за день — для вечернего отчёта
        today = daily_rules.local_now(topic_progress.now(), profile.get("tz_offset")).date().isoformat()
        activity = load_json(profile.get("activity"), {})
        if gained:
            day = activity.setdefault(today, {"solved": 0, "first_try": 0})
            day["points"] = day.get("points", 0) + gained

        facts = await self._reward_facts(uid, {**profile, "activity": activity}, grade)
        fresh = rewards_rules.new_achievements(rewards, facts)
        await self._save_profile(
            uid, rewards=json.dumps(rewards, ensure_ascii=False), activity=json.dumps(activity, ensure_ascii=False),
        )

        lines = []
        label = rewards_rules.gained_label(gained, mode)
        if label:
            lines.append(f"{label} · всего {rewards_rules.points_label(rewards['points'], mode)}")
        elif mode == rewards_rules.STRATEGY and event in ("first_try", "hinted"):
            goal = rewards_rules.WEEKLY_GOAL[mode]
            lines.append(f"Неделя: {min(facts['week']['solved'], goal)}/{goal} заданий")
        level_after, _ = rewards_rules.level(rewards.get("points", 0), mode)
        certificates = []
        if level_after and level_after != level_before:
            lines.append(f"🎉 Новый уровень: {level_after}!")
            certificates.append([f"Новый уровень: {level_after}",
                                 f"набрано очков: {rewards_rules.points_label(rewards['points'], mode).split()[0]}"])
        for achievement in fresh:
            title, _ = rewards_rules.ACHIEVEMENTS[achievement]
            lines.append(f"🏆 Новое достижение: {title}!")
            certificates.append([f"Достижение «{title.split(' ', 1)[-1]}»", "за старание и успехи в учёбе"])
        if certificates:
            # Грамоту пришлём картинкой сразу после этого сообщения (см. CoreMixin._send_with_voice)
            pending = load_json(await self.redis.get_state(f"cert_{uid}"), []) + certificates
            await self.redis.set_state(f"cert_{uid}", json.dumps(pending[-3:], ensure_ascii=False), ex=DAY)
        return ("\n\n" + "\n".join(lines)) if lines else ""

    async def menu_status(self, uid: int, grade: int) -> str:
        """Строка над главным меню: режим, очки, уровень, серия, цель недели."""
        profile = await self._load_profile(uid)
        mode = rewards_rules.mode_for_grade(grade)
        facts = await self._reward_facts(uid, profile, grade)
        status = rewards_rules.status_line(load_json(profile.get("rewards"), {}), mode, facts["streak"], facts["week"])
        title = load_json(await self.redis.get_state(f"chest_{uid}"), {}).get("title")
        if title:
            status = f"{status} · 🏅 {title}" if status else f"🏅 {title}"
        return f"{rewards_rules.MODE_NAMES[mode]} · {status}" if status else ""

    async def show_rewards(self, message: NormalizedMessage, grade: int) -> None:
        uid = message.user_id
        profile = await self._load_profile(uid)
        mode = rewards_rules.mode_for_grade(grade)
        rewards = load_json(profile.get("rewards"), {})
        facts = await self._reward_facts(uid, profile, grade)
        points = rewards.get("points", 0)
        goal = rewards_rules.WEEKLY_GOAL[mode]
        week = facts["week"]

        lines = [f"🏆 Награды · режим {rewards_rules.MODE_NAMES[mode]}", ""]
        name, to_next = rewards_rules.level(points, mode)
        if name:
            lines.append(f"{rewards_rules.points_label(points, mode)} · уровень: {name}")
            if to_next:
                lines.append(f"До следующего уровня: {rewards_rules.points_label(to_next, mode)}")
        done = min(week["solved"], goal)
        bar = "▓" * round(10 * done / goal) + "░" * (10 - round(10 * done / goal))
        lines.append(f"Цель недели: {bar} {done}/{goal} заданий")
        if mode == rewards_rules.STRATEGY and week["solved"]:
            lines.append(f"Точность с первой попытки за неделю: {round(100 * week['first_try'] / week['solved'])}%")
        if facts["streak"] > 1:
            lines.append(f"Серия: 🔥 {facts['streak']} учебных дней подряд")

        have = set(rewards.get("achievements") or [])
        lines += ["", f"Достижения: {len(have)} из {len(rewards_rules.ACHIEVEMENTS)}"]
        for key, (title, how) in rewards_rules.ACHIEVEMENTS.items():
            lines.append(f"✅ {title}" if key in have else f"🔒 {title} — {how}")

        voice = f"У тебя {len(have)} достижений. Цель недели: {done} из {goal} заданий."
        if name:
            voice = f"Твой уровень: {name}. " + voice
        await self._quiet(message, "\n".join(lines), keyboard=_kb([[_btn("🗼 Мой маяк", "lh"), _btn("📒 Альбом", "album")], [_back(f"subjects_{grade}")]]),
                                    voice_text=voice)
