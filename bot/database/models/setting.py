from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from bot.database.base import Base


class Setting(Base):
    """Key-value настройки, которые админ меняет из бота (лимиты Free и т.п.)."""
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
