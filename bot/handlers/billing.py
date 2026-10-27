"""Покупка тарифа: выбор тарифа и способа оплаты → заявка → «Я оплатил» → проверка админом."""
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.database.models import Order, PaymentMethod, Plan, User
from bot.handlers.admin.orders import order_text
from bot.keyboards.admin import order_review
from bot.services.plans import free_limits, get_limits, utcnow
from bot.ui import SEP, Nav, back, btn, esc, fmt_dt, kb, nav, show, to_main
from bot.views import days_text, format_amount, fmt_usd, limits_text

router = Router()
MAX_OPEN_ORDERS = 3


@router.callback_query(Nav.filter(F.a == "plans"))
async def plans(cb: CallbackQuery, session: AsyncSession) -> None:
    items = (await session.scalars(select(Plan).where(Plan.is_active.is_(True))
                                   .order_by(Plan.sort, Plan.price))).all()
    cur = await get_limits(session, cb.from_user.id)
    free = await free_limits(session)
    lines = [f"💳 <b>Тарифы</b>\n{SEP}",
             f"Ваш тариф: <b>{esc(cur.name)}</b>" + (f" · до {fmt_dt(cur.expires_at)}" if cur.expires_at else ""), ""]
    lines += [f"🆓 <b>Free</b>: {free.max_accounts} акк. · {free.daily_limit}/сут · "
              f"{free.max_recipients or '∞'} получ."]
    lines += [f"💎 <b>{esc(p.name)}</b> · {fmt_usd(p.price)} · {days_text(p.duration_days)}: "
              f"{p.max_accounts} акк. · {p.daily_limit}/сут · {p.max_recipients or '∞'} получ." for p in items]
    if not items:
        lines.append("\n<i>Платные тарифы скоро появятся.</i>")
    rows = [[btn(f"💎 {p.name} · {fmt_usd(p.price)}", nav("plan", p.id), style="success")] for p in items]
    await show(cb, "\n".join(lines), kb(*rows, back("profile", text="◀️ Профиль")))


@router.callback_query(Nav.filter(F.a == "plan"))
async def plan(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    p = await session.get(Plan, callback_data.id)
    if not p or not p.is_active:
        return await cb.answer("Тариф недоступен", show_alert=True)
    methods = (await session.scalars(select(PaymentMethod).where(PaymentMethod.is_enabled.is_(True))
                                     .order_by(PaymentMethod.sort, PaymentMethod.id))).all()
    text = (f"💎 <b>{esc(p.name)}</b>\n{SEP}\n💵 Цена: <b>{fmt_usd(p.price)}</b>\n📅 Срок: <b>{days_text(p.duration_days)}"
            f"</b>\n{limits_text(p.max_accounts, p.daily_limit, p.max_recipients)}\n{SEP}\n" +
            ("Выберите способ оплаты:" if methods else "⚠️ Оплата временно недоступна: способы оплаты не настроены."))
    rows = [[btn(f"{m.title} · {format_amount(p.price, m.rate, m.currency)}", nav("pay", p.id, m.id))]
            for m in methods]
    await show(cb, text, kb(*rows, back("plans", text="◀️ К тарифам")))


@router.callback_query(Nav.filter(F.a == "pay"))
async def pay(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    p = await session.get(Plan, callback_data.id)
    m = await session.get(PaymentMethod, callback_data.p)
    if not p or not p.is_active or not m or not m.is_enabled:
        return await cb.answer("Недоступно", show_alert=True)
    open_orders = await session.scalar(select(func.count(Order.id)).where(
        Order.user_id == cb.from_user.id, Order.status.in_(("new", "paid"))))
    if open_orders >= MAX_OPEN_ORDERS:
        return await cb.answer("Слишком много открытых заявок. Дождитесь решения или отмените лишние.",
                               show_alert=True)
    o = Order(user_id=cb.from_user.id, plan_id=p.id, plan_name=p.name, days=p.duration_days,
              max_accounts=p.max_accounts, daily_limit=p.daily_limit, max_recipients=p.max_recipients,
              price_usd=p.price, method_id=m.id, method_title=m.title,
              amount_text=format_amount(p.price, m.rate, m.currency), details=m.details, status="new")
    session.add(o)
    await session.commit()
    await show(cb, f"🧾 <b>Заявка #{o.id}</b>\n{SEP}\n💎 {esc(p.name)} · {days_text(p.duration_days)}\n"
                   f"💰 К оплате: <b>{esc(o.amount_text)}</b> ({esc(m.title)})\n📬 Реквизиты:\n"
                   f"<code>{esc(m.details)}</code>\n{SEP}\n⚠️ Переведите указанную сумму, комиссию оплачивайте "
                   "сверху. После перевода нажмите «Я оплатил»: администратор проверит платёж и активирует тариф.",
               kb([btn("✅ Я оплатил", nav("ord_paid", o.id), style="success")],
                  [btn("✖️ Отменить заявку", nav("ord_cancel", o.id), style="danger")]))


@router.callback_query(Nav.filter(F.a == "ord_cancel"))
async def ord_cancel(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    o = await session.get(Order, callback_data.id)
    if o and o.user_id == cb.from_user.id and o.status == "new":
        o.status = "cancelled"
        await session.commit()
    await cb.answer("Заявка отменена")
    await plans(cb, session)


@router.callback_query(Nav.filter(F.a == "ord_paid"))
async def ord_paid(cb: CallbackQuery, callback_data: Nav, session: AsyncSession, bot: Bot) -> None:
    o = await session.get(Order, callback_data.id)
    if not o or o.user_id != cb.from_user.id or o.status != "new":
        return await cb.answer("Заявка уже отправлена или отменена", show_alert=True)
    o.status, o.paid_at = "paid", utcnow()
    await session.commit()
    await show(cb, f"🕒 <b>Заявка #{o.id} отправлена на проверку</b>\n{SEP}\nКак только администратор подтвердит "
                   "оплату, тариф активируется и придёт уведомление.", kb(to_main()))

    user = await session.get(User, o.user_id)
    admin_ids = set(settings.admin_ids) | set((await session.scalars(
        select(User.id).where(User.is_admin.is_(True)))).all())
    sent = []
    for admin_id in admin_ids:
        try:
            m = await bot.send_message(admin_id, order_text(o, user), reply_markup=order_review(o.id))
            sent.append([admin_id, m.message_id])
        except TelegramAPIError:
            pass  # админ не запускал бота
    o.admin_msgs = sent
    await session.commit()
