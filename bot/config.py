import os
from pathlib import Path
from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _default_data_dir() -> str:
    """На хостинге персистентна только /app/data; локально работаем в ./data."""
    posix_host = os.name != "nt" and Path("/app").is_dir() and os.access("/app", os.W_OK)
    return "/app/data" if posix_host else "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str
    admin_ids: Annotated[list[int], NoDecode] = []  # ADMIN_IDS=123,456 (или JSON-список)

    # Папка с данными (БД, медиа, ключ). Всё, что нужно пережить перезапуск, лежит здесь.
    data_dir: str = _default_data_dir()
    # Пусто = SQLite в data_dir. Для PostgreSQL: postgresql+asyncpg://user:pass@host:5432/db
    database_url: str = ""
    redis_url: str = ""  # пусто = FSM в памяти (состояния диалогов теряются при перезапуске)

    api_id: int = 0  # https://my.telegram.org; 0 = подключение аккаунтов отключено
    api_hash: str = ""
    session_key: str = ""  # Fernet-ключ шифрования сессий; пусто = генерируется в data_dir/session.key
    media_dir: str = ""

    @field_validator("api_id", mode="before")
    @classmethod
    def _empty_api_id(cls, v):
        return 0 if v in ("", None) else v

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, v):
        if isinstance(v, str):
            return [int(x) for x in v.replace("[", "").replace("]", "").replace(";", ",").split(",") if x.strip()]
        return v

    @model_validator(mode="after")
    def _fill_defaults(self):
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        if not self.database_url:
            self.database_url = f"sqlite+aiosqlite:///{Path(self.data_dir).resolve().as_posix()}/bot.db"
        if not self.media_dir:
            self.media_dir = str(Path(self.data_dir) / "media")
        return self

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def sqlite_path(self) -> str:
        return self.database_url.split("///", 1)[1]


settings = Settings()
