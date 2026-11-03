# app/models/__init__.py
from app.models.base import Base
from app.models.profile import StudentProfile
from app.models.user import User

__all__ = [
    "Base",
    "StudentProfile",
    "User",
]