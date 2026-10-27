"""Заявки на оплату: проверка и подтверждение админом."""
import math

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Order, User
from bot.keyboards import admin as K
from bot.middlewares.admin import IsAdmin
from bot.services.plans import grant_subscription, utcnow
from bot.ui import SEP, Nav, back, esc, fmt_dt, kb, show
from bot.views import ORDER_STATUS, days_text, fmt_usd

router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())
PAGE = 6


def order_text(o: Order, user: User | None, admin_name: str = "") -> str:
    who = (f'<a href="tg://user?id={o.user_id}">{esc(user.first_name or "пользователь")}</a>'
           f"{' @' + esc(user.username) if user and user.username else ''} · <code>{o.user_id}</code>")
    tail = f"\n👮 {esc(admin_name)}" if admin_name else ""
    return (f"🧾 <b>Заявка #{o.id}</b> · {ORDER_STATUS.get(o.status, o.status)}\n{SEP}\n👤 {who}\n"
            f"💎 {esc(o.plan_name)} · {days_text(o.days)} · {fmt_usd(o.price_usd)}\n"
            f"💰 К оплате: <b>{esc(o.amount_text)}</b> ({esc(o.method_title)})\n"
            f"📬 Реквизиты: <code>{esc(o.details)}</code>\n"
            f"🕒 Создана: {fmt_dt(o.created_at)} · оплачена: {fmt_dt(o.paid_at)}{tail}")


@router.callback_query(Nav.filter(F.a == "adm_orders"))
async def orders(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    where = Order.status == "paid"
    total = await session.scalar(select(func.count(Order.id)).where(where))
    pages = max(math.ceil(total / PAGE), 1)
    page = min(callback_data.p, pages - 1)
    items = (await session.scalars(select(Order).where(where).order_by(Order.paid_at)
                                   .offset(page * PAGE).limit(PAGE))).all()
    done = await session.scalar(select(func.count(Order.id)).where(Order.status == "confirmed"))
    await show(cb, f"🧾 <b>Заявки на проверке</b> · {total}\n{SEP}\n" + (
        "Откройте заявку, сверьте платёж и подтвердите." if total else "Новых заявок нет 🎉") +
        f"\n\nВсего подтверждено: {done}", K.orders_list(list(items), page, pages))


@router.callback_query(Nav.filter(F.a == "adm_order"))
async def order_open(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    o = await session.get(Order, callback_data.id)
    if not o:
        return await cb.answer("Заявка не найдена", show_alert=True)
    user = await session.get(User, o.user_id)
    markup = K.order_review(o.id) if o.status == "paid" else kb()
    markup.inline_keyboard.append(back("adm_orders", text="◀️ К заявкам"))
    await show(cb, order_text(o, user), markup)


@router.callback_query(Nav.filter(F.a.in_({"adm_order_ok", "adm_order_no"})))
async def order_decide(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    o = await session.get(Order, callback_data.id)
    if not o or o.status != "paid":
        return await cb.answer("Заявка уже обработана или не ждёт проверки", show_alert=True)
    ok = callback_data.a == "adm_order_ok"
    o.status = "confirmed" if ok else "rejected"
    o.decided_by, o.decided_at = cb.from_user.id, utcnow()
    await session.commit()
    if ok:
        await grant_subscription(
            session, o.user_id, plan_name=o.plan_name, days=o.days, max_accounts=o.max_accounts,
            daily_limit=o.daily_limit, max_recipients=o.max_recipients, max_parse=o.max_parse, plan_id=o.plan_id,
            source="order", amount=o.price_usd, order_id=o.id, granted_by=cb.from_user.id)
    user_text = (f"✅ Оплата подтверждена! Тариф <b>{esc(o.plan_name)}</b> активирован ({days_text(o.days)})."
                 if ok else f"❌ Заявка #{o.id} отклонена: платёж не найден. Если вы оплатили, свяжитесь с поддержкой.")
    try:
        await cb.bot.send_message(o.user_id, user_text)
    except TelegramAPIError:
        pass
    user = await session.get(User, o.user_id)
    final = order_text(o, user, cb.from_user.full_name)
    for chat_id, msg_id in o.admin_msgs or []:  # убрать кнопки у всех админов, показать итог
        try:
            await cb.bot.edit_message_text(final, chat_id=chat_id, message_id=msg_id)
        except TelegramAPIError:
            pass
    await cb.answer("Подтверждено" if ok else "Отклонено")
    if cb.message and (cb.message.chat.id, cb.message.message_id) not in {tuple(x) for x in o.admin_msgs or []}:
        markup = kb(back("adm_orders", text="◀️ К заявкам"))
        await show(cb, final, markup)
