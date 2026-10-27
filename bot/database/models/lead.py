from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.database.base import Base


class ParsedLead(Base):
    """Получатель рассылки: спарсенный или импортированный владельцем. Адресуется по @username."""
    __tablename__ = "parsed_leads"
    __table_args__ = (
        UniqueConstraint("owner_id", "tag", "username", name="uq_parsed_leads_owner_tag_username"),
        Index("ix_parsed_leads_owner_tag_dead", "owner_id", "tag", "is_dead"),  # выборка получателей рассылки
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    tag: Mapped[str] = mapped_column(String(40))
    username: Mapped[str] = mapped_column(String(64))
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger)
    first_name: Mapped[str | None] = mapped_column(String(256))
    last_online: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_chat: Mapped[str | None] = mapped_column(String(256))
    is_dead: Mapped[bool] = mapped_column(default=False)  # недоступен: приватность/бан/нет такого юзернейма
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
