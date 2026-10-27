"""Парсер аудитории с аккаунтов клиентов (Telethon).

Источники: участники чатов, авторы сообщений, список из .txt, контакты аккаунта, проверка номеров.
Режимы: все / недавно онлайн / Premium / NFT-подарки / Premium + NFT. Результат сохраняется в базу
получателей с тегом. Администраторы чатов, боты и удалённые аккаунты исключаются.
"""
import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import count

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telethon import errors
from telethon.tl.functions.contacts import DeleteContactsRequest, GetContactsRequest, ImportContactsRequest
from telethon.tl.functions.messages import CheckChatInviteRequest
from telethon.tl.functions.payments import GetSavedStarGiftsRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import (
    ChannelParticipantsAdmins, ChatInviteAlready, InputPhoneContact, StarGiftUnique, User,
    UserStatusLastWeek, UserStatusOffline, UserStatusOnline, UserStatusRecently,
)

from bot.database.base import insert
from bot.database.models import Account, ParsedLead
from bot.services import busy
from bot.services.accounts import open_client

log = logging.getLogger(__name__)

SOURCES = {
    "groups": "💬 Участники чатов",
    "authors": "✍️ Авторы сообщений",
    "file": "📂 Список из .txt",
    "contacts": "👤 Контакты аккаунта",
    "phones": "📱 Проверка номеров",
}
MODES = {
    "all": "👥 Все",
    "recent": "🟢 Недавно онлайн (7 дн.)",
    "premium": "⭐ Premium",
    "gifts": "💎 NFT-подарки",
    "premium_gifts": "⭐💎 Premium + NFT",
}
UNITS = {"groups": "Чатов", "authors": "Чатов", "file": "Строк", "contacts": "Контактов", "phones": "Номеров"}

FULL_DELAY = 0.35        # пауза между «тяжёлыми» запросами (GetFullUser и т.п.)
MAX_FLOOD_SLEEP = 300    # FloodWait дольше — пропускаем элемент, а не стоим
AUTHOR_MESSAGES = 5000   # сколько последних сообщений чата просматриваем в режиме «авторы»
TICK = 2.5               # как часто обновлять карточку прогресса, сек
NOTES_KEEP = 4
KEEP_FINISHED = 3600     # сколько хранить завершённые задачи в памяти, сек
INVITE_RE = re.compile(r"(?:https?://)?t\.me/(?:\+|joinchat/)([\w-]+)", re.I)


class AccountBusy(Exception):
    """Аккаунт занят другой задачей; в .args[0] — чем именно."""


class _Skip(Exception):
    """Пропустить текущий элемент (причина попадает в заметки задачи)."""


@dataclass
class Job:
    id: int
    owner_id: int
    account_id: int
    source: str
    mode: str
    items: list[str]
    cap: int | None
    tag: str
    status: str = "running"            # running / done / stopped / failed
    found: dict[str, dict] = field(default_factory=dict)
    total: int = 0
    processed: int = 0
    checked: int = 0
    premium: int = 0
    gifts: int = 0
    no_username: int = 0
    errors: int = 0
    saved: int = 0
    capped: bool = False
    current: str = ""
    notes: list[str] = field(default_factory=list)
    error: str | None = None
    stop: bool = False
    started: float = field(default_factory=time.time)
    finished: float | None = None
    task: asyncio.Task | None = None

    def note(self, text: str) -> None:
        self.notes.append(text)
        del self.notes[:-NOTES_KEEP]

    @property
    def elapsed(self) -> float:
        return (self.finished or time.time()) - self.started


@dataclass
class _Ctx:
    job: Job
    client: object
    on_progress: object
    exclude: set = field(default_factory=set)
    seen: set = field(default_factory=set)
    last_tick: float = 0.0

    @property
    def full(self) -> bool:
        return self.job.cap is not None and len(self.job.found) >= self.job.cap

    async def tick(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_tick < TICK:
            return
        self.last_tick = now
        if self.on_progress:
            try:
                await self.on_progress(self.job, False)
            except Exception:
                log.debug("progress callback failed", exc_info=True)


# ---------- вспомогательное ----------
def _username(u: User) -> str | None:
    if getattr(u, "username", None):
        return u.username
    for un in getattr(u, "usernames", None) or []:
        if getattr(un, "active", False):
            return un.username
    return None


def _recent(u: User) -> bool:
    st = getattr(u, "status", None)
    if isinstance(st, (UserStatusOnline, UserStatusRecently, UserStatusLastWeek)):
        return True
    if isinstance(st, UserStatusOffline) and st.was_online:
        return datetime.now(timezone.utc) - st.was_online.astimezone(timezone.utc) <= timedelta(days=7)
    return False


def _explain(exc: Exception) -> str:
    if isinstance(exc, _Skip):
        return str(exc)
    if isinstance(exc, (errors.UsernameNotOccupiedError, errors.UsernameInvalidError)) or (
            isinstance(exc, ValueError) and "find any entity" in str(exc)):
        return "чат не найден"
    if isinstance(exc, (errors.ChannelPrivateError, errors.ChannelInvalidError)):
        return "нет доступа: аккаунт не состоит в чате"
    if isinstance(exc, errors.ChatAdminRequiredError):
        return "список участников скрыт администраторами"
    if isinstance(exc, (errors.InviteHashExpiredError, errors.InviteHashInvalidError)):
        return "ссылка-приглашение недействительна"
    return type(exc).__name__


async def _resolve(client, ref: str):
    m = INVITE_RE.search(ref)
    if m:  # приватные чаты читаем только если аккаунт уже в них состоит
        res = await client(CheckChatInviteRequest(m.group(1)))
        if isinstance(res, ChatInviteAlready):
            return res.chat
        raise _Skip("аккаунт не состоит в этом приватном чате")
    return await client.get_entity(ref)


async def _admin_ids(client, entity) -> set[int]:
    try:
        return {u.id async for u in client.iter_participants(entity, filter=ChannelParticipantsAdmins)}
    except Exception:
        return set()


async def _flood_wait(job: Job, e: errors.FloodWaitError) -> None:
    if e.seconds > MAX_FLOOD_SLEEP:
        raise _Skip(f"FloodWait {e.seconds // 60} мин: пропущено")
    job.note(f"FloodWait {e.seconds} с: жду")
    await asyncio.sleep(e.seconds + 1)


class ParserManager:
    def __init__(self, bot, sm: async_sessionmaker[AsyncSession]) -> None:
        self.bot, self.sm = bot, sm
        self.jobs: dict[int, Job] = {}
        self._seq = count(1)

    # ---------- доступ ----------
    def get(self, job_id: int) -> Job | None:
        return self.jobs.get(job_id)

    def active_for(self, owner_id: int) -> Job | None:
        return next((j for j in self.jobs.values() if j.owner_id == owner_id and j.status == "running"), None)

    def stop(self, job_id: int) -> None:
        if job := self.jobs.get(job_id):
            job.stop = True

    @staticmethod
    def results_text(job: Job) -> str:
        return "\n".join(f"@{d['username']}" for d in job.found.values())

    async def new_tag(self, owner_id: int) -> str:
        base = datetime.now().strftime("parse_%d%m_%H%M")
        async with self.sm() as s:
            existing = set((await s.scalars(select(ParsedLead.tag).where(
                ParsedLead.owner_id == owner_id, ParsedLead.tag.like(f"{base}%")).distinct())).all())
        tag, n = base, 1
        while tag in existing:
            n += 1
            tag = f"{base}_{n}"
        return tag

    # ---------- запуск ----------
    async def start(self, *, owner_id: int, account: Account, source: str, mode: str, items: list[str],
                    cap: int | None, on_progress) -> Job:
        # Всё до первого await синхронно: двойной тап не запустит два парсинга и не откроет аккаунт дважды
        if self.active_for(owner_id):
            raise RuntimeError("parser already running")
        if not busy.acquire(account.id, "parser"):
            raise AccountBusy(busy.holder(account.id))
        now = time.time()
        for jid in [j.id for j in self.jobs.values() if j.finished and now - j.finished > KEEP_FINISHED]:
            self.jobs.pop(jid, None)
        job = Job(id=next(self._seq), owner_id=owner_id, account_id=account.id, source=source, mode=mode,
                  items=items, cap=cap, tag="", total=len(items))
        self.jobs[job.id] = job
        try:
            job.tag = await self.new_tag(owner_id)
        except BaseException:
            self.jobs.pop(job.id, None)
            busy.release(account.id)
            raise
        job.task = asyncio.create_task(self._run(job, account, on_progress))
        return job

    async def _run(self, job: Job, account: Account, on_progress) -> None:
        client = None
        final = "failed"  # итоговый статус выставляется только после сохранения базы
        try:
            client = await open_client(account)
            if client is None:
                job.error = "Аккаунт разлогинен: подключите его заново"
                return
            ctx = _Ctx(job, client, on_progress)
            await ctx.tick(force=True)
            if job.source in ("groups", "authors"):
                await self._src_chats(ctx, authors=job.source == "authors")
            elif job.source == "contacts":
                await self._src_contacts(ctx)
            elif job.source == "phones":
                await self._src_phones(ctx)
            else:
                await self._src_list(ctx)
            job.capped = ctx.full
            final = "stopped" if job.stop else "done"
        except Exception as e:
            log.exception("parser job %s failed", job.id)
            job.error = _explain(e)
        finally:
            if client:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            busy.release(account.id)
            try:
                await self._save(job)
            except Exception:
                log.exception("parser save failed")
                job.error = job.error or "не удалось сохранить базу"
            job.status, job.finished = final, time.time()
            if on_progress:
                try:
                    await on_progress(job, True)
                except Exception:
                    log.debug("progress callback failed", exc_info=True)

    async def _save(self, job: Job) -> None:
        if not job.found:
            return
        rows = [{"owner_id": job.owner_id, "tag": job.tag, "username": d["username"], "tg_user_id": d["id"],
                 "first_name": d["name"], "source_chat": d["src"]} for d in job.found.values()]
        async with self.sm() as s:
            for i in range(0, len(rows), 150):  # 150 × 6 колонок < 999: лимит переменных старых SQLite
                res = await s.execute(insert(ParsedLead).values(rows[i:i + 150]).on_conflict_do_nothing())
                job.saved += res.rowcount or 0
            await s.commit()

    # ---------- проверка пользователя ----------
    async def _call(self, ctx: _Ctx, request):
        for _ in range(2):
            try:
                return await ctx.client(request)
            except errors.FloodWaitError as e:
                await _flood_wait(ctx.job, e)
        raise _Skip("повторный FloodWait")

    async def _has_nft(self, ctx: _Ctx, user: User, full=None) -> bool:
        try:
            if full is None:
                full = await self._call(ctx, GetFullUserRequest(user))
                await asyncio.sleep(FULL_DELAY)
            if not getattr(full.full_user, "stargifts_count", 0):
                return False
            peer = await ctx.client.get_input_entity(user)
            res = await self._call(ctx, GetSavedStarGiftsRequest(peer=peer, offset="", limit=100))
            await asyncio.sleep(FULL_DELAY)
            return any(isinstance(getattr(g, "gift", None), StarGiftUnique) for g in res.gifts)
        except _Skip:
            return False
        except Exception:
            ctx.job.errors += 1
            return False

    async def _consume(self, ctx: _Ctx, user, source: str, full=None) -> None:
        job = ctx.job
        job.checked += 1
        if (not isinstance(user, User) or user.bot or user.deleted
                or user.id in ctx.seen or user.id in ctx.exclude):
            return
        ctx.seen.add(user.id)
        premium = bool(getattr(user, "premium", False))
        mode = job.mode
        if mode in ("premium", "premium_gifts") and not premium:
            return
        if mode == "recent" and not _recent(user):
            return
        gifts = False
        if mode in ("gifts", "premium_gifts"):
            gifts = await self._has_nft(ctx, user, full)
            if not gifts:
                return
        name = _username(user)
        if not name:
            job.no_username += 1  # писать по id с другого аккаунта нельзя
            return
        key = name.lower()
        if key in job.found:
            return
        job.found[key] = {"username": name, "id": user.id, "name": (user.first_name or "")[:200], "src": source[:250]}
        job.premium += premium
        job.gifts += gifts
        await ctx.tick()

    # ---------- источники ----------
    async def _src_chats(self, ctx: _Ctx, authors: bool) -> None:
        job, c = ctx.job, ctx.client
        for ref in job.items:
            if job.stop or ctx.full:
                break
            job.current = ref
            try:
                entity = await _resolve(c, ref)
                title = getattr(entity, "title", None) or ref
                ctx.exclude = await _admin_ids(c, entity)
                if authors:
                    async for m in c.iter_messages(entity, limit=AUTHOR_MESSAGES):
                        if job.stop or ctx.full:
                            break
                        sender = m.sender or (await m.get_sender() if m.sender_id else None)
                        if sender is not None:
                            await self._consume(ctx, sender, title)
                else:
                    async for u in c.iter_participants(entity):
                        if job.stop or ctx.full:
                            break
                        await self._consume(ctx, u, title)
            except errors.FloodWaitError as e:
                job.errors += 1
                try:
                    await _flood_wait(job, e)
                except _Skip as s:
                    job.note(f"{ref}: {s}")
            except Exception as e:
                job.errors += 1
                job.note(f"{ref}: {_explain(e)}")
            job.processed += 1
            await ctx.tick(force=True)

    async def _src_list(self, ctx: _Ctx) -> None:
        job = ctx.job
        for ident in job.items:
            if job.stop or ctx.full:
                break
            job.current = ident
            try:
                full = await self._call(ctx, GetFullUserRequest(int(ident) if ident.isdigit() else ident))
                await asyncio.sleep(FULL_DELAY)
                if full.users:
                    await self._consume(ctx, full.users[0], "список", full=full)
            except _Skip as s:
                job.errors += 1
                job.note(f"{ident}: {s}")
            except Exception:
                job.errors += 1  # несуществующий username/id — обычное дело в чужих базах
            job.processed += 1
            await ctx.tick()

    async def _src_contacts(self, ctx: _Ctx) -> None:
        job = ctx.job
        try:
            res = await self._call(ctx, GetContactsRequest(hash=0))
        except Exception as e:
            raise RuntimeError("не удалось получить контакты") from e
        users = getattr(res, "users", []) or []
        job.total = len(users)
        if not users:
            job.note("В контактах аккаунта никого нет")
        for u in users:
            if job.stop or ctx.full:
                break
            await self._consume(ctx, u, "контакты")
            job.processed += 1
            await ctx.tick()

    async def _src_phones(self, ctx: _Ctx) -> None:
        job = ctx.job
        phones = job.items
        for i in range(0, len(phones), 100):
            if job.stop or ctx.full:
                break
            chunk = phones[i:i + 100]
            job.current = f"{i + 1}–{i + len(chunk)}"
            try:
                res = await self._call(ctx, ImportContactsRequest(
                    [InputPhoneContact(client_id=i + k, phone=p, first_name="c", last_name="")
                     for k, p in enumerate(chunk)]))
                users = list(res.users)
                for u in users:
                    await self._consume(ctx, u, "номера")
                if users:  # временно добавленные контакты убираем из аккаунта
                    try:
                        await ctx.client(DeleteContactsRequest(id=users))
                    except Exception:
                        job.note("не удалось удалить временные контакты")
            except _Skip as s:
                job.errors += 1
                job.note(str(s))
            except Exception as e:
                job.errors += 1
                job.note(_explain(e))
            job.processed += len(chunk)
            await ctx.tick(force=True)
            await asyncio.sleep(2)  # импорт контактов жёстко лимитируется Telegram
