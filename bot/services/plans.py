"""Тарифы: эффективные лимиты пользователя, выдача и продление подписок, Free-лимиты из настроек."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Setting, Subscription

# ключ -> (значение по умолчанию, описание)
FREE_DEFAULTS = {
    "free_max_accounts": 1,
    "free_daily_limit": 20,
    "free_max_recipients": 50,
    "free_max_parse": 100,
}


@dataclass
class Limits:
    name: str
    max_accounts: int
    daily_limit: int
    max_recipients: int  # 0 = без лимита
    expires_at: datetime | None = None
    is_free: bool = False
    max_parse: int = 0  # контактов за один парсинг, 0 = без лимита


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_setting_int(session: AsyncSession, key: str) -> int:
    row = await session.get(Setting, key)
    try:
        return int(row.value) if row else FREE_DEFAULTS[key]
    except ValueError:
        return FREE_DEFAULTS[key]


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(Setting, key)
    if row:
        row.value = value
    else:
        session.add(Setting(key=key, value=value))
    await session.commit()


async def free_limits(session: AsyncSession) -> Limits:
    return Limits("Free", await get_setting_int(session, "free_max_accounts"),
                  await get_setting_int(session, "free_daily_limit"),
                  await get_setting_int(session, "free_max_recipients"), is_free=True,
                  max_parse=await get_setting_int(session, "free_max_parse"))


async def active_subscription(session: AsyncSession, user_id: int) -> Subscription | None:
    """Действующая подписка: самая щедрая по лимитам (оплаченное не «перебивается» более слабой вечной)."""
    return await session.scalar(
        select(Subscription).where(
            Subscription.user_id == user_id, Subscription.status == "active",
            (Subscription.expires_at.is_(None)) | (Subscription.expires_at > utcnow()))
        .order_by(Subscription.max_accounts.desc(), Subscription.daily_limit.desc(),
                  case((Subscription.expires_at.is_(None), 1), else_=0).desc(), Subscription.expires_at.desc())
        .limit(1))


async def get_limits(session: AsyncSession, user_id: int) -> Limits:
    sub = await active_subscription(session, user_id)
    if not sub:
        return await free_limits(session)
    return Limits(sub.plan_name, sub.max_accounts, sub.daily_limit, sub.max_recipients, sub.expires_at,
                  max_parse=sub.max_parse)


async def grant_subscription(session: AsyncSession, user_id: int, *, plan_name: str, days: int,
                             max_accounts: int, daily_limit: int, max_recipients: int, max_parse: int = 0,
                             plan_id: int | None = None, source: str = "admin", amount=0,
                             order_id: int | None = None, granted_by: int | None = None) -> Subscription:
    """Новая подписка. Продление ТОГО ЖЕ тарифа добавляет срок к действующему (старая помечается replaced).

    Другой тариф живёт параллельно со своим сроком; действует самый щедрый по лимитам (см. active_subscription),
    поэтому покупка не обнуляет остаток уже оплаченного.
    """
    now = utcnow()
    same = await session.scalar(
        select(Subscription).where(
            Subscription.user_id == user_id, Subscription.status == "active", Subscription.expires_at.is_not(None),
            Subscription.expires_at > now,
            (Subscription.plan_id == plan_id) if plan_id is not None else (Subscription.plan_name == plan_name))
        .order_by(Subscription.expires_at.desc()).limit(1))
    start = now
    if same:
        start = max(now, same.expires_at.replace(tzinfo=same.expires_at.tzinfo or timezone.utc))
        await session.execute(update(Subscription).where(Subscription.id == same.id).values(status="replaced"))
    sub = Subscription(
        user_id=user_id, plan_id=plan_id, plan_name=plan_name, max_accounts=max_accounts,
        daily_limit=daily_limit, max_recipients=max_recipients, max_parse=max_parse, source=source, amount=amount,
        order_id=order_id, granted_by=granted_by, starts_at=now,
        expires_at=None if days == 0 else start + timedelta(days=days))
    session.add(sub)
    await session.commit()
    return sub


async def revoke_subscriptions(session: AsyncSession, user_id: int) -> int:
    res = await session.execute(update(Subscription).where(
        Subscription.user_id == user_id, Subscription.status == "active").values(status="cancelled"))
    await session.commit()
    return res.rowcount


def limit_presets(cap: int) -> list[int]:
    """Варианты дневного лимита аккаунта, не выше потолка тарифа."""
    base = [n for n in (20, 40, 80, 150, 300) if n <= cap]
    return sorted(set(base) | {cap})[-4:]
