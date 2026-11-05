"""Фоновая рассылка: раз в минуту проверяет, кому пора прислать задание на день или вечерний отчёт."""
import asyncio
import logging
from typing import Callable, Optional

from app.services import daily as daily_rules
from app.services import progress as topic_progress
from app.services.profiles import ProfileStore

logger = logging.getLogger(__name__)
CHECK_EVERY_SECONDS = 60


async def notify_once(session_factory: Callable, make_handler: Callable, make_message: Callable) -> int:
    """Один проход: отправляет всё, что пора. Возвращает число отправленных сообщений."""
    sent = 0
    now = topic_progress.now()
    async with session_factory() as session:
        profiles = await ProfileStore(session).all_profiles()
    for profile in profiles:
        actions = daily_rules.plan_actions(profile, now)
        if daily_rules.parent_report_due(profile, now):
            actions.append("parent_report")   # недельный отчёт родителям — в воскресенье вечером
        if profile.get("parents") not in (None, "", "[]", []) and \
                daily_rules.evening_check_due(profile, now, daily_rules.FAMILY_CHECK_HOUR):
            actions.append("family_check")    # давно не занимался — сигнал родителю
        if daily_rules.evening_check_due(profile, now, daily_rules.CLASS_CHECK_HOUR):
            actions.append("class_check")     # сроки заданий учителя
        for action in actions:
            try:
                async with session_factory() as session:
                    handler = make_handler(session)
                    await handler.run_scheduled(make_message(profile), action)
                sent += 1
                logger.info("Отправлено «%s» ученику %s", action, profile["user_id"])
            except Exception:
                logger.exception("Не удалось отправить «%s» ученику %s", action, profile.get("user_id"))
    return sent


async def run_notifier(session_factory: Callable, make_handler: Callable, make_message: Callable,
                       stop: Optional[asyncio.Event] = None) -> None:
    logger.info("Рассылка заданий запущена (по будням, задание %s, отчёт %s по местному времени ученика).",
                daily_rules.DEFAULT_DAILY_TIME, daily_rules.DEFAULT_REPORT_TIME)
    while stop is None or not stop.is_set():
        try:
            await notify_once(session_factory, make_handler, make_message)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка в рассылке заданий")
        await asyncio.sleep(CHECK_EVERY_SECONDS)
