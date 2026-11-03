from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.api_client import MaxApiClient
from app.services.redis_client import RedisClient
from app.schemas.payloads import NormalizedMessage


class BaseHandler:
    def __init__(self, api: MaxApiClient, db: AsyncSession, redis: RedisClient):
        self.api = api
        self.db = db
        self.redis = redis

    async def answer(
        self,
        message: NormalizedMessage,
        text: str,
        keyboard: Optional[Dict[str, Any]] = None,
        attachments: Optional[list] = None,
    ):
        if attachments:
            return await self.api.send_message(
                user_id=message.user_id, text=text, keyboard=keyboard, attachments=attachments
            )
        return await self.api.send_message(user_id=message.user_id, text=text, keyboard=keyboard)
        
