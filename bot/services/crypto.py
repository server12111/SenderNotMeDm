import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet

from bot.config import settings

log = logging.getLogger(__name__)


def _load_key() -> bytes:
    """SESSION_KEY из окружения; иначе ключ создаётся один раз и лежит в data_dir (переживает перезапуск)."""
    if settings.session_key:
        return settings.session_key.encode()
    path = Path(settings.data_dir) / "session.key"
    if not path.exists():
        path.write_bytes(Fernet.generate_key())
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        log.warning("SESSION_KEY не задан: создан %s. Лучше задать SESSION_KEY в переменных окружения "
                    "и не хранить ключ рядом с БД.", path)
    return path.read_bytes().strip()


_fernet = Fernet(_load_key())


def encrypt(value: str) -> str:
    return _fernet.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return _fernet.decrypt(value.encode()).decode()
