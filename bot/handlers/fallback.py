from aiogram import Router
from aiogram.types import CallbackQuery

router = Router()


@router.callback_query()
async def stale_button(cb: CallbackQuery) -> None:
    """Подключается последним: кнопка устарела, потеряно состояние или нет доступа. Без ответа у клиента вечный спиннер."""
    await cb.answer("Эта кнопка больше не актуальна. Откройте /menu", show_alert=True)
