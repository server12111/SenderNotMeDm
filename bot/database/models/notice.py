from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.database.base import Base


class Notice(Base):
    """Рассылка админа по пользователям бота (от имени самого бота)."""
    __tablename__ = "notices"

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(String(128))
    text: Mapped[str] = mapped_column(Text, default="")  # HTML
    media: Mapped[list] = mapped_column(JSON, default=list)  # не более одного: [{"type","file_id"}]
    buttons: Mapped[list] = mapped_column(JSON, default=list)  # настоящие инлайн-URL кнопки
    segment: Mapped[str] = mapped_column(String(16), default="all")  # all / paid / free
    status: Mapped[str] = mapped_column(String(12), default="draft", index=True)
    total: Mapped[int] = mapped_column(default=0)
    sent: Mapped[int] = mapped_column(default=0)
    blocked: Mapped[int] = mapped_column(default=0)
    failed: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NoticeLog(Base):
    __tablename__ = "notice_logs"
    __table_args__ = (UniqueConstraint("notice_id", "user_id", name="uq_notice_logs_notice_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    notice_id: Mapped[int] = mapped_column(ForeignKey("notices.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(12))
    error: Mapped[str | None] = mapped_column(String(256))
