from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.database.base import Base


class Plan(Base):
    """Платный тариф. Лимиты копируются в подписку при выдаче, поэтому правка тарифа не ломает купленное."""
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))  # USD
    duration_days: Mapped[int] = mapped_column(default=30)  # 0 = навсегда
    max_accounts: Mapped[int] = mapped_column(default=3)
    daily_limit: Mapped[int] = mapped_column(default=100)  # потолок сообщений в сутки на один аккаунт
    max_recipients: Mapped[int] = mapped_column(default=0)  # получателей в одной рассылке; 0 = без лимита
    max_parse: Mapped[int] = mapped_column(default=0, server_default="0")  # контактов за один парсинг; 0 = без лимита
    is_active: Mapped[bool] = mapped_column(default=True)  # скрытый тариф не продаётся
    sort: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
