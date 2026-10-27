from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.database.models import User


async def check_admin(session: AsyncSession, user_id: int) -> bool:
    if user_id in settings.admin_ids:
        return True
    return bool(await session.scalar(select(User.is_admin).where(User.id == user_id)))


class IsAdmin(BaseFilter):
    async def __call__(self, event: TelegramObject, session: AsyncSession, event_from_user=None) -> bool:
        return bool(event_from_user) and await check_admin(session, event_from_user.id)
