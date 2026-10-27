"""Движок рассылки с клиентских аккаунтов (Telethon).

Пул аккаунтов владельца работает параллельно: у каждого свой темп, дневной лимит и реакция на
FloodWait/PeerFlood. Проблемный аккаунт выбывает из пула, а его получатель возвращается в очередь.
"""
import asyncio
import logging
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot
from sqlalchemy import exists, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telethon import TelegramClient, errors

from bot.config import settings
from bot.database.models import Account, BroadcastLog, Campaign, CampaignStatus, DeliveryStatus, ParsedLead
from bot.services import busy
from bot.services.accounts import open_client
from bot.services.message_builder import compose
from bot.services.plans import get_limits

log = logging.getLogger(__name__)

BATCH = 100
FLUSH_EVERY = 20
SHORT_FLOOD = 60  # FloodWait до этого порога пережидаем на месте, дольше — аккаунт уходит в кулдаун

DEAD_ACCOUNT = (errors.AuthKeyUnregisteredError, errors.UserDeactivatedBanError,
                errors.UserDeactivatedError, errors.SessionRevokedError)
UNREACHABLE = (errors.UserPrivacyRestrictedError, errors.UserIsBlockedError, errors.YouBlockedUserError,
               errors.InputUserDeactivatedError, errors.UsernameNotOccupiedError,
               errors.UsernameInvalidError, ValueError)

ProgressCb = Callable[[Campaign, bool], Awaitable[None]]


@dataclass
class _Control:
    running: asyncio.Event = field(default_factory=asyncio.Event)
    stop: bool = False
    task: asyncio.Task | None = None

    def __post_init__(self) -> None:
        self.running.set()


class RateLimiter:
    """Случайная задержка между сообщениями + скользящий лимит «сообщений в минуту» (на аккаунт)."""

    def __init__(self, delay_min: float, delay_max: float, per_minute: int) -> None:
        self.delay_min, self.delay_max, self.per_minute = delay_min, delay_max, per_minute
        self._stamps: deque[float] = deque()

    async def wait(self) -> None:
        await asyncio.sleep(random.uniform(self.delay_min, self.delay_max))
        now = time.monotonic()
        while self._stamps and now - self._stamps[0] > 60:
            self._stamps.popleft()
        if len(self._stamps) >= self.per_minute:
            await asyncio.sleep(60 - (now - self._stamps[0]) + 0.1)
        self._stamps.append(time.monotonic())


def recipients_stmt(c: Campaign, after_id: int = 0, limit: int | None = None):
    done = exists().where(BroadcastLog.campaign_id == c.id, BroadcastLog.lead_id == ParsedLead.id)
    stmt = (select(ParsedLead.id, ParsedLead.username)
            .where(ParsedLead.owner_id == c.owner_id, ParsedLead.tag == c.tag,
                   ParsedLead.is_dead.is_(False), ParsedLead.id > after_id, ~done)
            .order_by(ParsedLead.id))
    return stmt.limit(limit) if limit else stmt


def usable_accounts_stmt(owner_id: int):
    return select(Account).where(
        Account.owner_id == owner_id, Account.status == "active",
        (Account.cooldown_until.is_(None)) | (Account.cooldown_until < func.now()))


@dataclass
class _Acc:
    model: Account
    client: TelegramClient
    sent_today: int
    limit: int = 0  # эффективный дневной лимит: min(лимит аккаунта, потолок тарифа)
    alive: bool = True
    dirty: bool = False


@dataclass
class _Run:
    c: Campaign
    ctl: _Control
    paths: list[str]
    accs: list[_Acc]
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    inflight: int = 0
    producer_done: bool = False
    budget: int | None = None  # сколько получателей ещё можно взять по лимиту тарифа
    logs: list[dict] = field(default_factory=list)
    dead_leads: list[int] = field(default_factory=list)
    counters: dict = field(default_factory=lambda: {"sent": 0, "failed": 0, "blocked": 0})
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def workers_alive(self) -> int:
        return sum(a.alive for a in self.accs)


class BroadcastManager:
    def __init__(self, bot: Bot, sm: async_sessionmaker[AsyncSession]) -> None:
        self.bot, self.sm = bot, sm
        self._ctl: dict[int, _Control] = {}

    # ---------- управление ----------
    async def start(self, campaign_id: int, on_progress: ProgressCb | None = None) -> None:
        if (old := self._ctl.get(campaign_id)) and not old.task.done():
            return
        ctl = _Control()
        self._ctl[campaign_id] = ctl
        ctl.task = asyncio.create_task(self._run(campaign_id, ctl, on_progress))

    async def pause(self, cid: int) -> None:
        if ctl := self._ctl.get(cid):
            ctl.running.clear()
            await self._set_status(cid, CampaignStatus.PAUSED)

    async def resume(self, cid: int) -> None:
        if ctl := self._ctl.get(cid):
            ctl.running.set()
            await self._set_status(cid, CampaignStatus.RUNNING)

    async def stop(self, cid: int) -> None:
        if ctl := self._ctl.get(cid):
            ctl.stop = True
            ctl.running.set()

    async def _set_status(self, cid: int, status: CampaignStatus) -> None:
        async with self.sm() as s:
            await s.execute(update(Campaign).where(Campaign.id == cid).values(status=status.value))
            await s.commit()

    def is_active(self, cid: int) -> bool:
        ctl = self._ctl.get(cid)
        return bool(ctl and ctl.task and not ctl.task.done())

    async def recover(self, on_progress: ProgressCb | None = None) -> None:
        """При старте: рассылки, прерванные рестартом, продолжаются с места остановки."""
        async with self.sm() as s:
            ids = (await s.scalars(
                select(Campaign.id).where(Campaign.status == CampaignStatus.RUNNING.value))).all()
        for cid in ids:
            await self.start(cid, on_progress)

    # ---------- подготовка ----------
    async def _prepare_media(self, c: Campaign) -> list[str]:
        """Файлы бота скачиваются один раз: file_id бота не годится для клиентского аккаунта."""
        folder = Path(settings.media_dir) / str(c.id)
        folder.mkdir(parents=True, exist_ok=True)
        paths = []
        for i, m in enumerate(c.media):
            p = folder / f"{i}.{'jpg' if m['type'] == 'photo' else 'mp4'}"
            if not p.exists():
                await self.bot.download(m["file_id"], destination=p)
            paths.append(str(p))
        return paths

    async def _notify(self, owner_id: int, text: str) -> None:
        try:
            await self.bot.send_message(owner_id, text)
        except Exception:
            log.debug("notify failed", exc_info=True)

    async def _open_accounts(self, c: Campaign) -> tuple[list[_Acc], int]:
        """Открытые клиенты и число аккаунтов, занятых другой задачей."""
        today = date.today()
        async with self.sm() as s:
            models = (await s.scalars(usable_accounts_stmt(c.owner_id))).all()
            cap = (await get_limits(s, c.owner_id)).daily_limit
        accs: list[_Acc] = []
        taken: list[int] = []
        n_busy = 0
        try:
            for m in models:
                if not busy.acquire(m.id, "campaign"):
                    n_busy += 1
                    continue
                taken.append(m.id)
                try:
                    client = await open_client(m)
                except Exception:
                    log.exception("account %s connect failed", m.id)
                    client = None
                if client is None:
                    busy.release(m.id)
                    taken.remove(m.id)
                    await self._mark_account(m.id, status="dead")
                    await self._notify(c.owner_id, f"⚠️ Аккаунт {m.phone} разлогинен — подключите заново.")
                    continue
                accs.append(_Acc(m, client, m.sent_today if m.sent_date == today else 0,
                                 limit=min(m.daily_limit, cap)))
        except BaseException:
            for acc_id in taken:
                busy.release(acc_id)
            raise
        return accs, n_busy

    async def _mark_account(self, acc_id: int, **values) -> None:
        async with self.sm() as s:
            await s.execute(update(Account).where(Account.id == acc_id).values(**values))
            await s.commit()

    # ---------- рабочий цикл ----------
    async def _run(self, cid: int, ctl: _Control, on_progress: ProgressCb | None) -> None:
        async with self.sm() as s:
            c = await s.get(Campaign, cid)
            if c.total == 0:
                c.total = await s.scalar(select(func.count()).select_from(recipients_stmt(c).subquery()))
                if c.limit:
                    c.total = min(c.total, c.limit)
            c.status = CampaignStatus.RUNNING.value
            c.started_at = c.started_at or datetime.now(timezone.utc)
            await s.commit()
            s.expunge(c)

        run: _Run | None = None
        accs: list[_Acc] = []
        final = CampaignStatus.DONE
        try:
            accs, n_busy = await self._open_accounts(c)
            if not accs:
                why = ("все аккаунты заняты другой задачей (парсинг или другая рассылка)" if n_busy
                       else "нет рабочих аккаунтов")
                await self._notify(c.owner_id, f"🛑 Рассылка «{c.name}»: {why}.")
                final = CampaignStatus.STOPPED
                return
            run = _Run(c, ctl, await self._prepare_media(c), accs)
            if c.limit is not None:
                run.budget = max(c.limit - (c.sent + c.blocked + c.failed), 0)
            await asyncio.gather(self._producer(run), *(self._worker(run, a, on_progress) for a in accs))

            await self._flush(run, None)  # без этого последние логи не видны проверке «остались ли получатели»
            async with self.sm() as s:
                left = await s.scalar(select(func.count()).select_from(recipients_stmt(c).subquery()))
                cur = await s.get(Campaign, cid)
            if c.limit is not None and cur.sent + cur.blocked + cur.failed >= c.limit:
                left = 0  # потолок тарифа достигнут: рассылка выполнена
            if ctl.stop or left:
                final = CampaignStatus.STOPPED
                if left and not ctl.stop:
                    await self._notify(c.owner_id, f"🛑 Рассылка «{c.name}» остановлена: аккаунты исчерпали "
                                       f"лимиты или ограничены. Осталось получателей: {left}.")
        except Exception:
            log.exception("campaign %s crashed", cid)
            final = CampaignStatus.STOPPED
        finally:
            if run:
                await self._flush(run, None)
                for a in run.accs:
                    await a.client.disconnect()
            for a in accs:
                busy.release(a.model.id)  # аккаунты свободны для парсера и других рассылок
            async with self.sm() as s:
                await s.execute(update(Campaign).where(Campaign.id == cid).values(
                    status=final.value, finished_at=datetime.now(timezone.utc)))
                await s.commit()
                fresh = await s.get(Campaign, cid)
            if on_progress:
                try:
                    await on_progress(fresh, True)
                except Exception:
                    log.debug("progress callback failed", exc_info=True)
            self._ctl.pop(cid, None)

    async def _producer(self, run: _Run) -> None:
        last = 0
        while not run.ctl.stop and run.workers_alive:
            if run.queue.qsize() >= BATCH:
                await asyncio.sleep(0.5)
                continue
            async with self.sm() as s:
                rows = (await s.execute(recipients_stmt(run.c, last, BATCH))).all()
            if not rows:
                break
            for r in rows:
                if run.budget is not None:
                    if run.budget <= 0:
                        break
                    run.budget -= 1
                run.queue.put_nowait((r.id, r.username))
                last = r.id
            if run.budget is not None and run.budget <= 0:
                break
        run.producer_done = True

    async def _next(self, run: _Run) -> tuple[int, str] | None:
        while not run.ctl.stop:
            try:
                item = run.queue.get_nowait()
                run.inflight += 1
                return item
            except asyncio.QueueEmpty:
                if run.producer_done and run.inflight == 0:
                    return None
                await asyncio.sleep(0.3)
        return None

    async def _worker(self, run: _Run, acc: _Acc, on_progress: ProgressCb | None) -> None:
        c = run.c
        limiter = RateLimiter(c.delay_min, c.delay_max, c.per_minute)
        try:
            while acc.alive and not run.ctl.stop:
                await run.ctl.running.wait()
                if acc.sent_today >= acc.limit:
                    break  # дневной лимит аккаунта исчерпан
                item = await self._next(run)
                if item is None:
                    break
                lead_id, username = item
                try:
                    await limiter.wait()
                    status, err = await self._send(run, acc, username)
                except _Retire as r:  # аккаунт выбыл — получатель возвращается в очередь
                    run.queue.put_nowait(item)
                    acc.alive = False
                    await self._on_retire(run, acc, r)
                    break
                except _Retry:
                    run.queue.put_nowait(item)
                    continue
                finally:
                    run.inflight -= 1
                async with run.lock:
                    run.logs.append({"campaign_id": c.id, "lead_id": lead_id, "account_id": acc.model.id,
                                     "status": status.value, "error": err})
                    if status is DeliveryStatus.SENT:
                        run.counters["sent"] += 1
                        acc.sent_today += 1
                        acc.dirty = True
                    elif status is DeliveryStatus.BLOCKED:
                        run.counters["blocked"] += 1
                        run.dead_leads.append(lead_id)
                    else:
                        run.counters["failed"] += 1
                    if len(run.logs) >= FLUSH_EVERY:
                        await self._flush(run, on_progress)
        finally:
            acc.alive = False

    async def _send(self, run: _Run, acc: _Acc, username: str) -> tuple[DeliveryStatus, str | None]:
        c, client, target = run.c, acc.client, f"@{username.lstrip('@')}"
        text = compose(c.text, c.buttons)
        try:
            if not run.paths:
                await client.send_message(target, text, parse_mode="html", link_preview=False)
            elif c.media[0]["type"] == "video_note":
                await client.send_file(target, run.paths[0], video_note=True)
                if text:
                    await client.send_message(target, text, parse_mode="html", link_preview=False)
            elif len(run.paths) == 1:
                await client.send_file(target, run.paths[0], caption=text, parse_mode="html")
            else:
                await client.send_file(target, run.paths, caption=[text] + [""] * (len(run.paths) - 1),
                                       parse_mode="html")
            return DeliveryStatus.SENT, None
        except errors.FloodWaitError as e:
            if e.seconds <= SHORT_FLOOD:
                log.warning("acc %s FloodWait %ss", acc.model.id, e.seconds)
                await asyncio.sleep(e.seconds + 1)
                raise _Retry
            raise _Retire("cooldown", e.seconds)
        except errors.PeerFloodError:
            raise _Retire("limited")
        except DEAD_ACCOUNT:
            raise _Retire("dead")
        except UNREACHABLE as e:
            return DeliveryStatus.BLOCKED, type(e).__name__
        except errors.RPCError as e:
            return DeliveryStatus.FAILED, str(e)[:250]
        except (ConnectionError, asyncio.TimeoutError) as e:
            return DeliveryStatus.FAILED, type(e).__name__

    async def _on_retire(self, run: _Run, acc: _Acc, r: "_Retire") -> None:
        m = acc.model
        if r.kind == "cooldown":
            until = datetime.now(timezone.utc) + timedelta(seconds=r.seconds)
            await self._mark_account(m.id, cooldown_until=until)
            note = f"FloodWait {r.seconds // 60} мин — отдыхает"
        elif r.kind == "limited":
            await self._mark_account(m.id, status="limited")
            note = "ограничен Telegram за спам (PeerFlood) — проверьте @SpamBot"
        else:
            await self._mark_account(m.id, status="dead")
            note = "разлогинен или заблокирован"
        await self._notify(run.c.owner_id, f"⚠️ Аккаунт {m.phone}: {note}. Выведен из рассылки.")

    async def _flush(self, run: _Run, on_progress: ProgressCb | None) -> None:
        logs, dead = run.logs[:], run.dead_leads[:]
        counters = dict(run.counters)
        run.logs.clear(); run.dead_leads.clear()
        run.counters.update(sent=0, failed=0, blocked=0)
        async with self.sm() as s:
            if logs:
                await s.execute(insert(BroadcastLog), logs)
            if dead:
                await s.execute(update(ParsedLead).where(ParsedLead.id.in_(dead)).values(is_dead=True))
            for a in run.accs:
                if a.dirty:
                    await s.execute(update(Account).where(Account.id == a.model.id).values(
                        sent_today=a.sent_today, sent_date=date.today()))
                    a.dirty = False
            await s.execute(update(Campaign).where(Campaign.id == run.c.id).values(
                **{k: getattr(Campaign, k) + v for k, v in counters.items()}))
            await s.commit()
            fresh = await s.get(Campaign, run.c.id)
        if on_progress:
            try:
                await on_progress(fresh, False)
            except Exception:
                log.debug("progress callback failed", exc_info=True)


class _Retry(Exception):
    """Повторить того же получателя тем же аккаунтом (после короткого FloodWait)."""


class _Retire(Exception):
    def __init__(self, kind: str, seconds: int = 0) -> None:
        self.kind, self.seconds = kind, seconds
