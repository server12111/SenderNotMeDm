from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.database.base import Base


class Account(Base):
    """Telegram-аккаунт клиента, с которого идёт рассылка (Telethon StringSession, зашифрована)."""
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("owner_id", "tg_id", name="uq_accounts_owner_tg"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)  # User.id владельца
    tg_id: Mapped[int] = mapped_column(BigInteger)
    phone: Mapped[str] = mapped_column(String(32))
    username: Mapped[str | None] = mapped_column(String(64))
    session_enc: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / limited / dead
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    daily_limit: Mapped[int] = mapped_column(default=40)
    sent_today: Mapped[int] = mapped_column(default=0)
    sent_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
