import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from bot.config import settings
from bot.database import models  # noqa: F401  (регистрация моделей в metadata)
from bot.database.base import Base

target_metadata = Base.metadata


def _configure(connection=None, url=None) -> None:
    context.configure(
        connection=connection, url=url, target_metadata=target_metadata, literal_binds=connection is None,
        render_as_batch=settings.is_sqlite,  # SQLite не умеет ALTER для большинства операций: пересоздаём таблицу
        compare_type=True,
    )


def run_migrations_offline() -> None:
    _configure(url=settings.database_url)
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as conn:
        await conn.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
