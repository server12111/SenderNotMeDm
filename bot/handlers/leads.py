import math
import re
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.base import insert
from bot.database.models import ParsedLead
from bot.keyboards.leads import import_cancel, lead_card, leads_list
from bot.states.forms import LeadImport
from bot.ui import SEP, Nav, confirm_kb, esc, nav, panel, show, start_panel, try_delete

router = Router()
PAGE = 6
MAX_BYTES = 5 * 1024 * 1024
USERNAME_RE = re.compile(r"(?:https?://)?(?:t\.me/)?@?([A-Za-z][A-Za-z0-9_]{4,31})\s*$")
TAG_RE = re.compile(r"^[\w\-]{2,24}$")


async def _stats(session: AsyncSession, uid: int, tag: str) -> tuple[int, int]:
    total, dead = (await session.execute(
        select(func.count(), func.coalesce(func.sum(case((ParsedLead.is_dead.is_(True), 1), else_=0)), 0))
        .where(ParsedLead.owner_id == uid, ParsedLead.tag == tag))).one()
    return total, dead


async def _list(target, session: AsyncSession, uid: int, page: int = 0) -> None:
    rows = (await session.execute(
        select(ParsedLead.tag, func.count(),
               func.coalesce(func.sum(case((ParsedLead.is_dead.is_(False), 1), else_=0)), 0))
        .where(ParsedLead.owner_id == uid).group_by(ParsedLead.tag).order_by(ParsedLead.tag))).all()
    pages = max(math.ceil(len(rows) / PAGE), 1)
    page = min(page, pages - 1)
    head = (f"🎯 <b>Базы получателей</b>\n{SEP}\n" +
            ("Число справа — доступно / всего.\n\n👇 Откройте базу" if rows else
             "Баз пока нет.\n\nЗагрузите список @username: файлом .txt или обычным сообщением."))
    await show(target, head, leads_list([(t, int(n), int(a)) for t, n, a in rows[page * PAGE:(page + 1) * PAGE]],
                                        page, pages))


async def _card(target, session: AsyncSession, uid: int, tag: str, note: str = "") -> None:
    total, dead = await _stats(session, uid, tag)
    if not total:
        return await _list(target, session, uid)
    await show(target, f"🎯 <b>{esc(tag)}</b>\n{SEP}\n👥 Всего: <b>{total}</b>\n✅ Доступно: <b>{total - dead}</b>\n"
                       f"🚫 Недоступно: <b>{dead}</b>\n<i>Недоступные — закрытая приватность, бан, неверный "
                       f"username — исключаются из рассылок автоматически.</i>{note}", lead_card(tag, dead))


@router.callback_query(Nav.filter(F.a == "leads"))
async def leads(cb: CallbackQuery, callback_data: Nav, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await _list(cb, session, cb.from_user.id, callback_data.p)


@router.callback_query(Nav.filter(F.a == "lead"))
async def lead(cb: CallbackQuery, callback_data: Nav, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await _card(cb, session, cb.from_user.id, callback_data.v)


@router.callback_query(Nav.filter(F.a == "lead_dl"))
async def lead_dl(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    names = (await session.scalars(select(ParsedLead.username).where(
        ParsedLead.owner_id == cb.from_user.id, ParsedLead.tag == callback_data.v,
        ParsedLead.is_dead.is_(False)).order_by(ParsedLead.id))).all()
    await cb.answer()
    await cb.message.answer_document(BufferedInputFile("\n".join(f"@{n}" for n in names).encode(),
                                                       filename=f"{callback_data.v}.txt"),
                                     caption=f"📤 Доступные получатели: {len(names)}")


@router.callback_query(Nav.filter(F.a == "lead_clean"))
async def lead_clean(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    res = await session.execute(delete(ParsedLead).where(
        ParsedLead.owner_id == cb.from_user.id, ParsedLead.tag == callback_data.v, ParsedLead.is_dead.is_(True)))
    await session.commit()
    await cb.answer(f"Удалено: {res.rowcount}")
    await _card(cb, session, cb.from_user.id, callback_data.v)


@router.callback_query(Nav.filter(F.a == "lead_del"))
async def lead_del(cb: CallbackQuery, callback_data: Nav) -> None:
    await show(cb, f"🗑 <b>Удалить базу «{esc(callback_data.v)}»?</b>\nВсе получатели будут удалены.",
               confirm_kb(nav("lead_del_ok", v=callback_data.v), nav("lead", v=callback_data.v)))


@router.callback_query(Nav.filter(F.a == "lead_del_ok"))
async def lead_del_ok(cb: CallbackQuery, callback_data: Nav, session: AsyncSession) -> None:
    await session.execute(delete(ParsedLead).where(ParsedLead.owner_id == cb.from_user.id,
                                                   ParsedLead.tag == callback_data.v))
    await session.commit()
    await cb.answer("База удалена")
    await _list(cb, session, cb.from_user.id)


# ---------- импорт ----------
@router.callback_query(Nav.filter(F.a == "lead_new"))
async def lead_new(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(LeadImport.tag)
    await start_panel(state, cb.message)
    await show(cb, f"📥 <b>Новая база</b>\n{SEP}\nВведите <b>название</b> (латиница/цифры/_/-, до 24 символов), "
                   "например <code>crypto_chat</code>:", import_cancel())


@router.callback_query(Nav.filter(F.a == "lead_add"))
async def lead_add(cb: CallbackQuery, callback_data: Nav, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(LeadImport.data)
    await state.update_data(tag=callback_data.v)
    await start_panel(state, cb.message)
    await show(cb, _ask_data(callback_data.v), import_cancel(callback_data.v))


def _ask_data(tag: str) -> str:
    return (f"📥 <b>База «{esc(tag)}»</b>\n{SEP}\nПришлите список получателей:\n"
            "• файл <b>.txt</b> (один @username на строку), или\n• обычное сообщение со списком.\n\n"
            "Дубли и неверные строки будут пропущены.")


@router.message(LeadImport.tag, F.text)
async def got_tag(msg: Message, state: FSMContext) -> None:
    await try_delete(msg)
    tag = msg.text.strip()
    if not TAG_RE.match(tag):
        return await panel(msg.bot, state, "❌ Название: 2–24 символа, буквы/цифры/_/-. Ещё раз:", import_cancel())
    await state.update_data(tag=tag)
    await state.set_state(LeadImport.data)
    await panel(msg.bot, state, _ask_data(tag), import_cancel(tag))


async def _import(msg: Message, state: FSMContext, session: AsyncSession, raw: str) -> None:
    tag = (await state.get_data())["tag"]
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    matches = [m.group(1) for ln in lines if (m := USERNAME_RE.match(ln))]
    names = set(matches)
    added = 0
    ordered = sorted(names)
    for i in range(0, len(ordered), 500):
        res = await session.execute(insert(ParsedLead).values(
            [{"owner_id": msg.from_user.id, "tag": tag, "username": n} for n in ordered[i:i + 500]]
        ).on_conflict_do_nothing())
        added += res.rowcount
    await session.commit()
    note = (f"\n\n✅ Добавлено: <b>{added}</b> · дублей: {len(matches) - added} · "
            f"неверных строк: {len(lines) - len(matches)}")
    total, dead = await _stats(session, msg.from_user.id, tag)
    if total:
        await panel(msg.bot, state, f"🎯 <b>{esc(tag)}</b>\n{SEP}\n👥 Всего: <b>{total}</b>\n"
                                    f"✅ Доступно: <b>{total - dead}</b>\n🚫 Недоступно: <b>{dead}</b>{note}",
                    lead_card(tag, dead))
    else:
        await panel(msg.bot, state, "❌ Не нашёл ни одного корректного @username. Формат: по одному на строку.",
                    import_cancel())
    await state.clear()


@router.message(LeadImport.data, F.document)
async def got_file(msg: Message, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    if msg.document.file_size > MAX_BYTES:
        return await msg.answer("Файл слишком большой (макс. 5 МБ).")
    buf: BytesIO = await bot.download(msg.document)
    await try_delete(msg)
    await _import(msg, state, session, buf.read().decode("utf-8", "ignore"))


@router.message(LeadImport.data, F.text)
async def got_text(msg: Message, state: FSMContext, session: AsyncSession) -> None:
    await try_delete(msg)
    await _import(msg, state, session, msg.text)
