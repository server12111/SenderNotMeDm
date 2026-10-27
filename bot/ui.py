"""UI-примитивы: единая навигация одним «окном», цветные кнопки, пагинация, живые карточки."""
from datetime import datetime, timezone
from html import escape as esc  # noqa: F401  (реэкспорт для хендлеров)

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

SEP = "━━━━━━━━━━━━━━"
_HAS_STYLE = "style" in InlineKeyboardButton.model_fields  # цветные кнопки: Bot API 9.4+

# (chat_id, message_id) -> ключ карточки: сообщения, где сейчас открыта карточка рассылки и нужен live-прогресс
LIVE: dict[tuple[int, int], str] = {}  # значение: "c:<id>" рассылка клиента, "n:<id>" рассылка админа


class Nav(CallbackData, prefix="n"):
    """Единый формат callback: действие, id объекта, страница/число, строковое значение."""
    a: str
    id: int = 0
    p: int = 0
    v: str = ""


def nav(a: str, id: int = 0, p: int = 0, v: str = "") -> Nav:
    return Nav(a=a, id=id, p=p, v=v)


def btn(text: str, cb: Nav | None = None, *, url: str | None = None, style: str | None = None) -> InlineKeyboardButton:
    """style: 'success' (зелёная) / 'danger' (красная) / 'primary' (синяя); без style — нейтральная."""
    kw: dict = {"text": text}
    if style and _HAS_STYLE:
        kw["style"] = style
    if url:
        kw["url"] = url
    else:
        kw["callback_data"] = cb.pack()
    return InlineKeyboardButton(**kw)


def kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows if r])


def to_main() -> list[InlineKeyboardButton]:
    return [btn("🏠 Главное меню", nav("main"))]


def back(a: str, id: int = 0, p: int = 0, v: str = "", text: str = "◀️ Назад") -> list[InlineKeyboardButton]:
    return [btn(text, nav(a, id, p, v))]


def pager(a: str, page: int, pages: int, **kw) -> list[InlineKeyboardButton]:
    if pages <= 1:
        return []
    row = []
    if page > 0:
        row.append(btn("◀️", nav(a, p=page - 1, **kw)))
    row.append(btn(f"{page + 1} / {pages}", nav("noop")))
    if page < pages - 1:
        row.append(btn("▶️", nav(a, p=page + 1, **kw)))
    return row


def confirm_kb(yes: Nav, no: Nav, yes_text: str = "✅ Да, удалить") -> InlineKeyboardMarkup:
    return kb([btn(yes_text, yes, style="danger"), btn("◀️ Нет", no)])


def bar(done: int, total: int, width: int = 10) -> str:
    pct = done / total if total else 0
    filled = min(width, int(pct * width))
    return f"{'▰' * filled}{'▱' * (width - filled)} {pct * 100:.0f}%"


def aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_dt(dt: datetime | None) -> str:
    return aware(dt).astimezone().strftime("%d.%m.%Y %H:%M") if dt else "—"


async def show(target: CallbackQuery | Message, text: str, markup: InlineKeyboardMarkup | None = None,
               track: str | None = None) -> Message:
    """Показать экран: по callback редактируем текущее сообщение (одно окно), иначе шлём новое."""
    if isinstance(target, CallbackQuery):
        msg = target.message
        try:
            await msg.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as e:
            if "not modified" not in str(e).lower():
                msg = await msg.answer(text, reply_markup=markup)  # старое сообщение не редактируется
    else:
        msg = await target.answer(text, reply_markup=markup)
    key = (msg.chat.id, msg.message_id)
    LIVE.pop(key, None)
    if track is not None:
        LIVE[key] = track
    return msg


async def panel(bot: Bot, state: FSMContext, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Правка «панели» мастера (сообщение бота), пока пользователь вводит данные обычными сообщениями."""
    d = await state.get_data()
    try:
        await bot.edit_message_text(text, chat_id=d["chat"], message_id=d["panel"], reply_markup=markup)
    except TelegramBadRequest as e:
        if "not modified" in str(e).lower():
            return
        m = await bot.send_message(d["chat"], text, reply_markup=markup)
        await state.update_data(panel=m.message_id)


async def start_panel(state: FSMContext, msg: Message) -> None:
    await state.update_data(chat=msg.chat.id, panel=msg.message_id)


async def try_delete(msg: Message) -> None:
    try:
        await msg.delete()
    except Exception:
        pass
