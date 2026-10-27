import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.database.base import Base


class OrderStatus(str, enum.Enum):
    NEW = "new"              # создана, пользователь ещё не оплатил
    PAID = "paid"            # пользователь нажал «Я оплатил» и ждёт проверки админом
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class PaymentMethod(Base):
    """Способ оплаты: реквизиты и курс задаёт админ."""
    __tablename__ = "payment_methods"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(16), default="USDT")
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=1)  # единиц валюты за 1 USD
    details: Mapped[str] = mapped_column(Text, default="")  # адрес кошелька / номер карты
    is_enabled: Mapped[bool] = mapped_column(default=True)
    sort: Mapped[int] = mapped_column(default=0)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_status_created", "status", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plans.id", ondelete="SET NULL"))
    # Снимок тарифа и реквизитов на момент заявки
    plan_name: Mapped[str] = mapped_column(String(64))
    days: Mapped[int] = mapped_column(default=30)
    max_accounts: Mapped[int] = mapped_column(default=1)
    daily_limit: Mapped[int] = mapped_column(default=20)
    max_recipients: Mapped[int] = mapped_column(default=0)
    price_usd: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    method_id: Mapped[int | None] = mapped_column(ForeignKey("payment_methods.id", ondelete="SET NULL"))
    method_title: Mapped[str] = mapped_column(String(64))
    amount_text: Mapped[str] = mapped_column(String(64))  # «41.50 USDT»
    details: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[str] = mapped_column(String(12), default=OrderStatus.NEW.value)
    admin_msgs: Mapped[list] = mapped_column(JSON, default=list)  # [[chat_id, message_id], ...] уведомления админам
    decided_by: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
