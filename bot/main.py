import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage

from bot.config import settings
from bot.database.base import make_sessionmaker
from bot.database.migrate import upgrade_to_head
from bot.handlers import accounts, billing, campaigns, fallback, leads, menu, parser
from bot.handlers.admin import catalog, notices, orders, panel
from bot.middlewares.db import DbMiddleware
from bot.services.accounts import LoginManager
from bot.services.broadcast import BroadcastManager
from bot.services.live import LiveUpdater, NoticeLive, ParserLive
from bot.services.notices import NoticeManager
from bot.services.parser import ParserManager
from bot.services.subscriptions import expiry_loop

log = logging.getLogger("bot")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info("data dir: %s | db: %s", settings.data_dir, "SQLite" if settings.is_sqlite else "PostgreSQL")
    if not settings.admin_ids:
        log.warning("ADMIN_IDS пуст: админ-панель никому не доступна")
    if not settings.api_id or not settings.api_hash:
        log.warning("API_ID/API_HASH не заданы: подключение аккаунтов отключено")

    await asyncio.to_thread(upgrade_to_head)  # схема БД всегда актуальна, данные не теряются

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode="HTML", link_preview_is_disabled=True))
    sm = make_sessionmaker(settings.database_url)

    storage = RedisStorage.from_url(settings.redis_url) if settings.redis_url else MemoryStorage()
    manager, notice_mgr = BroadcastManager(bot, sm), NoticeManager(bot, sm)
    live, notice_live = LiveUpdater(bot), NoticeLive(bot)
    dp = Dispatcher(storage=storage, manager=manager, logins=LoginManager(sm), live=live,
                    notices=notice_mgr, notice_live=notice_live, parsers=ParserManager(bot, sm),
                    parser_live=ParserLive(bot))
    dp.update.outer_middleware(DbMiddleware(sm))
    dp.include_routers(panel.router, notices.router, catalog.router, orders.router, menu.router, billing.router,
                       accounts.router, leads.router, parser.router, campaigns.router, fallback.router)

    await manager.recover(live)           # дослать рассылки, прерванные рестартом
    await notice_mgr.recover(notice_live)
    asyncio.create_task(expiry_loop(bot, sm))  # напоминания и закрытие истёкших подписок
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
