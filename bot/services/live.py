"""Живое обновление открытых карточек рассылок + уведомление о завершении."""
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from bot.database.models import Campaign, Notice
from bot.keyboards.admin import notice_card
from bot.keyboards.campaigns import campaign_card
from bot.ui import LIVE
from bot.views import campaign_card_text, notice_card_text

log = logging.getLogger(__name__)


async def _refresh(bot: Bot, key: str, text: str, markup) -> None:
    for (chat_id, msg_id), k in list(LIVE.items()):
        if k != key:
            continue
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup)
        except TelegramAPIError as e:
            if "not modified" not in str(e).lower():
                LIVE.pop((chat_id, msg_id), None)  # сообщение удалено/недоступно


async def _notify(bot: Bot, chat_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id, text)
    except TelegramAPIError:
        log.debug("finish notify failed", exc_info=True)


class LiveUpdater:
    """Рассылки клиентов (с аккаунтов)."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def __call__(self, c: Campaign, finished: bool) -> None:
        await _refresh(self.bot, f"c:{c.id}", campaign_card_text(c), campaign_card(c))
        if finished:
            await _notify(self.bot, c.owner_id,
                          f"{'✅ Рассылка завершена' if c.status == 'done' else '🛑 Рассылка остановлена'}: "
                          f"<b>{c.name}</b>\n✅ {c.sent} · 🚫 {c.blocked} · ⚠️ {c.failed} из {c.total}")


class NoticeLive:
    """Рассылки админа по пользователям бота."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def __call__(self, n: Notice, finished: bool) -> None:
        await _refresh(self.bot, f"n:{n.id}", notice_card_text(n), notice_card(n))
        if finished:
            await _notify(self.bot, n.admin_id,
                          f"{'✅ Рассылка завершена' if n.status == 'done' else '🛑 Рассылка остановлена'}: "
                          f"<b>{n.name}</b>\n✅ {n.sent} · 🚫 {n.blocked} · ⚠️ {n.failed} из {n.total}")
