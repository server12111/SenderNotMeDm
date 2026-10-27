import asyncio
import math
import os
import sqlite3
import tempfile
from datetime import timedelta

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.database.models import Account, Campaign, Order, Plan, Subscription, User
from bot.keyboards import admin as K
from bot.middlewares.admin import IsAdmin
from bot.services.plans import active_subscription, grant_subscription, revoke_subscriptions, utcnow
from bot.states.forms import AdminForm, UserMsgForm
from bot.ui import SEP, Nav, btn, esc, fmt_dt, kb, nav, panel, show, start_panel, try_delete
from bot.views import days_text, fmt_usd

router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())
PAGE = 8


async def pending_orders(session: AsyncSession) -> int:
    return await session.scalar(select(func.count(Order.id)).where(Order.status == "paid"))


async def _main_screen(session: AsyncSession):
    users, active, banned = (await session.execute(select(
        func.count(User.id), func.count().filter(User.is_active.is_(True)),
        func.count().filter(User.is_banned.is_(True))))).one()
    paid = await session.scalar(select(func.count(func.distinct(Subscription.user_id))).where(
        Subscription.status == "active",
        (Subscription.expires_at.is_(None)) | (Subscription.expires_at > utcnow())))
    pending = await pending_orders(session)
    text = (f"⚙️ <b>Админ-панель</b>\n{SEP}\n👥 Пользователей: <b>{users}</b> "
            f"(активных {active}, заблокированных {banned})\n💎 С тарифом: <b>{paid}</b>\n"
            f"🧾 Заявок на проверке: <b>{pending}</b>\n\n👇 Выберите раздел")
    return text, K.admin_main(pending)


@router.message(Command("admin"))
async def admin_cmd(msg: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await show(msg, *await _main_screen(session))


@router.callback_query(Nav.filter(F.a == "adm"))
async def admin_cb(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await show(cb, *await _main_screen(session))


# ---------- статистика ----------
@router.callback_query(Nav.filter(F.a == "adm_stats"))
async def stats(cb: CallbackQuery, session: AsyncSession) -> None:
    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    week = today - timedelta(days=7)
    users = await session.scalar(select(func.count(User.id)))
    new_today = await session.scalar(select(func.count(User.id)).where(User.created_at >= today))
    new_week = await session.scalar(select(func.count(User.id)).where(User.created_at >= week))
    accs, accs_ok = (await session.execute(select(
        func.count(Account.id), func.count().filter(Account.status == "active")))).one()
    sent, blocked, failed, camps = (await session.execute(select(
        func.coalesce(func.sum(Campaign.sent), 0), func.coalesce(func.sum(Campaign.blocked), 0),
        func.coalesce(func.sum(Campaign.failed), 0), func.count(Campaign.id)))).one()
    revenue, orders_ok = (await session.execute(select(
        func.coalesce(func.sum(Order.price_usd), 0), func.count(Order.id)).where(Order.status == "confirmed"))).one()
    rev_week = await session.scalar(select(func.coalesce(func.sum(Order.price_usd), 0)).where(
        Order.status == "confirmed", Order.decided_at >= week))
    plans = (await session.execute(select(Subscription.plan_name, func.count()).where(
        Subscription.status == "active").group_by(Subscription.plan_name).order_by(func.count().desc()))).all()
    lines = [f"📊 <b>Статистика</b>\n{SEP}",
             f"👥 Пользователей: <b>{users}</b> · сегодня +{new_today} · за 7 дней +{new_week}",
             f"👤 Аккаунтов: <b>{accs}</b> (рабочих {accs_ok})",
             f"🚀 Рассылок клиентов: <b>{camps}</b> · доставлено <b>{sent}</b> · "
             f"недоступны {blocked} · ошибки {failed}", SEP,
             f"💰 Выручка: <b>{fmt_usd(revenue)}</b> ({orders_ok} оплат) · за 7 дней {fmt_usd(rev_week)}"]
    if plans:
        lines += ["", "<b>Активные подписки</b>"] + [f"• {esc(n)}: {c}" for n, c in plans]
    await show(cb, "\n".join(lines), K.refresh("adm_stats"))


# ---------- пользователи ----------
@router.callback_query(Nav.filter(F.a == "adm_users"))
async def users(cb: CallbackQuery, callback_data: Nav, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    total = await session.scalar(select(func.count(User.id)))
    pages = max(math.ceil(total / PAGE), 1)
    page = min(callback_data.p, pages - 1)
    rows = (await session.scalars(select(User).order_by(User.created_at.desc())
                                  .offset(page * PAGE).limit(PAGE))).all()
    await show(cb, f"👥 <b>Пользователи</b> · {total}\n{SEP}\n👤 обычный · 👑 админ · 🚫 заблокирован",
               K.users_list(list(rows), page, pages))


async def _user_card(target, session: AsyncSession, uid: int, note: str = "") -> None:
    u = await session.get(User, uid)
    if not u:
        return await show(target, "Пользователь не найден.", K.find_cancel())
    accs = await session.scalar(select(func.count(Account.id)).where(Account.owner_id == uid))
    camps, sent = (await session.execute(select(func.count(Campaign.id), func.coalesce(func.sum(Campaign.sent), 0))
                                         .where(Campaign.owner_id == uid))).one()
    spent = await session.scalar(select(func.coalesce(func.sum(Order.price_usd), 0)).where(
        Order.user_id == uid, Order.status == "confirmed"))
    sub = await active_subscription(session, uid)
    plan = (f"💎 <b>{esc(sub.plan_name)}</b> · до {fmt_dt(sub.expires_at) if sub.expires_at else 'навсегда'}"
            if sub else "🆓 Free")
    flags = " ".join(f for f, on in (("👑 админ", u.is_admin), ("🚫 заблокирован", u.is_banned),
                                     ("📴 заблокировал бота", not u.is_active)) if on) or "—"
    await show(target, f"👤 <b>{esc(u.first_name or '—')}</b> {'@' + esc(u.username) if u.username else ''}\n{SEP}\n"
                       f"🆔 <code>{u.id}</code>\n📅 Регистрация: {fmt_dt(u.created_at)}\n"
                       f"👀 Активность: {fmt_dt(u.last_seen)}\nТариф: {plan}\nФлаги: {flags}\n{SEP}\n"
                       f"👤 Аккаунтов: {accs} · 🚀 Рассылок: {camps} · ✅ Доставлено: {sent}\n"
                       f"💰 Оплачено: {fmt_usd(spent)}{note}", K.user_card(u, bool(sub)), )


@router.callback_query(Nav.filter(F.a == "adm_user"))
async def user_view(cb: CallbackQuery, callback_data: Nav, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await _user_card(cb, session, callback_data.id)


@router.callback_query(Nav.filter(F.a.in_({"adm_ban", "adm_unban", "adm_op"})))
async def user_edit(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    u = await session.get(User, callback_data.id)
    if not u:
        return await cb.answer("Не найден", show_alert=True)
    if u.id in settings.admin_ids and callback_data.a != "adm_unban":
        return await cb.answer("Админов из .env менять нельзя", show_alert=True)
    if callback_data.a == "adm_op":
        u.is_admin = not u.is_admin
    else:
        u.is_banned = callback_data.a == "adm_ban"
    await session.commit()
    await cb.answer("Готово")
    await _user_card(cb, session, u.id)


# ---------- выдача / снятие тарифа ----------
@router.callback_query(Nav.filter(F.a == "adm_grant"))
async def grant(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    plans = (await session.scalars(select(Plan).order_by(Plan.sort, Plan.id))).all()
    if not plans:
        return await cb.answer("Сначала создайте тариф в разделе «Тарифы».", show_alert=True)
    await show(cb, f"🎁 <b>Выдать тариф</b>\n{SEP}\nВыберите тариф. Срок считается от текущего, если у пользователя "
                   "уже есть действующая подписка.", K.grant_plans(callback_data.id, list(plans)))


@router.callback_query(Nav.filter(F.a == "adm_grant_do"))
async def grant_do(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    plan = await session.get(Plan, callback_data.p)
    user = await session.get(User, callback_data.id)
    if not plan or not user:
        return await cb.answer("Не найдено", show_alert=True)
    sub = await grant_subscription(
        session, user.id, plan_name=plan.name, days=plan.duration_days, max_accounts=plan.max_accounts,
        daily_limit=plan.daily_limit, max_recipients=plan.max_recipients, plan_id=plan.id,
        source="admin", granted_by=cb.from_user.id)
    try:
        await cb.bot.send_message(user.id, f"🎁 Вам выдан тариф <b>{esc(plan.name)}</b> "
                                           f"({days_text(plan.duration_days)}). Приятной работы!")
    except TelegramAPIError:
        pass
    await cb.answer("Тариф выдан")
    await _user_card(cb, session, user.id, f"\n\n✅ Выдан тариф «{esc(sub.plan_name)}»")


@router.callback_query(Nav.filter(F.a == "adm_revoke"))
async def revoke(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    n = await revoke_subscriptions(session, callback_data.id)
    await cb.answer(f"Снято подписок: {n}")
    await _user_card(cb, session, callback_data.id)


# ---------- личное сообщение ----------
@router.callback_query(Nav.filter(F.a == "adm_msg"))
async def msg_start(cb: CallbackQuery, callback_data: Nav, state: FSMContext) -> None:
    await state.set_state(UserMsgForm.text)
    await state.update_data(uid=callback_data.id)
    await start_panel(state, cb.message)
    await show(cb, f"✉️ <b>Сообщение пользователю</b>\n{SEP}\nОтправьте текст: он уйдёт от имени бота. "
                   "Форматирование сохранится.", kb([btn("✖️ Отмена", nav("adm_user", callback_data.id))]))


@router.message(UserMsgForm.text, F.text)
async def msg_send(msg: Message, state: FSMContext, session: AsyncSession) -> None:
    uid = (await state.get_data())["uid"]
    await try_delete(msg)
    try:
        await msg.bot.send_message(uid, msg.html_text)
        note = "\n\n✅ Сообщение отправлено"
    except TelegramAPIError as e:
        note = f"\n\n❌ Не доставлено: {esc(str(e))}"
    await _user_card(msg, session, uid, note)
    d = await state.get_data()
    await try_delete_panel(msg, d)
    await state.clear()


async def try_delete_panel(msg: Message, d: dict) -> None:
    try:
        await msg.bot.delete_message(d["chat"], d["panel"])
    except TelegramAPIError:
        pass


# ---------- поиск ----------
@router.callback_query(Nav.filter(F.a == "adm_find"))
async def find(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminForm.search)
    await start_panel(state, cb.message)
    await show(cb, f"🔎 <b>Поиск</b>\n{SEP}\nОтправьте ID или @username пользователя.", K.find_cancel())


@router.message(AdminForm.search, F.text)
async def find_do(msg: Message, state: FSMContext, session: AsyncSession) -> None:
    await try_delete(msg)
    q = msg.text.strip().lstrip("@")
    u = await session.scalar(select(User).where(or_(User.id == int(q) if q.isdigit() else False,
                                                    func.lower(User.username) == q.lower())))
    if not u:
        return await panel(msg.bot, state, f"🔎 Ничего не найдено по «{esc(q)}». Ещё раз:", K.find_cancel())
    d = await state.get_data()
    await state.clear()
    await try_delete_panel(msg, d)
    await _user_card(msg, session, u.id)


# ---------- бэкап БД ----------
MAX_BACKUP = 49 * 1024 * 1024  # лимит Bot API на файл


def _sqlite_snapshot() -> bytes:
    """Согласованная копия живой БД (sqlite backup API, безопасно при записи)."""
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        src, dst = sqlite3.connect(settings.sqlite_path), sqlite3.connect(tmp)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        os.unlink(tmp)


@router.callback_query(Nav.filter(F.a == "adm_backup"))
async def backup(cb: CallbackQuery) -> None:
    if not settings.is_sqlite:
        return await cb.answer("Используется PostgreSQL: делайте бэкап через pg_dump.", show_alert=True)
    await cb.answer("Готовлю копию…")
    data = await asyncio.to_thread(_sqlite_snapshot)
    if len(data) > MAX_BACKUP:
        return await cb.message.answer(f"⚠️ БД весит {len(data) // 1024 // 1024} МБ: больше лимита Telegram (50 МБ). "
                                       "Скачайте файл bot.db из папки данных на сервере.")
    await cb.message.answer_document(
        BufferedInputFile(data, filename=f"bot-{utcnow():%Y%m%d-%H%M}.db"),
        caption="💾 Копия БД. Сессии аккаунтов зашифрованы, но файл всё равно конфиденциален. "
                "Для восстановления положите его в папку данных как bot.db.")
