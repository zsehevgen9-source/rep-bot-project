"""Часовой пояс ученика: чтобы задания и отчёты приходили по его местному времени.

MAX не сообщает, где находится ученик. Поэтому один раз (после выбора класса) бот спрашивает:
ученик пишет свой город («Казань», «я из Перми») или нажимает кнопку с временем, которое видит на часах.
Поменять можно в «⚙️ Настройки → 🔔 Уведомления и время».
"""
from datetime import timedelta

from app.handlers.tutor.common import *  # noqa: F401,F403
from app.handlers.tutor.tutorial import KIDS_MAX_GRADE
from app.services import timezones


class TimezoneMixin:
    async def ask_timezone(self, message: NormalizedMessage, then: str) -> None:
        """then: «g<класс>» — после ответа продолжить знакомство, «n» — вернуться в настройки уведомлений."""
        uid = message.user_id
        await self.redis.set_state(f"awaiting_tz_{uid}", then, ex=3600)
        now = topic_progress.now()
        buttons = [
            _btn(f"{(now + timedelta(hours=h)).strftime('%H:%M')} {city}", f"tz_{h}_{then}")
            for h, city in timezones.ZONES
        ]
        rows = _fit_rows(buttons, max_per_row=2)
        rows.append([_btn("🤷 Не знаю", f"tz_x_{then}")])
        if then == "n":
            rows.append([_back("notify_settings")])
        await self._send_with_voice(
            message,
            "🕐 Чтобы я присылал задания вовремя, а не ночью, скажи, где ты живёшь.\n\n"
            "Напиши свой город (например: «Казань») или нажми кнопку, где время совпадает с твоими часами 👇",
            keyboard=_kb(rows),
            voice_text="Чтобы я присылал задания вовремя, а не ночью, напиши свой город. "
                       "Или нажми кнопку, где время такое же, как у тебя на часах.",
        )

    async def set_timezone(self, message: NormalizedMessage, payload: str) -> None:
        """Кнопка «tz_<часы>_<куда дальше>» или «tz_x_…» (не знаю)."""
        _, hours, then = payload.split("_", 2)
        if hours == "x":
            await self._save_timezone(message, daily_rules.DEFAULT_TZ_OFFSET, then, guessed=True)
            return
        if not hours.isdigit() or int(hours) not in [h for h, _ in timezones.ZONES]:
            raise ValueError(f"неверный часовой пояс {payload}")
        await self._save_timezone(message, int(hours) * 60, then)

    async def timezone_from_text(self, message: NormalizedMessage, text: str, then: str) -> None:
        """Ученик написал город или время."""
        now = topic_progress.now()
        offset = timezones.detect_city(text)
        if offset is None:
            offset = timezones.detect_time(text, now.hour, now.minute)
        if offset is None:
            await self._send_with_voice(
                message,
                "Я не узнал такой город 🙈 Напиши крупный город рядом с тобой или нажми кнопку с временем выше.",
            )
            return
        await self._save_timezone(message, offset, then)

    async def _save_timezone(self, message: NormalizedMessage, offset: int, then: str, guessed: bool = False) -> None:
        uid = message.user_id
        await self.redis.delete_state(f"awaiting_tz_{uid}")
        await self._save_profile(uid, tz_offset=offset)
        local = daily_rules.local_now(topic_progress.now(), offset).strftime("%H:%M")
        if guessed:
            note = f"Хорошо! Пока считаю, что ты живёшь по времени {timezones.zone_name(offset)}. Поменять можно в настройках."
        else:
            note = f"Запомнил: {timezones.zone_name(offset)}, у тебя сейчас {local} 🕐"
        await self.answer(message, note)
        if then == "n":
            await self.show_notify_settings(message)
        elif then.startswith("g") and then[1:].isdigit():
            await self.after_grade_chosen(message, int(then[1:]))
        else:
            grade = await self._get_grade(uid)
            if grade:
                await self.show_subjects(message, grade)

    async def after_grade_chosen(self, message: NormalizedMessage, grade: int) -> None:
        """Что дальше после выбора класса: обучение (1–2 класс) или сразу меню.
        Диагностика — не при регистрации, а входным тестом при первом входе в каждый предмет."""
        uid = message.user_id
        if grade <= KIDS_MAX_GRADE and not (await self._load_profile(uid)).get("tutorial_done"):
            # Первокласснику сложно читать меню — сначала показываем, как со мной заниматься
            await self.start_tutorial(message, after="menu")
        else:
            await self.show_subjects(message, grade, first=True)
