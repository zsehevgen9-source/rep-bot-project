import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.profile import StudentProfile

logger = logging.getLogger(__name__)

FIELDS = {
    "name", "grade", "diagnostic_status", "diagnostic_grade",
    "diagnostic_summary", "diagnostic_scores", "weak_topics", "topic_progress",
    "chat_id", "notify_enabled", "notify_paused", "daily_time", "report_time", "tz_offset",
    "last_daily_date", "last_report_date", "last_active_at", "streak_days", "last_study_date", "activity",
    "tutorial_done", "rewards", "schedule", "lesson_marks", "textbooks", "parents", "last_parent_report",
    "consent_at",
}


class ProfileStore:
    """Чтение и запись профиля ученика в PostgreSQL.

    Если база недоступна, методы не падают: get() возвращает None, save() — False,
    а бот продолжает работать на данных из Redis.
    """

    def __init__(self, db: Optional[AsyncSession]):
        self.db = db

    async def get(self, user_id: int) -> Optional[Dict[str, Any]]:
        if self.db is None:
            return None
        try:
            profile = await self.db.get(StudentProfile, user_id)
            if profile is None:
                return None
            return {field: getattr(profile, field) for field in FIELDS}
        except Exception as e:
            logger.error("Не удалось прочитать профиль %s: %s", user_id, e)
            await self._rollback()
            return None

    async def save(self, user_id: int, **fields: Any) -> bool:
        if self.db is None:
            return False
        unknown = set(fields) - FIELDS
        if unknown:
            raise ValueError(f"неизвестные поля профиля: {unknown}")
        try:
            profile = await self.db.get(StudentProfile, user_id)
            if profile is None:
                profile = StudentProfile(user_id=user_id)
                self.db.add(profile)
            for field, value in fields.items():
                setattr(profile, field, value)
            await self.db.commit()
            return True
        except Exception as e:
            logger.error("Не удалось сохранить профиль %s: %s", user_id, e)
            await self._rollback()
            return False

    async def all_profiles(self) -> List[Dict[str, Any]]:
        """Все профили (для рассылки заданий и /stats). Если база недоступна — пустой список."""
        if self.db is None:
            return []
        try:
            from sqlalchemy import select

            result = await self.db.execute(select(StudentProfile))
            return [
                {"user_id": p.user_id, **{field: getattr(p, field) for field in FIELDS}}
                for p in result.scalars().all()
            ]
        except Exception as e:
            logger.error("Не удалось прочитать список профилей: %s", e)
            await self._rollback()
            return []

    async def delete(self, user_id: int) -> bool:
        if self.db is None:
            return False
        try:
            profile = await self.db.get(StudentProfile, user_id)
            if profile is not None:
                await self.db.delete(profile)
                await self.db.commit()
            return True
        except Exception as e:
            logger.error("Не удалось удалить профиль %s: %s", user_id, e)
            await self._rollback()
            return False

    async def _rollback(self) -> None:
        try:
            await self.db.rollback()
        except Exception:
            pass


def load_json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value  # уже разобрано
    if not value:
        return default
    try:
        return json.loads(value)
    except ValueError:
        return default


def weak_topics_for(profile: Optional[Dict[str, Any]], subject_id: str) -> List[str]:
    """Слабые темы ученика по предмету (по итогам диагностики)."""
    if not profile:
        return []
    return [w.get("topic", "") for w in load_json(profile.get("weak_topics"), []) if w.get("subject_id") == subject_id and w.get("topic")]
