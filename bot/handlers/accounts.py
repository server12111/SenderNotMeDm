import math
import re

from aiogram import Bot, F, Router

from bot.config import settings
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import errors

from bot.database.models import Account
from bot.keyboards.accounts import account_card, accounts_list, code_pad, login_cancel
from bot.services.accounts import LoginManager
from bot.services.plans import get_limits, limit_presets
from bot.states.forms import AccountForm
from bot.ui import SEP, Nav, btn, confirm_kb, kb, nav, panel, show, start_panel, try_delete
from bot.views import account_card_text

router = Router()
PAGE = 6


async def _owned(session: AsyncSession, acc_id: int, uid: int) -> Account | None:
    return await session.scalar(select(Account).where(Account.id == acc_id, Account.owner_id == uid))


async def _list(target, session: AsyncSession, uid: int, page: int = 0) -> None:
    total = await session.scalar(select(func.count(Account.id)).where(Account.owner_id == uid))
    pages = max(math.ceil(total / PAGE), 1)
    page = min(page, pages - 1)
    accs = (await session.scalars(select(Account).where(Account.owner_id == uid).order_by(Account.id)
                                  .offset(page * PAGE).limit(PAGE))).all()
    online = sum(a.status == "active" for a in accs)
    head = (f"👤 <b>Аккаунты</b>\n{SEP}\n" +
            (f"Подключено: <b>{total}</b> · в работе на этой странице: <b>{online}</b>\n"
             "Число справа — отправлено сегодня / дневной лимит.\n\n👇 Откройте аккаунт для настроек"
             if total else "Аккаунтов пока нет.\n\nПодключите свой Telegram-аккаунт: рассылка пойдёт с него."))
    await show(target, head, accounts_list(list(accs), page, pages))


@router.callback_query(Nav.filter(F.a == "accs"))
async def accs(cb: CallbackQuery, callback_data: Nav, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await _list(cb, session, cb.from_user.id, callback_data.p)


async def _card(target, session: AsyncSession, acc: Account, note: str = "") -> None:
    cap = (await get_limits(session, acc.owner_id)).daily_limit
    await show(target, account_card_text(acc) + note, account_card(acc, cap))


@router.callback_query(Nav.filter(F.a == "acc"))
async def acc_view(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    acc = await _owned(session, callback_data.id, cb.from_user.id)
    if not acc:
        return await cb.answer("Аккаунт не найден", show_alert=True)
    await _card(cb, session, acc)


@router.callback_query(Nav.filter(F.a.in_({"acc_on", "acc_off", "acc_lim"})))
async def acc_edit(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    acc = await _owned(session, callback_data.id, cb.from_user.id)
    if not acc:
        return await cb.answer("Аккаунт не найден", show_alert=True)
    if callback_data.a == "acc_on":
        acc.status, acc.cooldown_until = "active", None
    elif callback_data.a == "acc_off":
        acc.status = "off"
    elif callback_data.p in limit_presets((await get_limits(session, acc.owner_id)).daily_limit):
        acc.daily_limit = callback_data.p
    await session.commit()
    await cb.answer("Сохранено")
    await _card(cb, session, acc)


@router.callback_query(Nav.filter(F.a == "acc_del"))
async def acc_del(cb: CallbackQuery, callback_data: Nav) -> None:
    await show(cb, "🗑 <b>Удалить аккаунт?</b>\nСессия будет стёрта из бота. Это не выход из аккаунта в Telegram.",
               confirm_kb(nav("acc_del_ok", callback_data.id), nav("acc", callback_data.id)))


@router.callback_query(Nav.filter(F.a == "acc_del_ok"))
async def acc_del_ok(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    res = await session.execute(delete(Account).where(Account.id == callback_data.id,
                                                      Account.owner_id == cb.from_user.id))
    await session.commit()
    await cb.answer("Аккаунт удалён" if res.rowcount else "Аккаунт не найден")
    await _list(cb, session, cb.from_user.id)


# ---------- подключение ----------
@router.callback_query(Nav.filter(F.a == "acc_add"))
async def acc_add(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    if not settings.api_id or not settings.api_hash:
        return await cb.answer("Подключение аккаунтов пока не настроено администратором.", show_alert=True)
    lim = await get_limits(session, cb.from_user.id)
    have = await session.scalar(select(func.count(Account.id)).where(Account.owner_id == cb.from_user.id))
    if have >= lim.max_accounts:
        return await show(cb, f"🔒 <b>Лимит аккаунтов</b>\n{SEP}\nНа тарифе <b>{lim.name}</b> можно подключить "
                              f"до <b>{lim.max_accounts}</b>. Подключено: {have}.\n\nПерейдите на тариф выше, "
                              "чтобы добавить больше.", kb([btn("💳 Тарифы", nav("plans"), style="success")],
                                                           [btn("◀️ Назад", nav("accs"))]))
    await state.set_state(AccountForm.phone)
    await start_panel(state, cb.message)
    await show(cb, f"➕ <b>Подключение аккаунта</b>\n{SEP}\nОтправьте номер в международном формате, "
                   "например <code>+79001234567</code>.\n\n🔐 Сессия хранится в зашифрованном виде. "
                   "Подключайте только свои аккаунты.", login_cancel())


@router.callback_query(Nav.filter(F.a == "acc_re"))
async def acc_reconnect(cb: CallbackQuery, callback_data: Nav, state: FSMContext, session: AsyncSession,
                        logins: LoginManager) -> None:
    acc = await _owned(session, callback_data.id, cb.from_user.id)
    if not acc:
        return await cb.answer("Аккаунт не найден", show_alert=True)
    await state.clear()
    await start_panel(state, cb.message)
    await _send_code(cb.bot, state, logins, cb.from_user.id, acc.phone)


@router.callback_query(Nav.filter(F.a == "acc_cancel"))
async def acc_cancel(cb: CallbackQuery, state: FSMContext, session: AsyncSession, logins: LoginManager) -> None:
    await logins.cancel(cb.from_user.id)
    await state.clear()
    await cb.answer("Отменено")
    await _list(cb, session, cb.from_user.id)


async def _send_code(bot: Bot, state: FSMContext, logins: LoginManager, uid: int, phone: str) -> None:
    try:
        await logins.start(uid, phone)
    except errors.PhoneNumberInvalidError:
        await state.set_state(AccountForm.phone)
        return await panel(bot, state, "❌ Неверный номер. Отправьте ещё раз, например <code>+79001234567</code>.",
                           login_cancel())
    except errors.FloodWaitError as e:
        await panel(bot, state, f"⏳ Слишком много попыток. Подождите {e.seconds} сек.", login_cancel())
        return await state.clear()
    except Exception as e:  # сеть, неверные API_ID/API_HASH и т.п.
        await panel(bot, state, f"❌ Не удалось запросить код: <code>{type(e).__name__}</code>", login_cancel())
        return await state.clear()
    await state.set_state(AccountForm.code)
    await state.update_data(code="", phone=phone)
    await panel(bot, state, _code_text(phone, ""), code_pad())


def _code_text(phone: str, code: str, error: str = "") -> str:
    dots = " ".join(list(code) + ["●"] * max(5 - len(code), 0))
    return (f"🔑 <b>Код подтверждения</b>\n{SEP}\nКод отправлен в Telegram на <code>{phone}</code>.\n"
            f"Введите его кнопками ниже:\n\n<b>{dots}</b>{error}")


@router.message(AccountForm.phone, F.text)
async def got_phone(msg: Message, state: FSMContext, logins: LoginManager) -> None:
    await try_delete(msg)
    digits = re.sub(r"\D", "", msg.text)
    if len(digits) < 8:
        return await panel(msg.bot, state, "❌ Похоже на неверный номер. Попробуйте ещё раз.", login_cancel())
    await _send_code(msg.bot, state, logins, msg.from_user.id, "+" + digits)


@router.callback_query(AccountForm.code, Nav.filter(F.a == "pad"))
async def pad(cb: CallbackQuery, callback_data: Nav, state: FSMContext, logins: LoginManager,
              session: AsyncSession) -> None:
    d = await state.get_data()
    code, phone, key = d["code"], d["phone"], callback_data.v
    if key == "del":
        code = code[:-1]
    elif key == "ok":
        if len(code) < 5:
            return await cb.answer("Введите все цифры кода", show_alert=True)
        return await _submit_code(cb, state, logins, code, session)
    elif len(code) < 6:
        code += key
    await state.update_data(code=code)
    await cb.answer()
    await panel(cb.bot, state, _code_text(phone, code), code_pad())


async def _submit_code(cb: CallbackQuery, state: FSMContext, logins: LoginManager, code: str,
                       session: AsyncSession) -> None:
    phone = (await state.get_data())["phone"]
    try:
        acc = await logins.submit_code(cb.from_user.id, code)
    except (errors.PhoneCodeInvalidError, errors.PhoneCodeEmptyError):
        await state.update_data(code="")
        return await panel(cb.bot, state, _code_text(phone, "", "\n\n❌ Неверный код, попробуйте ещё раз."), code_pad())
    except errors.PhoneCodeExpiredError:
        await logins.cancel(cb.from_user.id)
        await state.clear()
        return await show(cb, "⌛ Код истёк. Начните подключение заново.", login_cancel())
    except KeyError:  # бот перезапускался, ожидающий вход потерян
        await state.clear()
        return await show(cb, "⚠️ Сессия входа потеряна. Начните подключение заново.", login_cancel())
    except errors.RPCError as e:
        await state.clear()
        return await show(cb, f"❌ Ошибка Telegram: <code>{type(e).__name__}</code>", login_cancel())
    if acc is None:
        await state.set_state(AccountForm.password)
        return await show(cb, f"🔐 <b>Двухфакторная защита</b>\n{SEP}\nВведите облачный пароль аккаунта. "
                              "Сообщение с паролем бот сразу удалит.", login_cancel())
    await state.clear()
    await _card(cb, session, acc, "\n\n✅ <b>Аккаунт подключён!</b>")


@router.message(AccountForm.password, F.text)
async def got_password(msg: Message, state: FSMContext, logins: LoginManager, session: AsyncSession) -> None:
    await try_delete(msg)
    try:
        acc = await logins.submit_password(msg.from_user.id, msg.text)
    except errors.PasswordHashInvalidError:
        return await panel(msg.bot, state, f"🔐 <b>Двухфакторная защита</b>\n{SEP}\n❌ Неверный пароль, ещё раз:",
                           login_cancel())
    except (KeyError, errors.RPCError):
        await panel(msg.bot, state, "⚠️ Сессия входа потеряна. Начните подключение заново.", login_cancel())
        return await state.clear()
    cap = (await get_limits(session, acc.owner_id)).daily_limit
    await panel(msg.bot, state, account_card_text(acc) + "\n\n✅ <b>Аккаунт подключён!</b>",
                account_card(acc, cap))
    await state.clear()
