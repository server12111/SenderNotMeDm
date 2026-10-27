import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.database.base import Base


class DeliveryStatus(str, enum.Enum):
    SENT = "sent"
    BLOCKED = "blocked"  # получатель недоступен: приватность, бан, нет юзернейма
    FAILED = "failed"


class BroadcastLog(Base):
    __tablename__ = "broadcast_logs"
    __table_args__ = (UniqueConstraint("campaign_id", "lead_id", name="uq_broadcast_logs_campaign_lead"),)  # защита от дублей при resume

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("parsed_leads.id", ondelete="CASCADE"))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(String(256))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
