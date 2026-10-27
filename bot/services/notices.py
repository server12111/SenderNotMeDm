"""Рассылка админа по пользователям бота: сегменты, троттлинг, FloodWait, пауза/стоп, resume."""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import exists, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.database.models import Notice, NoticeLog, Subscription, User

log = logging.getLogger(__name__)

BATCH = 200
FLUSH_EVERY = 25
DELAY = 0.05  # ≈20 сообщений/сек: безопасно ниже лимита Bot API (30/сек)
DEAD_MARKERS = ("chat not found", "user is deactivated", "bot was blocked", "peer_id_invalid")
ProgressCb = Callable[[Notice, bool], Awaitable[None]]


def markup(buttons: list[list[dict]]) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=b["text"], url=b["url"]) for b in row] for row in buttons])


async def send_notice(bot: Bot, chat_id: int, text: str, media: list[dict], buttons: list[list[dict]]) -> None:
    """Единая отправка для предпросмотра и боевой рассылки (кнопки настоящие, бот умеет)."""
    kb = markup(buttons)
    if not media:
        await bot.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)
        return
    m = media[0]
    send = {"photo": bot.send_photo, "video": bot.send_video, "animation": bot.send_animation}[m["type"]]
    await send(chat_id, m["file_id"], caption=text or None, reply_markup=kb)


def recipients_stmt(n: Notice, after_id: int = 0, limit: int | None = None):
    paid = select(Subscription.user_id).where(
        Subscription.status == "active",
        (Subscription.expires_at.is_(None)) | (Subscription.expires_at > func.now()))
    stmt = select(User.id).where(User.is_active.is_(True), User.is_banned.is_(False), User.id > after_id,
                                 ~exists().where(NoticeLog.notice_id == n.id, NoticeLog.user_id == User.id))
    if n.segment == "paid":
        stmt = stmt.where(User.id.in_(paid))
    elif n.segment == "free":
        stmt = stmt.where(User.id.not_in(paid))
    stmt = stmt.order_by(User.id)
    return stmt.limit(limit) if limit else stmt


@dataclass
class _Control:
    running: asyncio.Event = field(default_factory=asyncio.Event)
    stop: bool = False
    task: asyncio.Task | None = None

    def __post_init__(self) -> None:
        self.running.set()


class NoticeManager:
    def __init__(self, bot: Bot, sm: async_sessionmaker[AsyncSession]) -> None:
        self.bot, self.sm = bot, sm
        self._ctl: dict[int, _Control] = {}

    def is_active(self, nid: int) -> bool:
        ctl = self._ctl.get(nid)
        return bool(ctl and ctl.task and not ctl.task.done())

    async def start(self, nid: int, on_progress: ProgressCb | None = None) -> None:
        if self.is_active(nid):
            return
        ctl = _Control()
        self._ctl[nid] = ctl
        ctl.task = asyncio.create_task(self._run(nid, ctl, on_progress))

    async def pause(self, nid: int) -> None:
        if ctl := self._ctl.get(nid):
            ctl.running.clear()
            await self._set_status(nid, "paused")

    async def resume(self, nid: int) -> None:
        if ctl := self._ctl.get(nid):
            ctl.running.set()
            await self._set_status(nid, "running")

    async def stop(self, nid: int) -> None:
        if ctl := self._ctl.get(nid):
            ctl.stop = True
            ctl.running.set()

    async def _set_status(self, nid: int, status: str) -> None:
        async with self.sm() as s:
            await s.execute(update(Notice).where(Notice.id == nid).values(status=status))
            await s.commit()

    async def recover(self, on_progress: ProgressCb | None = None) -> None:
        async with self.sm() as s:
            ids = (await s.scalars(select(Notice.id).where(Notice.status == "running"))).all()
        for nid in ids:
            await self.start(nid, on_progress)

    async def _run(self, nid: int, ctl: _Control, on_progress: ProgressCb | None) -> None:
        async with self.sm() as s:
            n = await s.get(Notice, nid)
            if n.total == 0:
                n.total = await s.scalar(select(func.count()).select_from(recipients_stmt(n).subquery()))
            n.status = "running"
            n.started_at = n.started_at or datetime.now(timezone.utc)
            await s.commit()
            s.expunge(n)

        logs: list[dict] = []
        dead: list[int] = []
        counters = {"sent": 0, "failed": 0, "blocked": 0}
        last_id = 0
        final = "done"

        async def flush() -> None:
            async with self.sm() as s:
                if logs:
                    await s.execute(insert(NoticeLog), logs)
                if dead:
                    await s.execute(update(User).where(User.id.in_(dead)).values(is_active=False))
                await s.execute(update(Notice).where(Notice.id == nid).values(
                    **{k: getattr(Notice, k) + v for k, v in counters.items()}))
                await s.commit()
                fresh = await s.get(Notice, nid)
            logs.clear(); dead.clear(); counters.update(sent=0, failed=0, blocked=0)
            if on_progress:
                try:
                    await on_progress(fresh, False)
                except Exception:
                    log.debug("progress callback failed", exc_info=True)

        try:
            while not ctl.stop:
                async with self.sm() as s:
                    ids = (await s.scalars(recipients_stmt(n, last_id, BATCH))).all()
                if not ids:
                    break
                for uid in ids:
                    await ctl.running.wait()
                    if ctl.stop:
                        break
                    await asyncio.sleep(DELAY)
                    status, err = await self._send_one(n, uid)
                    last_id = uid
                    logs.append({"notice_id": nid, "user_id": uid, "status": status, "error": err})
                    counters[status] += 1
                    if status == "blocked":
                        dead.append(uid)
                    if len(logs) >= FLUSH_EVERY:
                        await flush()
            if ctl.stop:
                final = "stopped"
        except Exception:
            log.exception("notice %s crashed", nid)
            final = "stopped"
        finally:
            await flush()
            async with self.sm() as s:
                await s.execute(update(Notice).where(Notice.id == nid).values(
                    status=final, finished_at=datetime.now(timezone.utc)))
                await s.commit()
                fresh = await s.get(Notice, nid)
            if on_progress:
                try:
                    await on_progress(fresh, True)
                except Exception:
                    log.debug("progress callback failed", exc_info=True)
            self._ctl.pop(nid, None)

    async def _send_one(self, n: Notice, uid: int) -> tuple[str, str | None]:
        for _ in range(3):
            try:
                await send_notice(self.bot, uid, n.text, n.media, n.buttons)
                return "sent", None
            except TelegramRetryAfter as e:
                log.warning("FloodWait %ss", e.retry_after)
                await asyncio.sleep(e.retry_after + 1)
            except TelegramForbiddenError as e:
                return "blocked", str(e)[:250]
            except TelegramBadRequest as e:
                msg = str(e).lower()
                return ("blocked" if any(m in msg for m in DEAD_MARKERS) else "failed"), str(e)[:250]
            except TelegramAPIError as e:
                return "failed", str(e)[:250]
        return "failed", "retry limit"
