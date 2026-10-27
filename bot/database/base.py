from sqlalchemy import MetaData, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from bot.config import settings

if settings.is_sqlite:
    from sqlalchemy.dialects.sqlite import insert  # noqa: F401
else:
    from sqlalchemy.dialects.postgresql import insert  # noqa: F401

# Стабильные имена констрейнтов нужны Alembic для корректных миграций
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def make_engine(url: str):
    if url.startswith("sqlite"):
        engine = create_async_engine(url, connect_args={"timeout": 30})

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")      # читатели не блокируют писателя
            cur.execute("PRAGMA synchronous=NORMAL")    # безопасно с WAL, заметно быстрее FULL
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=30000")    # ждать блокировку, а не падать с «database is locked»
            cur.close()
        return engine
    return create_async_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20, pool_recycle=1800)


def make_sessionmaker(url: str) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(make_engine(url), expire_on_commit=False)
