"""Парсер аудитории: аккаунт → источник → режим → ввод → фоновая задача с живым прогрессом."""
import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import parser as K
from bot.services.broadcast import usable_accounts_stmt
from bot.services.live import ParserLive
from bot.services.parser import MODES, SOURCES, AccountBusy, ParserManager
from bot.services.plans import get_limits
from bot.states.forms import ParserForm
from bot.ui import LIVE, SEP, Nav, esc, panel, show, start_panel, try_delete
from bot.views import parser_card_text

router = Router()
MAX_BYTES = 5 * 1024 * 1024
MAX_CHATS, MAX_LINES, MAX_PHONES = 50, 20000, 5000
IDENT_RE = re.compile(r"^(?:https?://)?(?:t\.me/)?@?([A-Za-z][A-Za-z0-9_]{3,31})$")

PROMPTS = {
    "groups": "Отправьте ссылки или @username чатов (по одному в строке или через пробел).\n"
              "Аккаунт должен состоять в приватных чатах. Администраторы, боты и удалённые аккаунты исключаются.",
    "authors": "Отправьте ссылки или @username чатов. Бот соберёт авторов последних сообщений: это работает, даже "
               "если список участников скрыт.",
    "file": "Пришлите <b>.txt</b> файл или вставьте список: по одному @username или ID в строке. "
            "Бот проверит каждого и отфильтрует по режиму.",
    "phones": "Пришлите <b>.txt</b> файл или вставьте номера телефонов в международном формате "
              "(<code>+79991234567</code>, по одному в строке). Бот найдёт тех, кто "
              "зарегистрирован в Telegram, и вернёт их @username. Номера временно добавляются в контакты "
              "аккаунта и сразу удаляются.",
}


def parse_chats(text: str) -> list[str]:
    seen, out = set(), []
    for tok in re.split(r"[\s,;]+", text.strip()):
        if tok and tok.lower() not in seen:
            seen.add(tok.lower())
            out.append(tok[:120])
    return out[:MAX_CHATS]


def parse_idents(text: str) -> list[str]:
    seen, out = set(), []
    for line in text.splitlines():
        line = line.strip()
        if line.isdigit():
            ident = line
        elif m := IDENT_RE.match(line):
            ident = m.group(1)
        else:
            continue
        if ident.lower() not in seen:
            seen.add(ident.lower())
            out.append(ident)
    return out[:MAX_LINES]


def parse_phones(text: str) -> list[str]:
    seen, out = set(), []
    for line in text.splitlines():
        digits = re.sub(r"\D", "", line)
        if 7 <= len(digits) <= 15 and digits not in seen:
            seen.add(digits)
            out.append("+" + digits)
    return out[:MAX_PHONES]


PARSERS = {"groups": parse_chats, "authors": parse_chats, "file": parse_idents, "phones": parse_phones}


# ---------- навигация ----------
async def _sources_screen(target, session: AsyncSession, uid: int) -> None:
    lim = await get_limits(session, uid)
    cap = f"до <b>{lim.max_parse}</b> контактов за раз" if lim.max_parse else "без лимита"
    await show(target, f"🧲 <b>Парсер аудитории</b>\n{SEP}\nТариф <b>{esc(lim.name)}</b>: {cap}.\n\n"
                       "Выберите <b>источник</b>:\n\n"
                       "💬 <b>Участники чатов</b>: все, кто состоит в чате\n"
                       "✍️ <b>Авторы сообщений</b>: кто писал в чате (работает и со скрытым списком)\n"
                       "📂 <b>Список из .txt</b>: проверка вашей базы\n"
                       "👤 <b>Контакты аккаунта</b>: из адресной книги\n"
                       "📱 <b>Проверка номеров</b>: найти @username по телефонам", K.parser_sources())


@router.callback_query(Nav.filter(F.a == "parser"))
async def parser(cb: CallbackQuery, state: FSMContext, session: AsyncSession, parsers: ParserManager) -> None:
    await state.clear()
    uid = cb.from_user.id
    if job := parsers.active_for(uid):
        return await show(cb, parser_card_text(job), K.parser_card(job), track=f"p:{job.id}")
    accs = (await session.scalars(usable_accounts_stmt(uid))).all()
    if not accs:
        return await show(cb, f"🧲 <b>Парсер аудитории</b>\n{SEP}\nНужен рабочий аккаунт: парсинг идёт с вашего "
                              "Telegram-аккаунта.", K.parser_no_accounts())
    if len(accs) == 1:
        await state.update_data(account_id=accs[0].id)
        return await _sources_screen(cb, session, uid)
    await show(cb, f"🧲 <b>Парсер аудитории</b>\n{SEP}\nВыберите <b>аккаунт</b>, с которого собирать:",
               K.parser_accounts(list(accs)))


@router.callback_query(Nav.filter(F.a == "pr_cancel"))
async def cancel(cb: CallbackQuery, state: FSMContext, session: AsyncSession, parsers: ParserManager) -> None:
    await cb.answer("Отменено")
    await parser(cb, state, session, parsers)


@router.callback_query(Nav.filter(F.a == "pr_acc"))
async def pick_account(cb: CallbackQuery, callback_data: Nav, state: FSMContext, session: AsyncSession) -> None:
    ids = {a.id for a in (await session.scalars(usable_accounts_stmt(cb.from_user.id))).all()}
    if callback_data.id not in ids:
        return await cb.answer("Аккаунт недоступен", show_alert=True)
    await state.update_data(account_id=callback_data.id)
    await _sources_screen(cb, session, cb.from_user.id)


@router.callback_query(Nav.filter(F.a == "pr_src"))
async def pick_source(cb: CallbackQuery, callback_data: Nav, state: FSMContext) -> None:
    source = callback_data.v
    if source not in SOURCES or "account_id" not in await state.get_data():
        return await cb.answer("Начните заново", show_alert=True)
    await state.update_data(source=source)
    if source == "phones":  # проверка номеров: режимы не нужны
        return await _ask_input(cb, state, "all")
    await show(cb, f"🧲 <b>{SOURCES[source]}</b>\n{SEP}\nВыберите <b>режим</b> фильтрации:\n\n"
                   "<i>⚠️ Режимы с NFT-подарками работают заметно медленнее из-за лимитов Telegram.</i>",
               K.parser_modes())


@router.callback_query(Nav.filter(F.a == "pr_mode"))
async def pick_mode(cb: CallbackQuery, callback_data: Nav, state: FSMContext, session: AsyncSession,
                    parsers: ParserManager, parser_live: ParserLive) -> None:
    mode = callback_data.v
    d = await state.get_data()
    if mode not in MODES or "source" not in d:
        return await cb.answer("Начните заново", show_alert=True)
    if d["source"] == "contacts":
        await state.update_data(mode=mode)
        return await _launch(cb, state, session, parsers, parser_live, [])
    await _ask_input(cb, state, mode)


async def _ask_input(cb: CallbackQuery, state: FSMContext, mode: str) -> None:
    d = await state.get_data()
    await state.update_data(mode=mode)
    await state.set_state(ParserForm.input)
    await start_panel(state, cb.message)
    await show(cb, f"🧲 <b>{SOURCES[d['source']]}</b>\nРежим: {MODES[mode]}\n{SEP}\n{PROMPTS[d['source']]}",
               K.parser_cancel())


# ---------- ввод данных ----------
@router.message(ParserForm.input, F.document)
async def got_file(msg: Message, state: FSMContext, session: AsyncSession, bot: Bot, parsers: ParserManager,
                   parser_live: ParserLive) -> None:
    if msg.document.file_size > MAX_BYTES:
        return await msg.answer("Файл слишком большой (макс. 5 МБ).")
    buf = await bot.download(msg.document)
    await try_delete(msg)
    await _from_text(msg, state, session, parsers, parser_live, buf.read().decode("utf-8", "ignore"))


@router.message(ParserForm.input, F.text)
async def got_text(msg: Message, state: FSMContext, session: AsyncSession, parsers: ParserManager,
                   parser_live: ParserLive) -> None:
    await try_delete(msg)
    await _from_text(msg, state, session, parsers, parser_live, msg.text)


async def _from_text(msg: Message, state: FSMContext, session: AsyncSession, parsers: ParserManager,
                     parser_live: ParserLive, text: str) -> None:
    d = await state.get_data()
    items = PARSERS[d["source"]](text)
    if not items:
        return await panel(msg.bot, state, f"🧲 <b>{SOURCES[d['source']]}</b>\n{SEP}\n❌ Не нашёл подходящих строк.\n\n"
                                           f"{PROMPTS[d['source']]}", K.parser_cancel())
    await _launch(msg, state, session, parsers, parser_live, items)


# ---------- запуск ----------
async def _launch(target, state: FSMContext, session: AsyncSession, parsers: ParserManager,
                  parser_live: ParserLive, items: list[str]) -> None:
    d = await state.get_data()
    uid = target.from_user.id
    bot = target.bot
    lim = await get_limits(session, uid)
    accs = {a.id: a for a in (await session.scalars(usable_accounts_stmt(uid))).all()}
    account = accs.get(d.get("account_id"))
    if not account:
        await state.clear()
        text, markup = "❌ Аккаунт недоступен. Выберите другой.", K.parser_no_accounts()
        return await (show(target, text, markup) if isinstance(target, CallbackQuery)
                      else panel(bot, state, text, markup))
    try:
        job = await parsers.start(owner_id=uid, account=account, source=d["source"], mode=d["mode"], items=items,
                                  cap=lim.max_parse or None, on_progress=parser_live)
    except (RuntimeError, AccountBusy) as e:
        await state.clear()
        text = ("Парсинг уже идёт: дождитесь завершения или остановите его." if isinstance(e, RuntimeError) else
                f"Аккаунт сейчас занят ({'рассылкой' if e.args and e.args[0] == 'campaign' else 'другой задачей'}). "
                "Дождитесь окончания или выберите другой аккаунт.")
        return await (target.answer(text, show_alert=True) if isinstance(target, CallbackQuery)
                      else target.answer(text))
    text, markup = parser_card_text(job), K.parser_card(job)
    if isinstance(target, CallbackQuery):
        await show(target, text, markup, track=f"p:{job.id}")
    else:  # ввод был обычным сообщением: правим панель мастера и подписываем её на live-обновления
        chat, mid = d["chat"], d["panel"]
        try:
            await bot.edit_message_text(text, chat_id=chat, message_id=mid, reply_markup=markup)
        except TelegramAPIError:
            mid = (await bot.send_message(chat, text, reply_markup=markup)).message_id
        LIVE[(chat, mid)] = f"p:{job.id}"
    await state.clear()


# ---------- управление задачей ----------
def _owned(parsers: ParserManager, job_id: int, uid: int):
    job = parsers.get(job_id)
    return job if job and job.owner_id == uid else None


@router.callback_query(Nav.filter(F.a == "pr_view"))
async def view(cb: CallbackQuery, callback_data: Nav, parsers: ParserManager) -> None:
    job = _owned(parsers, callback_data.id, cb.from_user.id)
    if not job:
        return await cb.answer("Задача не найдена", show_alert=True)
    await show(cb, parser_card_text(job), K.parser_card(job), track=f"p:{job.id}")


@router.callback_query(Nav.filter(F.a == "pr_stop"))
async def stop(cb: CallbackQuery, callback_data: Nav, parsers: ParserManager) -> None:
    job = _owned(parsers, callback_data.id, cb.from_user.id)
    if not job:
        return await cb.answer("Задача не найдена", show_alert=True)
    parsers.stop(job.id)
    await cb.answer("🛑 Останавливаю: собранное сохранится в базу")


@router.callback_query(Nav.filter(F.a == "pr_dl"))
async def download(cb: CallbackQuery, callback_data: Nav, parsers: ParserManager) -> None:
    job = _owned(parsers, callback_data.id, cb.from_user.id)
    if not job or not job.found:
        return await cb.answer("Пока ничего не собрано", show_alert=True)
    await cb.answer()
    await cb.message.answer_document(
        BufferedInputFile(parsers.results_text(job).encode(), filename=f"parsed_{job.id}.txt"),
        caption=f"📥 Промежуточный результат: {len(job.found)}")
