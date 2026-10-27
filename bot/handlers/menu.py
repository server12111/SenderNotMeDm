from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User as TgUser
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Account, Campaign, User
from bot.keyboards.menu import main_menu, profile_kb
from bot.middlewares.admin import check_admin
from bot.services.plans import get_limits
from bot.ui import SEP, Nav, esc, fmt_dt, show
from bot.views import limits_text, sent_today

router = Router()


async def main_screen(session: AsyncSession, tg: TgUser):
    accs = (await session.scalars(select(Account).where(Account.owner_id == tg.id))).all()
    online = [a for a in accs if a.status == "active"]
    running = await session.scalar(select(func.count(Campaign.id)).where(
        Campaign.owner_id == tg.id, Campaign.status.in_(("running", "paused"))))
    lim = await get_limits(session, tg.id)
    used, cap = sum(sent_today(a) for a in online), sum(min(a.daily_limit, lim.daily_limit) for a in online)
    hint = "" if accs else "\n\n💡 <i>Начните с раздела «Аккаунты»: подключите Telegram-аккаунт для рассылки.</i>"
    text = (f"🚀 <b>AutoSender DM</b>\n{SEP}\n👋 Привет, <b>{esc(tg.first_name or 'друг')}</b>!\n\n"
            f"💎 Тариф: <b>{esc(lim.name)}</b>\n👤 Аккаунты онлайн: <b>{len(online)}</b> из {len(accs)}\n"
            f"🚀 Активных рассылок: <b>{running}</b>\n"
            f"✉️ Отправлено сегодня: <b>{used}</b> из {cap}\n{SEP}{hint}\n\n👇 Выберите раздел")
    return text, main_menu(await check_admin(session, tg.id))


@router.message(CommandStart())
@router.message(Command("menu"))
async def start(msg: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    text, markup = await main_screen(session, msg.from_user)
    await show(msg, text, markup)


@router.callback_query(Nav.filter(F.a == "main"))
async def main_cb(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    text, markup = await main_screen(session, cb.from_user)
    await show(cb, text, markup)


@router.callback_query(Nav.filter(F.a == "noop"))
async def noop(cb: CallbackQuery) -> None:
    await cb.answer()


@router.callback_query(Nav.filter(F.a == "profile"))
async def profile(cb: CallbackQuery, session: AsyncSession) -> None:
    uid = cb.from_user.id
    user = await session.get(User, uid)
    accs = await session.scalar(select(func.count(Account.id)).where(Account.owner_id == uid))
    camps, sent = (await session.execute(
        select(func.count(Campaign.id), func.coalesce(func.sum(Campaign.sent), 0))
        .where(Campaign.owner_id == uid))).one()
    uname = f"@{esc(user.username)}" if user.username else "—"
    lim = await get_limits(session, uid)
    until = f" · до {fmt_dt(lim.expires_at)}" if lim.expires_at else (" · навсегда" if not lim.is_free else "")
    await show(cb, (
        f"💎 <b>Профиль</b>\n{SEP}\n🆔 ID: <code>{uid}</code>\n👤 Username: {uname}\n"
        f"📅 В боте с: {fmt_dt(user.created_at)}\n💳 Тариф: <b>{esc(lim.name)}</b>{until}\n"
        f"{limits_text(lim.max_accounts, lim.daily_limit, lim.max_recipients)}\n{SEP}\n"
        f"👤 Аккаунтов: <b>{accs}</b>\n🚀 Рассылок: <b>{camps}</b>\n✅ Всего доставлено: <b>{sent}</b>\n{SEP}\n"
        "<i>Отправляйте сообщения только тем, кто может быть заинтересован, и соблюдайте правила Telegram: "
        "жалобы получателей приводят к ограничению аккаунтов.</i>"), profile_kb())
