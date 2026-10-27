import enum
from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Float, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bot.database.base import Base


class CampaignStatus(str, enum.Enum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    DONE = "done"


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (Index("ix_campaigns_owner_status", "owner_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default=CampaignStatus.DRAFT.value, index=True)

    text: Mapped[str] = mapped_column(Text, default="")  # HTML
    media: Mapped[list] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), default=list)  # [{"type": "photo", "file_id": "..."}]
    buttons: Mapped[list] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), default=list)  # дописываются в текст ссылками

    tag: Mapped[str] = mapped_column(String(40))  # база получателей (ParsedLead.tag владельца)
    delay_min: Mapped[float] = mapped_column(Float, default=5.0)
    delay_max: Mapped[float] = mapped_column(Float, default=15.0)
    per_minute: Mapped[int] = mapped_column(default=4)  # на один аккаунт

    limit: Mapped[int | None] = mapped_column()  # потолок получателей по тарифу; NULL = без лимита
    total: Mapped[int] = mapped_column(default=0)
    sent: Mapped[int] = mapped_column(default=0)
    failed: Mapped[int] = mapped_column(default=0)
    blocked: Mapped[int] = mapped_column(default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
