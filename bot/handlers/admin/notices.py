"""Рассылка админа по пользователям бота (от имени бота, с настоящими инлайн-кнопками)."""
import asyncio
import math

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Notice, NoticeLog
from bot.keyboards import admin as K
from bot.middlewares.admin import IsAdmin
from bot.services.live import NoticeLive
from bot.services.message_builder import parse_buttons
from bot.services.notices import NoticeManager, recipients_stmt, send_notice
from bot.states.forms import NoticeForm
from bot.ui import SEP, Nav, esc, nav, panel, show, start_panel, try_delete
from bot.views import SEGMENTS, notice_card_text

router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())
PAGE = 6
STEPS = 5


def _step(n: int, title: str, hint: str) -> str:
    return (f"📣 <b>Новая рассылка</b> · шаг {n}/{STEPS}\n{'●' * n}{'○' * (STEPS - n)}\n{SEP}\n"
            f"<b>{title}</b>\n{hint}")


async def _list(target, session: AsyncSession, page: int = 0) -> None:
    total = await session.scalar(select(func.count(Notice.id)))
    pages = max(math.ceil(total / PAGE), 1)
    page = min(page, pages - 1)
    items = (await session.scalars(select(Notice).order_by(Notice.id.desc())
                                   .offset(page * PAGE).limit(PAGE))).all()
    head = (f"📣 <b>Рассылка пользователям</b>\n{SEP}\nСообщение уйдёт от имени бота всем выбранным "
            f"пользователям.\nВсего рассылок: <b>{total}</b>")
    await show(target, head, K.notices_list(list(items), page, pages))


async def _card(target, n: Notice, note: str = "") -> None:
    await show(target, notice_card_text(n) + note, K.notice_card(n), track=f"n:{n.id}")


async def _get(session: AsyncSession, nid: int) -> Notice | None:
    return await session.get(Notice, nid)


@router.callback_query(Nav.filter(F.a == "adm_notices"))
async def notices(cb: CallbackQuery, callback_data: Nav, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await _list(cb, session, callback_data.p)


@router.callback_query(Nav.filter(F.a == "adm_notice"))
async def notice_view(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    n = await _get(session, callback_data.id)
    if not n:
        return await cb.answer("Рассылка не найдена", show_alert=True)
    await session.refresh(n)
    await _card(cb, n)


# ---------- управление ----------
@router.callback_query(Nav.filter(F.a.in_({"nt_start", "nt_resume"})))
async def nt_start(cb: CallbackQuery, callback_data: Nav, session: AsyncSession, notices: NoticeManager,
                   notice_live: NoticeLive) -> None:
    n = await _get(session, callback_data.id)
    if not n:
        return await cb.answer("Рассылка не найдена", show_alert=True)
    if notices.is_active(n.id):
        await notices.resume(n.id)
    else:
        if not await session.scalar(select(func.count()).select_from(recipients_stmt(n).subquery())):
            return await cb.answer("Некому отправлять: все получатели уже обработаны.", show_alert=True)
        n.status = "running"
        await session.commit()
        await notices.start(n.id, notice_live)
    await cb.answer("🚀 Запущено")
    await session.refresh(n)
    await _card(cb, n)


@router.callback_query(Nav.filter(F.a.in_({"nt_pause", "nt_stop"})))
async def nt_ctl(cb: CallbackQuery, callback_data: Nav, session: AsyncSession, notices: NoticeManager) -> None:
    n = await _get(session, callback_data.id)
    if not n:
        return await cb.answer("Рассылка не найдена", show_alert=True)
    if callback_data.a == "nt_pause":
        await notices.pause(n.id)
        await cb.answer("⏸ Пауза")
    else:
        await notices.stop(n.id)
        await cb.answer("🛑 Останавливаю…")
        await asyncio.sleep(1.2)
    await session.refresh(n)
    await _card(cb, n)


@router.callback_query(Nav.filter(F.a == "nt_test"))
async def nt_test(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    n = await _get(session, callback_data.id)
    await cb.answer()
    if n:
        await _preview(cb, n.text, n.media, n.buttons)


async def _preview(cb: CallbackQuery, text: str, media: list, buttons: list) -> None:
    try:
        await send_notice(cb.bot, cb.from_user.id, text, media, buttons)
    except TelegramAPIError as e:
        await cb.message.answer(f"❌ Не удалось собрать сообщение: {esc(str(e))}")


@router.callback_query(Nav.filter(F.a == "nt_del"))
async def nt_del(cb: CallbackQuery, callback_data: Nav) -> None:
    from bot.ui import confirm_kb
    await show(cb, "🗑 <b>Удалить рассылку?</b>\nСтатистика и логи тоже будут удалены.",
               confirm_kb(nav("nt_del_ok", callback_data.id), nav("adm_notice", callback_data.id)))


@router.callback_query(Nav.filter(F.a == "nt_del_ok"))
async def nt_del_ok(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    n = await _get(session, callback_data.id)
    if n and n.status != "running":
        await session.execute(NoticeLog.__table__.delete().where(NoticeLog.notice_id == n.id))
        await session.delete(n)
        await session.commit()
        await cb.answer("Удалено")
    else:
        await cb.answer("Остановите рассылку перед удалением", show_alert=True)
    await _list(cb, session)


# ---------- мастер ----------
@router.callback_query(Nav.filter(F.a == "nt_cancel"))
async def nt_cancel(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await cb.answer("Отменено")
    await _list(cb, session)


@router.callback_query(Nav.filter(F.a == "nt_new"))
async def nt_new(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(NoticeForm.name)
    await start_panel(state, cb.message)
    await show(cb, _step(1, "Название", "Как назвать рассылку? Это видите только вы."), K.nt_cancel())


@router.message(NoticeForm.name, F.text)
async def w_name(msg: Message, state: FSMContext) -> None:
    await try_delete(msg)
    await state.update_data(name=msg.text.strip()[:60])
    await state.set_state(NoticeForm.text)
    await panel(msg.bot, state, _step(2, "Текст", "Отправьте текст сообщения. Жирный, курсив, ссылки и цитаты "
                                                   "сохранятся."), K.nt_cancel())


@router.message(NoticeForm.text, F.text)
async def w_text(msg: Message, state: FSMContext) -> None:
    await try_delete(msg)
    await state.update_data(text=msg.html_text, media=[])
    await state.set_state(NoticeForm.media)
    await panel(msg.bot, state, _step(3, "Медиа", "Пришлите одно фото, видео или GIF. Или пропустите шаг."),
                K.nt_media())


@router.message(NoticeForm.media, F.photo | F.video | F.animation)
async def w_media(msg: Message, state: FSMContext) -> None:
    kind = "photo" if msg.photo else "animation" if msg.animation else "video"
    file_id = (msg.photo[-1] if msg.photo else msg.animation or msg.video).file_id
    await try_delete(msg)
    await state.update_data(media=[{"type": kind, "file_id": file_id}])
    await panel(msg.bot, state, _step(3, "Медиа", "✅ Медиа добавлено (заменит предыдущее, если пришлёте другое). "
                                                   "Нажмите «Готово»."), K.nt_media())


@router.callback_query(NoticeForm.media, Nav.filter(F.a == "nt_media_done"))
async def w_media_done(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    if d["media"] and len(d["text"]) > 1000:
        return await cb.answer("Подпись к медиа ограничена 1024 символами. Сократите текст или отмените медиа.",
                               show_alert=True)
    await state.set_state(NoticeForm.buttons)
    await show(cb, _step(4, "Кнопки", "Добавьте URL-кнопки: строка = ряд, кнопки в ряду через <code>|</code>:\n\n"
                                      "<code>Сайт - https://example.com | Канал - https://t.me/channel</code>"),
               K.nt_buttons())


@router.message(NoticeForm.buttons, F.text)
async def w_buttons(msg: Message, state: FSMContext) -> None:
    await try_delete(msg)
    try:
        buttons = parse_buttons(msg.text)
    except ValueError as e:
        return await panel(msg.bot, state, _step(4, "Кнопки", f"❌ {esc(str(e))}\nПопробуйте ещё раз."), K.nt_buttons())
    await state.update_data(buttons=buttons)
    await _ask_segment(msg.bot, state)


@router.callback_query(NoticeForm.buttons, Nav.filter(F.a == "nt_btn_skip"))
async def w_btn_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(buttons=[])
    await _ask_segment(cb.bot, state)


async def _ask_segment(bot, state: FSMContext) -> None:
    await state.set_state(NoticeForm.segment)
    await panel(bot, state, _step(5, "Аудитория", "Кому отправляем? Пользователи, заблокировавшие бота или "
                                                   "заблокированные вами, пропускаются."), K.nt_segments())


@router.callback_query(NoticeForm.segment, Nav.filter(F.a == "nt_seg"))
async def w_seg(cb: CallbackQuery, callback_data: Nav, state: FSMContext, session: AsyncSession) -> None:
    await state.update_data(segment=callback_data.v)
    d = await state.get_data()
    count = await session.scalar(select(func.count()).select_from(
        recipients_stmt(Notice(id=0, segment=d["segment"])).subquery()))
    await state.update_data(total=count)
    await state.set_state(NoticeForm.confirm)
    await cb.message.delete()  # панель уйдёт вниз, под предпросмотр
    await _preview(cb, d["text"], d["media"], d["buttons"])
    m = await cb.message.answer(
        f"📋 <b>Проверьте рассылку</b>\n{SEP}\n📣 {esc(d['name'])}\n"
        f"Аудитория: {SEGMENTS[d['segment']]} · получателей: <b>{count}</b>\n\n"
        "Предпросмотр выше 👆 Создать рассылку?", reply_markup=K.nt_confirm())
    await state.update_data(chat=m.chat.id, panel=m.message_id)


@router.callback_query(NoticeForm.confirm, Nav.filter(F.a == "nt_wtest"))
async def w_test(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    await cb.answer("Отправил предпросмотр")
    await _preview(cb, d["text"], d["media"], d["buttons"])


@router.callback_query(NoticeForm.confirm, Nav.filter(F.a == "nt_create"))
async def w_create(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    d = await state.get_data()
    await state.clear()
    n = Notice(admin_id=cb.from_user.id, name=d["name"], text=d["text"], media=d["media"],
               buttons=d["buttons"], segment=d["segment"], total=d["total"], status="draft")
    session.add(n)
    await session.commit()
    await cb.answer("Создано")
    await _card(cb, n, "\n\n💡 <i>Черновик создан: проверьте и нажмите «Запустить».</i>")
