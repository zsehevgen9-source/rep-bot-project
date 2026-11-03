from typing import Optional
from pydantic import BaseModel, Field


class Sender(BaseModel):
    user_id: int
    name: Optional[str] = None


class Recipient(BaseModel):
    chat_id: int


class NormalizedMessage(BaseModel):
    chat_id: Optional[int] = None
    user_id: Optional[int] = None
    message_id: str
    text: Optional[str] = None
    payload: Optional[str] = None  # For callback data
    audio_url: Optional[str] = None
    file_url: Optional[str] = None    # фото или документ (например, расписание уроков)
    file_name: Optional[str] = None
    file_kind: Optional[str] = None   # "image" или "document"

    @property
    def is_callback(self) -> bool:
        """Returns True if the message is a callback (has payload)."""
        return self.payload is not None
