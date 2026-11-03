from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, SmallInteger, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class StudentProfile(Base):
    """Профиль ученика: имя, класс и итоги диагностики. Хранится в PostgreSQL, чтобы не терялся при перезапуске."""

    __tablename__ = "student_profiles"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[Optional[str]] = mapped_column(String(64))
    grade: Mapped[Optional[int]] = mapped_column(SmallInteger)

    # none — не проходил, in_progress — начал, done — прошёл, skipped — пропустил
    diagnostic_status: Mapped[str] = mapped_column(String(16), default="none", server_default="none")
    diagnostic_grade: Mapped[Optional[int]] = mapped_column(SmallInteger)
    diagnostic_summary: Mapped[Optional[str]] = mapped_column(Text)
    # JSON: {"Математика": {"correct": 2, "total": 3}, ...}
    diagnostic_scores: Mapped[Optional[str]] = mapped_column(Text)
    # JSON: [{"subject_id": "math", "subject": "Математика", "topic": "Задачи на движение"}, ...]
    weak_topics: Mapped[Optional[str]] = mapped_column(Text)
    # JSON: прогресс по темам и интервальное повторение (см. app/services/progress.py)
    topic_progress: Mapped[Optional[str]] = mapped_column(Text)

    # --- Ежедневные занятия и уведомления ---
    chat_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    notify_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    notify_paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    daily_time: Mapped[Optional[str]] = mapped_column(String(5))      # «16:00» по местному времени
    report_time: Mapped[Optional[str]] = mapped_column(String(5))     # «19:30»
    tz_offset: Mapped[Optional[int]] = mapped_column(SmallInteger)    # минуты от UTC; пусто — UTC+5
    last_daily_date: Mapped[Optional[str]] = mapped_column(String(10))
    last_report_date: Mapped[Optional[str]] = mapped_column(String(10))
    last_active_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    streak_days: Mapped[Optional[int]] = mapped_column(SmallInteger)
    last_study_date: Mapped[Optional[str]] = mapped_column(String(10))
    # JSON: {"2026-11-02": {"solved": 3, "first_try": 2}} — решённые задания по дням (для отчёта и /stats)
    activity: Mapped[Optional[str]] = mapped_column(Text)
    # Обучение («как пользоваться ботом») пройдено — первоклассникам оно больше не запускается само
    tutorial_done: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # JSON: очки, счётчики и открытые достижения (см. app/services/rewards.py)
    rewards: Mapped[Optional[str]] = mapped_column(Text)
    # JSON: расписание уроков {"0": ["math", "rus"], ...} (0 — понедельник), см. app/services/schedule.py
    schedule: Mapped[Optional[str]] = mapped_column(Text)
    # JSON: оценки за уроки [{"date": "2026-11-02", "subject_id": "math", "topic": "Дроби", "mark": 5}, ...]
    lesson_marks: Mapped[Optional[str]] = mapped_column(Text)
    # JSON: учебник по каждому предмету {"math": "Моро (Школа России)", ...}
    textbooks: Mapped[Optional[str]] = mapped_column(Text)
    # JSON: подключённые родители [{"user_id": 123, "chat_id": 456}] и дата последнего отчёта родителям
    parents: Mapped[Optional[str]] = mapped_column(Text)
    last_parent_report: Mapped[Optional[str]] = mapped_column(String(10))
    # Когда дано согласие на обработку данных и версия политики: «2026-11-01T19:40:00+00:00 v2026-11»
    consent_at: Mapped[Optional[str]] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
