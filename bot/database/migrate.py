from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[2]


def upgrade_to_head() -> None:
    """Применить миграции. Синхронная: вызывать через asyncio.to_thread (env.py поднимает свой event loop)."""
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")
