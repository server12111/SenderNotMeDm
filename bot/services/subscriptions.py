"""Фоновый воркер подписок: раз в час закрывает истёкшие и напоминает за 3 дня и за сутки."""
import asyncio
import logging
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.database.models import Subscription
from bot.services.plans import utcnow
from bot.ui import aware, fmt_dt

log = logging.getLogger(__name__)
INTERVAL = 3600


async def _notify(bot: Bot, user_id: int, text: str) -> None:
    try:
        await bot.send_message(user_id, text)
    except TelegramAPIError:
        pass  # пользователь заблокировал бота


async def tick(bot: Bot, sm: async_sessionmaker[AsyncSession]) -> None:
    now = utcnow()
    async with sm() as s:
        subs = (await s.scalars(select(Subscription).where(
            Subscription.status == "active", Subscription.expires_at.is_not(None)))).all()
        for sub in subs:
            left = aware(sub.expires_at) - now
            if left <= timedelta(0):
                sub.status = "expired"
                await _notify(bot, sub.user_id, f"⌛ Тариф <b>{sub.plan_name}</b> закончился. "
                                                "Лимиты вернулись к Free. Продлить: /menu → Профиль → Тарифы.")
            elif left <= timedelta(days=1) and not sub.notified_1d:
                sub.notified_1d = sub.notified_3d = True
                await _notify(bot, sub.user_id, f"⏰ Тариф <b>{sub.plan_name}</b> закончится "
                                                f"через сутки ({fmt_dt(sub.expires_at)}).")
            elif left <= timedelta(days=3) and not sub.notified_3d:
                sub.notified_3d = True
                await _notify(bot, sub.user_id, f"⏰ Тариф <b>{sub.plan_name}</b> закончится "
                                                f"{fmt_dt(sub.expires_at)} (меньше чем через 3 дня).")
        await s.commit()


async def expiry_loop(bot: Bot, sm: async_sessionmaker[AsyncSession]) -> None:
    while True:
        try:
            await tick(bot, sm)
        except Exception:
            log.exception("subscription tick failed")
        await asyncio.sleep(INTERVAL)
