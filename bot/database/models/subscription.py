from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.database.base import Base


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        Index("ix_subscriptions_user_status_exp", "user_id", "status", "expires_at"),
        Index("ix_subscriptions_expiry", "status", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plans.id", ondelete="SET NULL"))
    plan_name: Mapped[str] = mapped_column(String(64))
    # Снимок лимитов на момент выдачи
    max_accounts: Mapped[int] = mapped_column(default=1)
    daily_limit: Mapped[int] = mapped_column(default=20)
    max_recipients: Mapped[int] = mapped_column(default=0)

    source: Mapped[str] = mapped_column(String(16), default="admin")  # order / admin
    status: Mapped[str] = mapped_column(String(12), default="active")  # active / expired / cancelled / replaced
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)  # USD
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"))
    granted_by: Mapped[int | None] = mapped_column(BigInteger)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # NULL = навсегда
    notified_3d: Mapped[bool] = mapped_column(default=False)
    notified_1d: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
