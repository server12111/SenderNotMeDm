import asyncio
import math
from datetime import date

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Account, BroadcastLog, Campaign, ParsedLead
from bot.keyboards import campaigns as K
from bot.services.broadcast import BroadcastManager, recipients_stmt, usable_accounts_stmt
from bot.services.live import LiveUpdater
from bot.services.message_builder import parse_buttons, preview
from bot.services.plans import get_limits
from bot.states.forms import MailingForm
from bot.ui import SEP, Nav, confirm_kb, esc, nav, panel, show, start_panel, try_delete
from bot.views import campaign_card_text

router = Router()
PAGE = 6
LOG_PAGE = 8
MAX_MEDIA = 10
STEPS = 6


# ---------- список и карточка ----------
async def _owned(session: AsyncSession, cid: int, uid: int) -> Campaign | None:
    return await session.scalar(select(Campaign).where(Campaign.id == cid, Campaign.owner_id == uid))


async def _list(target, session: AsyncSession, uid: int, page: int = 0) -> None:
    total = await session.scalar(select(func.count(Campaign.id)).where(Campaign.owner_id == uid))
    running = await session.scalar(select(func.count(Campaign.id)).where(
        Campaign.owner_id == uid, Campaign.status.in_(("running", "paused"))))
    pages = max(math.ceil(total / PAGE), 1)
    page = min(page, pages - 1)
    camps = (await session.scalars(select(Campaign).where(Campaign.owner_id == uid)
                                   .order_by(Campaign.id.desc()).offset(page * PAGE).limit(PAGE))).all()
    head = (f"🚀 <b>Рассылки</b>\n{SEP}\nВсего: <b>{total}</b> · активных: <b>{running}</b>\n\n👇 Откройте рассылку"
            if total else f"🚀 <b>Рассылки</b>\n{SEP}\nПока пусто. Создайте первую рассылку: бот проведёт по шагам.")
    await show(target, head, K.campaigns_list(list(camps), page, pages))


async def _card(target, session: AsyncSession, c: Campaign, note: str = "") -> None:
    n = await session.scalar(select(func.count()).select_from(usable_accounts_stmt(c.owner_id).subquery()))
    await show(target, campaign_card_text(c, n) + note, K.campaign_card(c), track=f"c:{c.id}")


@router.callback_query(Nav.filter(F.a == "camps"))
async def camps(cb: CallbackQuery, callback_data: Nav, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await _list(cb, session, cb.from_user.id, callback_data.p)


@router.callback_query(Nav.filter(F.a == "camp"))
async def camp_view(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    c = await _owned(session, callback_data.id, cb.from_user.id)
    if not c:
        return await cb.answer("Рассылка не найдена", show_alert=True)
    await session.refresh(c)
    await _card(cb, session, c)


# ---------- управление ----------
@router.callback_query(Nav.filter(F.a.in_({"camp_start", "camp_resume"})))
async def camp_start(cb: CallbackQuery, callback_data: Nav, session: AsyncSession, manager: BroadcastManager,
                     live: LiveUpdater) -> None:
    c = await _owned(session, callback_data.id, cb.from_user.id)
    if not c:
        return await cb.answer("Рассылка не найдена", show_alert=True)
    if manager.is_active(c.id):
        await manager.resume(c.id)
    else:
        if not await session.scalar(select(func.count()).select_from(usable_accounts_stmt(c.owner_id).subquery())):
            return await cb.answer("Нет рабочих аккаунтов. Подключите или включите аккаунт.", show_alert=True)
        if not await session.scalar(select(func.count()).select_from(recipients_stmt(c).subquery())):
            return await cb.answer("Некому отправлять: все получатели уже обработаны.", show_alert=True)
        c.status = "running"
        await session.commit()
        await manager.start(c.id, live)
    await cb.answer("🚀 Запущено")
    await session.refresh(c)
    await _card(cb, session, c)


@router.callback_query(Nav.filter(F.a.in_({"camp_pause", "camp_stop"})))
async def camp_ctl(cb: CallbackQuery, callback_data: Nav, session: AsyncSession, manager: BroadcastManager) -> None:
    c = await _owned(session, callback_data.id, cb.from_user.id)
    if not c:
        return await cb.answer("Рассылка не найдена", show_alert=True)
    if callback_data.a == "camp_pause":
        await manager.pause(c.id)
        await cb.answer("⏸ Пауза")
    else:
        await manager.stop(c.id)
        await cb.answer("🛑 Останавливаю…")
        await asyncio.sleep(1.5)  # дать циклу корректно завершиться и записать статус
    await session.refresh(c)
    await _card(cb, session, c)


@router.callback_query(Nav.filter(F.a == "camp_test"))
async def camp_test(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    c = await _owned(session, callback_data.id, cb.from_user.id)
    if not c:
        return await cb.answer("Рассылка не найдена", show_alert=True)
    await cb.answer()
    await _safe_preview(cb, c.text, c.media, c.buttons)


async def _safe_preview(cb: CallbackQuery, text: str, media: list, buttons: list) -> None:
    try:
        await preview(cb.bot, cb.from_user.id, text, media, buttons)
    except TelegramAPIError as e:
        await cb.message.answer(f"❌ Не удалось собрать сообщение: {esc(str(e))}")


@router.callback_query(Nav.filter(F.a == "camp_del"))
async def camp_del(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    c = await _owned(session, callback_data.id, cb.from_user.id)
    if not c:
        return await cb.answer("Рассылка не найдена", show_alert=True)
    await show(cb, f"🗑 <b>Удалить рассылку «{esc(c.name)}»?</b>\nСтатистика и логи тоже будут удалены.",
               confirm_kb(nav("camp_del_ok", c.id), nav("camp", c.id)))


@router.callback_query(Nav.filter(F.a == "camp_del_ok"))
async def camp_del_ok(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    c = await _owned(session, callback_data.id, cb.from_user.id)
    if c and c.status != "running":
        await session.execute(BroadcastLog.__table__.delete().where(BroadcastLog.campaign_id == c.id))
        await session.delete(c)
        await session.commit()
        await cb.answer("Рассылка удалена")
    else:
        await cb.answer("Остановите рассылку перед удалением", show_alert=True)
    await _list(cb, session, cb.from_user.id)


# ---------- статистика и ошибки ----------
@router.callback_query(Nav.filter(F.a == "camp_stats"))
async def camp_stats(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    c = await _owned(session, callback_data.id, cb.from_user.id)
    if not c:
        return await cb.answer("Рассылка не найдена", show_alert=True)
    per_acc = (await session.execute(
        select(Account.phone, func.count()).join(BroadcastLog, BroadcastLog.account_id == Account.id)
        .where(BroadcastLog.campaign_id == c.id, BroadcastLog.status == "sent")
        .group_by(Account.phone).order_by(func.count().desc()))).all()
    errs = (await session.execute(
        select(BroadcastLog.error, func.count()).where(BroadcastLog.campaign_id == c.id, BroadcastLog.status != "sent")
        .group_by(BroadcastLog.error).order_by(func.count().desc()).limit(5))).all()
    done = c.sent + c.blocked + c.failed
    rate = f"{c.sent * 100 // done}%" if done else "—"
    text = [f"📊 <b>{esc(c.name)}</b>\n{SEP}", f"Обработано: <b>{done}</b> из {c.total}",
            f"Успешность доставки: <b>{rate}</b>", ""]
    text += ["<b>Отправлено по аккаунтам</b>"] + [f"• {esc(p)} — {n}" for p, n in per_acc] if per_acc else \
        ["<i>Данных по аккаунтам пока нет</i>"]
    if errs:
        text += ["", "<b>Частые причины недоставки</b>"] + [f"• {esc(e or '—')}: {n}" for e, n in errs]
    await show(cb, "\n".join(text), K.stats_kb(c.id))


@router.callback_query(Nav.filter(F.a == "camp_logs"))
async def camp_logs(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    c = await _owned(session, callback_data.id, cb.from_user.id)
    if not c:
        return await cb.answer("Рассылка не найдена", show_alert=True)
    where = (BroadcastLog.campaign_id == c.id, BroadcastLog.status != "sent")
    total = await session.scalar(select(func.count(BroadcastLog.id)).where(*where))
    pages = max(math.ceil(total / LOG_PAGE), 1)
    page = min(callback_data.p, pages - 1)
    rows = (await session.execute(
        select(BroadcastLog.status, BroadcastLog.error, ParsedLead.username)
        .join(ParsedLead, ParsedLead.id == BroadcastLog.lead_id).where(*where)
        .order_by(BroadcastLog.id.desc()).offset(page * LOG_PAGE).limit(LOG_PAGE))).all()
    lines = [f"{'🚫' if s == 'blocked' else '⚠️'} @{esc(u)} — {esc(e or '—')}" for s, e, u in rows]
    await show(cb, f"📋 <b>Ошибки доставки</b>\n{SEP}\n" + ("\n".join(lines) if lines else "Ошибок нет 🎉"),
               K.logs_kb(c.id, page, pages))


# ---------- мастер создания ----------
def _step(n: int, title: str, hint: str) -> str:
    return f"➕ <b>Новая рассылка</b> · шаг {n}/{STEPS}\n{'●' * n}{'○' * (STEPS - n)}\n{SEP}\n<b>{title}</b>\n{hint}"


@router.callback_query(Nav.filter(F.a == "w_cancel"))
async def w_cancel(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await cb.answer("Отменено")
    await _list(cb, session, cb.from_user.id)


@router.callback_query(Nav.filter(F.a == "camp_new"))
async def w_new(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(MailingForm.name)
    await start_panel(state, cb.message)
    await show(cb, _step(1, "Название", "Как назвать рассылку? Это видите только вы."), K.wizard_cancel())


@router.message(MailingForm.name, F.text)
async def w_name(msg: Message, state: FSMContext) -> None:
    await try_delete(msg)
    await state.update_data(name=msg.text.strip()[:60])
    await state.set_state(MailingForm.text)
    await panel(msg.bot, state, _step(2, "Текст сообщения",
                "Отправьте текст. Жирный, курсив, ссылки и цитаты сохранятся. Можно использовать эмодзи."),
                K.wizard_cancel())


@router.message(MailingForm.text, F.text)
async def w_text(msg: Message, state: FSMContext) -> None:
    await try_delete(msg)
    await state.update_data(text=msg.html_text, media=[])
    await state.set_state(MailingForm.media)
    await panel(msg.bot, state, _step(3, "Медиа",
                f"Пришлите фото, видео или кружок (до {MAX_MEDIA}; несколько фото/видео станут альбомом). "
                "Или пропустите шаг."), K.wizard_media())


@router.message(MailingForm.media, F.photo | F.video | F.video_note)
async def w_media(msg: Message, state: FSMContext) -> None:
    media = (await state.get_data())["media"]
    kind = "photo" if msg.photo else "video" if msg.video else "video_note"
    file_id = (msg.photo[-1] if msg.photo else msg.video or msg.video_note).file_id
    await try_delete(msg)
    note = ""
    if len(media) >= MAX_MEDIA:
        note = "\n\n⚠️ Достигнут лимит медиа."
    elif kind == "video_note" and media or media and media[0]["type"] == "video_note":
        note = "\n\n⚠️ Кружок отправляется отдельно и не сочетается с альбомом."
    else:
        media.append({"type": kind, "file_id": file_id})
        await state.update_data(media=media)
    await panel(msg.bot, state, _step(3, "Медиа", f"Добавлено: <b>{len(media)}</b>. Отправьте ещё или нажмите «Готово».{note}"),
                K.wizard_media())


@router.callback_query(MailingForm.media, Nav.filter(F.a == "w_media_done"))
async def w_media_done(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    if d["media"] and len(d["text"]) > 1000:
        return await cb.answer("Подпись к медиа ограничена 1024 символами. Сократите текст или уберите медиа "
                               "(отмена и заново).", show_alert=True)
    await state.set_state(MailingForm.buttons)
    await show(cb, _step(4, "Ссылки", "С личных аккаунтов нет инлайн-кнопок, поэтому ссылки добавятся в конец "
                                      "сообщения. Формат: строка = ряд, через <code>|</code>:\n\n"
                                      "<code>Сайт - https://example.com | Канал - https://t.me/channel</code>"),
               K.wizard_buttons())


@router.message(MailingForm.buttons, F.text)
async def w_buttons(msg: Message, state: FSMContext, session: AsyncSession) -> None:
    await try_delete(msg)
    try:
        buttons = parse_buttons(msg.text)
    except ValueError as e:
        return await panel(msg.bot, state, _step(4, "Ссылки", f"❌ {esc(str(e))}\nПопробуйте ещё раз."), K.wizard_buttons())
    await state.update_data(buttons=buttons)
    await _ask_tag(msg.bot, state, session, msg.from_user.id)


@router.callback_query(MailingForm.buttons, Nav.filter(F.a == "w_btn_skip"))
async def w_btn_skip(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.update_data(buttons=[])
    await _ask_tag(cb.bot, state, session, cb.from_user.id)


async def _ask_tag(bot, state: FSMContext, session: AsyncSession, uid: int) -> None:
    tags = (await session.execute(
        select(ParsedLead.tag, func.count()).where(ParsedLead.owner_id == uid, ParsedLead.is_dead.is_(False))
        .group_by(ParsedLead.tag))).all()
    if not tags:
        await panel(bot, state, "❌ У вас нет баз получателей. Загрузите список и начните заново.", K.no_bases())
        return await state.clear()
    await state.set_state(MailingForm.segment)
    await panel(bot, state, _step(5, "База получателей", "Кому отправляем? Число — доступных получателей."),
                K.wizard_tags([(t, n) for t, n in tags]))


@router.callback_query(MailingForm.segment, Nav.filter(F.a == "w_tag"))
async def w_tag(cb: CallbackQuery, callback_data: Nav, state: FSMContext) -> None:
    await state.update_data(tag=callback_data.v)
    await state.set_state(MailingForm.delay)
    await show(cb, _step(6, "Скорость", "Пауза между сообщениями случайная (в заданных пределах), темп считается на "
                                        "каждый аккаунт отдельно. <b>Чем медленнее, тем безопаснее для аккаунтов.</b>"),
               K.wizard_delay())


@router.callback_query(MailingForm.delay, Nav.filter(F.a == "w_delay"))
async def w_delay(cb: CallbackQuery, callback_data: Nav, state: FSMContext, session: AsyncSession) -> None:
    _, dmin, dmax, per_min = K.DELAY_PRESETS[callback_data.v]
    await state.update_data(delay_min=dmin, delay_max=dmax, per_minute=per_min)
    d = await state.get_data()
    uid = cb.from_user.id

    count = await session.scalar(select(func.count()).select_from(
        recipients_stmt(Campaign(id=0, owner_id=uid, tag=d["tag"])).subquery()))
    accs = (await session.scalars(usable_accounts_stmt(uid))).all()
    if not accs:
        await state.clear()
        return await show(cb, "❌ Нет рабочих аккаунтов. Подключите или включите аккаунт в разделе «👤 Аккаунты».",
                          K.wizard_cancel())
    lim = await get_limits(session, uid)
    limit = lim.max_recipients or None
    capacity = sum(max(min(a.daily_limit, lim.daily_limit) - (a.sent_today if a.sent_date == date.today() else 0), 0)
                   for a in accs)
    shown = min(count, limit) if limit else count
    await state.update_data(total=shown, limit=limit)
    await state.set_state(MailingForm.confirm)

    warn = f"\n⚠️ Дневных лимитов хватит на ~{capacity}: остальное уйдёт завтра (возобновите рассылку)." \
        if capacity < shown else ""
    if limit and count > limit:
        warn += f"\n🔒 Тариф {esc(lim.name)}: в рассылке будет не больше {limit} получателей из {count}."
    await cb.message.delete()  # панель уйдёт вниз, под предпросмотр
    await _safe_preview(cb, d["text"], d["media"], d["buttons"])
    m = await cb.message.answer(
        f"📋 <b>Проверьте рассылку</b>\n{SEP}\n📦 {esc(d['name'])}\n🎯 База: <code>{esc(d['tag'])}</code> · "
        f"получателей: <b>{shown}</b>\n👤 Аккаунтов: <b>{len(accs)}</b> (лимит на сегодня: {capacity})\n"
        f"⏱ Темп: {dmin:g}–{dmax:g} с, до {per_min}/мин на аккаунт{warn}\n\n"
        "Предпросмотр выше 👆 Создать рассылку?", reply_markup=K.wizard_confirm())
    await state.update_data(chat=m.chat.id, panel=m.message_id)


@router.callback_query(MailingForm.confirm, Nav.filter(F.a == "w_test"))
async def w_test(cb: CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    await cb.answer("Отправил предпросмотр")
    await _safe_preview(cb, d["text"], d["media"], d["buttons"])


@router.callback_query(MailingForm.confirm, Nav.filter(F.a == "w_create"))
async def w_create(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    d = await state.get_data()
    await state.clear()
    c = Campaign(owner_id=cb.from_user.id, name=d["name"], text=d["text"], media=d["media"], buttons=d["buttons"],
                 tag=d["tag"], delay_min=d["delay_min"], delay_max=d["delay_max"], per_minute=d["per_minute"],
                 total=d["total"], limit=d.get("limit"), status="draft")
    session.add(c)
    await session.commit()
    await cb.answer("Создано")
    await _card(cb, session, c, "\n\n💡 <i>Рассылка создана как черновик: проверьте и нажмите «Запустить».</i>")
