"""Один аккаунт — один подключённый клиент.

Если открыть одну и ту же сессию двумя клиентами сразу (например, парсер во время рассылки), Telegram может
аннулировать ключ авторизации (AuthKeyDuplicated), и аккаунт будет потерян. Поэтому задачи занимают аккаунт.
"""
_busy: dict[int, str] = {}


def acquire(account_id: int, who: str) -> bool:
    if account_id in _busy:
        return False
    _busy[account_id] = who
    return True


def release(account_id: int) -> None:
    _busy.pop(account_id, None)


def holder(account_id: int) -> str | None:
    return _busy.get(account_id)
