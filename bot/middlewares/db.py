import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import func

from bot.config import settings
from bot.database.base import insert
from bot.database.models import User

SEEN_TTL = 120  # не чаще раза в 2 минуты пишем last_seen: меньше записей, меньше блокировок


class DbMiddleware(BaseMiddleware):
    """Даёт хендлерам `session`, upsert'ит пользователя и отсекает забаненных."""

    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self.sm = sm
        self._seen: dict[int, tuple[float, str | None, str | None]] = {}

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        async with self.sm() as session:
            data["session"] = session
            tg: TgUser | None = data.get("event_from_user")
            if tg and not tg.is_bot:
                prev = self._seen.get(tg.id)
                fresh = prev and time.monotonic() - prev[0] < SEEN_TTL and prev[1:] == (tg.username, tg.first_name)
                if not fresh:
                    await self._upsert(session, tg)
                    self._seen[tg.id] = (time.monotonic(), tg.username, tg.first_name)
                banned = await session.scalar(select(User.is_banned).where(User.id == tg.id))
                if banned and tg.id not in settings.admin_ids:
                    text = "🚫 Доступ к боту ограничен администратором."
                    if getattr(event, "callback_query", None):
                        await event.callback_query.answer(text, show_alert=True)
                    else:
                        await data["bot"].send_message(tg.id, text)
                    return None
            return await handler(event, data)

    @staticmethod
    async def _upsert(session: AsyncSession, tg: TgUser) -> None:
        stmt = insert(User).values(
            id=tg.id, username=tg.username, first_name=tg.first_name, is_admin=tg.id in settings.admin_ids,
        ).on_conflict_do_update(
            index_elements=[User.id],
            set_=dict(username=tg.username, first_name=tg.first_name, is_active=True, last_seen=func.now()),
        )
        await session.execute(stmt)
        await session.commit()
