"""Тарифы, Free-лимиты и способы оплаты: списки, карточки, создание и правка полей одним мастером."""
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Order, PaymentMethod, Plan, Subscription
from bot.keyboards import admin as K
from bot.middlewares.admin import IsAdmin
from bot.services.plans import FREE_DEFAULTS, free_limits, set_setting
from bot.states.forms import CatalogForm
from bot.ui import SEP, Nav, back, confirm_kb, esc, kb, nav, panel, show, start_panel, try_delete
from bot.views import days_text, fmt_usd, limits_text

router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# ---------- парсеры значений ----------
def p_text(maxlen: int) -> Callable[[str], str]:
    def parse(s: str) -> str:
        s = s.strip()
        if not s:
            raise ValueError("Значение не может быть пустым.")
        if len(s) > maxlen:
            raise ValueError(f"Слишком длинно (максимум {maxlen} символов).")
        return s
    return parse


def p_int(lo: int, hi: int) -> Callable[[str], int]:
    def parse(s: str) -> int:
        try:
            v = int(s.strip())
        except ValueError:
            raise ValueError("Нужно целое число.") from None
        if not lo <= v <= hi:
            raise ValueError(f"Допустимо от {lo} до {hi}.")
        return v
    return parse


def p_decimal(lo: Decimal, strict: bool = False) -> Callable[[str], Decimal]:
    def parse(s: str) -> Decimal:
        try:
            v = Decimal(s.strip().replace(",", "."))
        except InvalidOperation:
            raise ValueError("Нужно число, например 9.99") from None
        if v < lo or (strict and v == lo) or v > Decimal("1000000000"):
            raise ValueError("Недопустимое значение.")
        return v
    return parse


def p_currency(s: str) -> str:
    return p_text(16)(s).upper()


@dataclass
class Field:
    key: str
    label: str
    hint: str
    parse: Callable


SPECS: dict[str, dict] = {
    "plan": {"title": "Новый тариф", "model": Plan, "back": nav("adm_plans"), "fields": [
        Field("name", "Название", "Например: Pro", p_text(64)),
        Field("price", "Цена в $", "Например: 9.99", p_decimal(Decimal(0))),
        Field("duration_days", "Срок в днях", "<code>0</code> — навсегда", p_int(0, 3650)),
        Field("max_accounts", "Максимум аккаунтов", "Сколько Telegram-аккаунтов можно подключить", p_int(1, 1000)),
        Field("daily_limit", "Дневной лимит на аккаунт", "Потолок сообщений в сутки с одного аккаунта",
              p_int(1, 5000)),
        Field("max_recipients", "Получателей в рассылке", "<code>0</code> — без лимита", p_int(0, 10_000_000)),
    ]},
    "method": {"title": "Новый способ оплаты", "model": PaymentMethod, "back": nav("adm_methods"), "fields": [
        Field("title", "Название", "Например: USDT TRC20 или Карта", p_text(64)),
        Field("currency", "Валюта", "Например: USDT, RUB, BTC", p_currency),
        Field("rate", "Курс", "Сколько единиц валюты за <b>$1</b>. Для USD/USDT — <code>1</code>",
              p_decimal(Decimal(0), strict=True)),
        Field("details", "Реквизиты", "Адрес кошелька или номер карты: это увидит клиент при оплате", p_text(500)),
    ]},
    "free": {"title": "Free-тариф", "model": None, "back": nav("adm_free"), "fields": [
        Field("free_max_accounts", "Максимум аккаунтов", "Для пользователей без тарифа", p_int(0, 1000)),
        Field("free_daily_limit", "Дневной лимит на аккаунт", "Сообщений в сутки с одного аккаунта", p_int(1, 5000)),
        Field("free_max_recipients", "Получателей в рассылке", "<code>0</code> — без лимита",
              p_int(0, 10_000_000)),
    ]},
}


def _field(kind: str, key: str) -> Field:
    return next(f for f in SPECS[kind]["fields"] if f.key == key)


# ---------- карточки ----------
async def plan_view(session: AsyncSession, pid: int, note: str = ""):
    p = await session.get(Plan, pid)
    if not p:
        return "Тариф не найден.", kb(back("adm_plans"))
    buyers = await session.scalar(select(func.count(Subscription.id)).where(Subscription.plan_id == p.id))
    return (f"💎 <b>{esc(p.name)}</b> {'' if p.is_active else '· ⚫ скрыт'}\n{SEP}\n"
            f"💵 Цена: <b>{fmt_usd(p.price)}</b>\n📅 Срок: <b>{days_text(p.duration_days)}</b>\n"
            f"{limits_text(p.max_accounts, p.daily_limit, p.max_recipients)}\n{SEP}\n"
            f"🛒 Выдано подписок: {buyers}{note}"), K.plan_card(p)


async def free_view(session: AsyncSession, note: str = ""):
    f = await free_limits(session)
    return (f"🆓 <b>Free-тариф</b>\n{SEP}\nЛимиты для пользователей без оплаченного тарифа:\n\n"
            f"{limits_text(f.max_accounts, f.daily_limit, f.max_recipients)}{note}"), K.free_card()


async def method_view(session: AsyncSession, mid: int, note: str = ""):
    m = await session.get(PaymentMethod, mid)
    if not m:
        return "Способ не найден.", kb(back("adm_methods"))
    return (f"💰 <b>{esc(m.title)}</b> {'🟢' if m.is_enabled else '⚫ выключен'}\n{SEP}\n"
            f"Валюта: <b>{esc(m.currency)}</b>\nКурс: <b>{m.rate:.6g}</b> {esc(m.currency)} за $1\n"
            f"Реквизиты:\n<code>{esc(m.details)}</code>\n{SEP}\n"
            f"<i>Клиент увидит сумму в {esc(m.currency)} и эти реквизиты.</i>{note}"), K.method_card(m)


async def _view(kind: str, session: AsyncSession, obj_id: int, note: str = ""):
    if kind == "plan":
        return await plan_view(session, obj_id, note)
    if kind == "method":
        return await method_view(session, obj_id, note)
    return await free_view(session, note)


# ---------- списки ----------
@router.callback_query(Nav.filter(F.a == "adm_plans"))
async def plans(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    items = (await session.scalars(select(Plan).order_by(Plan.sort, Plan.id))).all()
    await show(cb, f"💳 <b>Тарифы</b>\n{SEP}\n" + (
        "Нажмите на тариф, чтобы изменить его. ⚫ — скрыт из продажи." if items else
        "Платных тарифов пока нет: создайте первый. Пока их нет, все работают по Free."),
        K.plans_list(list(items)))


@router.callback_query(Nav.filter(F.a == "adm_methods"))
async def methods(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    items = (await session.scalars(select(PaymentMethod).order_by(PaymentMethod.sort, PaymentMethod.id))).all()
    on = sum(m.is_enabled for m in items)
    await show(cb, f"💰 <b>Реквизиты для оплаты</b>\n{SEP}\nВключено способов: <b>{on}/{len(items)}</b>\n\n"
                   "Клиент выбирает способ при покупке, переводит сумму и нажимает «Я оплатил»; заявка приходит "
                   "вам на проверку.", K.methods_list(list(items)))


@router.callback_query(Nav.filter(F.a == "adm_plan"))
async def plan_open(cb: CallbackQuery, callback_data: Nav, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await show(cb, *await plan_view(session, callback_data.id))


@router.callback_query(Nav.filter(F.a == "adm_free"))
async def free_open(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await show(cb, *await free_view(session))


@router.callback_query(Nav.filter(F.a == "adm_method"))
async def method_open(cb: CallbackQuery, callback_data: Nav, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await show(cb, *await method_view(session, callback_data.id))


# ---------- переключатели и удаление ----------
@router.callback_query(Nav.filter(F.a == "adm_plan_toggle"))
async def plan_toggle(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    p = await session.get(Plan, callback_data.id)
    if p:
        p.is_active = not p.is_active
        await session.commit()
    await cb.answer("Готово")
    await show(cb, *await plan_view(session, callback_data.id))


@router.callback_query(Nav.filter(F.a == "adm_method_toggle"))
async def method_toggle(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    m = await session.get(PaymentMethod, callback_data.id)
    if m:
        m.is_enabled = not m.is_enabled
        await session.commit()
    await cb.answer("Готово")
    await show(cb, *await method_view(session, callback_data.id))


@router.callback_query(Nav.filter(F.a.in_({"adm_plan_del", "adm_method_del"})))
async def delete_ask(cb: CallbackQuery, callback_data: Nav) -> None:
    kind = "plan" if callback_data.a == "adm_plan_del" else "method"
    await show(cb, f"🗑 <b>Удалить {'тариф' if kind == 'plan' else 'способ оплаты'}?</b>\nУже выданные подписки и "
                   "заявки сохранятся. Если на него есть заявки, он будет скрыт вместо удаления.",
               confirm_kb(nav(f"{callback_data.a}_ok", callback_data.id), nav(f"adm_{kind}", callback_data.id)))


@router.callback_query(Nav.filter(F.a == "adm_plan_del_ok"))
async def plan_del(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    p = await session.get(Plan, callback_data.id)
    if p:
        used = await session.scalar(select(func.count(Order.id)).where(Order.plan_id == p.id)) or \
            await session.scalar(select(func.count(Subscription.id)).where(Subscription.plan_id == p.id))
        if used:
            p.is_active = False
            await cb.answer("Есть выдачи: тариф скрыт из продажи", show_alert=True)
        else:
            await session.delete(p)
            await cb.answer("Тариф удалён")
        await session.commit()
    await _back_list(cb, "plan", session)


@router.callback_query(Nav.filter(F.a == "adm_method_del_ok"))
async def method_del(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    m = await session.get(PaymentMethod, callback_data.id)
    if m:
        if await session.scalar(select(func.count(Order.id)).where(Order.method_id == m.id)):
            m.is_enabled = False
            await cb.answer("Есть заявки: способ выключен", show_alert=True)
        else:
            await session.delete(m)
            await cb.answer("Способ удалён")
        await session.commit()
    await _back_list(cb, "method", session)


async def _back_list(cb: CallbackQuery, kind: str, session: AsyncSession) -> None:
    if kind == "plan":
        items = (await session.scalars(select(Plan).order_by(Plan.sort, Plan.id))).all()
        await show(cb, f"💳 <b>Тарифы</b>\n{SEP}\nНажмите на тариф, чтобы изменить его.", K.plans_list(list(items)))
    else:
        items = (await session.scalars(select(PaymentMethod).order_by(PaymentMethod.sort, PaymentMethod.id))).all()
        await show(cb, f"💰 <b>Реквизиты для оплаты</b>\n{SEP}", K.methods_list(list(items)))


# ---------- мастер полей ----------
def _prompt(kind: str, d: dict) -> str:
    spec = SPECS[kind]
    fields = spec["fields"]
    if d["mode"] == "create":
        i = d["i"]
        f = fields[i]
        done = "".join(f"✔️ {_field(kind, k).label}: <b>{esc(str(v))}</b>\n" for k, v in d["vals"].items())
        return (f"➕ <b>{spec['title']}</b> · шаг {i + 1}/{len(fields)}\n{SEP}\n{done}"
                f"\n<b>{f.label}</b>\n{f.hint}")
    f = _field(kind, d["key"])
    return f"✏️ <b>{f.label}</b>\n{SEP}\nТекущее значение: <b>{esc(str(d['current']))}</b>\n\n{f.hint}\nОтправьте новое значение:"


def _cancel_target(kind: str, d: dict):
    if d["mode"] == "edit" and kind != "free":
        return nav(f"adm_{kind}", d["id"])
    return SPECS[kind]["back"]


async def _begin(cb: CallbackQuery, state: FSMContext, kind: str, mode: str, **extra) -> None:
    await state.clear()
    await state.set_state(CatalogForm.input)
    await start_panel(state, cb.message)
    await state.update_data(kind=kind, mode=mode, i=0, vals={}, **extra)
    d = await state.get_data()
    await show(cb, _prompt(kind, d), K.form_cancel(_cancel_target(kind, d)))


@router.callback_query(Nav.filter(F.a.in_({"adm_plan_new", "adm_method_new"})))
async def create_start(cb: CallbackQuery, callback_data: Nav, state: FSMContext) -> None:
    await _begin(cb, state, "plan" if callback_data.a == "adm_plan_new" else "method", "create")


@router.callback_query(Nav.filter(F.a == "adm_edit"))
async def edit_start(cb: CallbackQuery, callback_data: Nav, state: FSMContext, session: AsyncSession) -> None:
    kind, key = callback_data.v.split(".", 1)
    if kind == "free":
        current = getattr(await free_limits(session), {"free_max_accounts": "max_accounts",
                                                       "free_daily_limit": "daily_limit",
                                                       "free_max_recipients": "max_recipients"}[key])
    else:
        obj = await session.get(SPECS[kind]["model"], callback_data.id)
        if not obj:
            return await cb.answer("Не найдено", show_alert=True)
        current = getattr(obj, key)
    await _begin(cb, state, kind, "edit", id=callback_data.id, key=key, current=current)


@router.message(CatalogForm.input, F.text)
async def got_value(msg: Message, state: FSMContext, session: AsyncSession) -> None:
    await try_delete(msg)
    d = await state.get_data()
    kind = d["kind"]
    field = SPECS[kind]["fields"][d["i"]] if d["mode"] == "create" else _field(kind, d["key"])
    try:
        value = field.parse(msg.text)
    except ValueError as e:
        return await panel(msg.bot, state, f"{_prompt(kind, d)}\n\n❌ {esc(str(e))}",
                           K.form_cancel(_cancel_target(kind, d)))

    if d["mode"] == "create":
        vals = {**d["vals"], field.key: value}
        if d["i"] + 1 < len(SPECS[kind]["fields"]):
            await state.update_data(vals=vals, i=d["i"] + 1)
            d = await state.get_data()
            return await panel(msg.bot, state, _prompt(kind, d), K.form_cancel(_cancel_target(kind, d)))
        obj = SPECS[kind]["model"](**vals)
        session.add(obj)
        await session.commit()
        obj_id, note = obj.id, "\n\n✅ <b>Создано</b>"
    else:
        if kind == "free":
            await set_setting(session, field.key, str(value))
            obj_id = 0
        else:
            obj = await session.get(SPECS[kind]["model"], d["id"])
            setattr(obj, field.key, value)
            await session.commit()
            obj_id = obj.id
        note = "\n\n✅ <b>Сохранено</b>"
    text, markup = await _view(kind, session, obj_id, note)
    await panel(msg.bot, state, text, markup)
    await state.clear()


assert set(FREE_DEFAULTS) == {f.key for f in SPECS["free"]["fields"]}
