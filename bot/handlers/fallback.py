import logging

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, ErrorEvent

router = Router()
log = logging.getLogger(__name__)


@router.callback_query()
async def stale_button(cb: CallbackQuery) -> None:
    """Подключается последним: кнопка устарела, потеряно состояние или нет доступа. Без ответа у клиента вечный спиннер."""
    await cb.answer("Эта кнопка больше не актуальна. Откройте /menu", show_alert=True)


@router.errors()
async def on_error(event: ErrorEvent) -> bool:
    """Любая необработанная ошибка в хендлере: в лог, а пользователь не остаётся с вечным спиннером."""
    log.error("Unhandled error in handler", exc_info=event.exception)
    update = event.update
    try:
        if update.callback_query:
            await update.callback_query.answer("⚠️ Что-то пошло не так. Попробуйте ещё раз или откройте /menu",
                                               show_alert=True)
        elif update.message:
            await update.message.answer("⚠️ Что-то пошло не так. Попробуйте ещё раз или откройте /menu")
    except TelegramAPIError:
        pass
    return True
