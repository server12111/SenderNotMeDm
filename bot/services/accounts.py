"""Подключение клиентских аккаунтов: телефон → код → (2FA) → зашифрованная StringSession в БД."""
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

from bot.config import settings
from bot.database.models import Account
from bot.services.crypto import decrypt, encrypt


def new_client(session: str = "") -> TelegramClient:
    return TelegramClient(StringSession(session), settings.api_id, settings.api_hash)


async def open_client(acc: Account) -> TelegramClient | None:
    """Подключённый клиент или None, если сессия больше не авторизована."""
    client = new_client(decrypt(acc.session_enc))
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        return None
    return client


@dataclass
class _Pending:
    client: TelegramClient
    phone: str
    code_hash: str


class LoginManager:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self.sm = sm
        self._pending: dict[int, _Pending] = {}

    async def start(self, owner_id: int, phone: str) -> None:
        await self.cancel(owner_id)
        client = new_client()
        await client.connect()
        try:
            sent = await client.send_code_request(phone)
        except Exception:
            await client.disconnect()
            raise
        self._pending[owner_id] = _Pending(client, phone, sent.phone_code_hash)

    async def submit_code(self, owner_id: int, code: str) -> Account | None:
        """Account — вход завершён; None — нужен пароль 2FA."""
        p = self._pending[owner_id]
        try:
            await p.client.sign_in(p.phone, code, phone_code_hash=p.code_hash)
        except SessionPasswordNeededError:
            return None
        return await self._finish(owner_id)

    async def submit_password(self, owner_id: int, password: str) -> Account:
        await self._pending[owner_id].client.sign_in(password=password)
        return await self._finish(owner_id)

    async def cancel(self, owner_id: int) -> None:
        if p := self._pending.pop(owner_id, None):
            await p.client.disconnect()

    async def _finish(self, owner_id: int) -> Account:
        p = self._pending.pop(owner_id)
        me = await p.client.get_me()
        session = encrypt(p.client.session.save())
        await p.client.disconnect()
        async with self.sm() as s:
            acc = await s.scalar(select(Account).where(Account.owner_id == owner_id, Account.tg_id == me.id))
            if acc:  # повторное подключение того же аккаунта обновляет сессию
                acc.session_enc, acc.status, acc.cooldown_until = session, "active", None
            else:
                acc = Account(owner_id=owner_id, tg_id=me.id, phone=p.phone,
                              username=me.username, session_enc=session)
                s.add(acc)
            await s.commit()
            return acc
