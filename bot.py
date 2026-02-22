import os
import asyncio
import logging

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message


# ----------------------------
# Aiohttp health server (Render needs open PORT)
# ----------------------------
async def health_check(request: web.Request) -> web.Response:
    return web.Response(text="OK")


async def start_health_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.environ.get("PORT", "10000"))
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()

    logging.info("Health server started on port %s", port)
    return runner


# ----------------------------
# Telegram bot logic (aiogram 3)
# ----------------------------
def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def start_handler(message: Message):
        await message.answer(
            "Привіт 👋\n\n"
            "Введи показники у форматі:\n"
            "120/80 70\n\n"
            "де 120/80 — тиск, 70 — пульс"
        )

    @dp.message()
    async def data_handler(message: Message):
        await message.answer(f"Отримано дані: {message.text}")

    return dp


async def main():
    logging.basicConfig(level=logging.INFO)

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN is not set in environment variables")

    # 1) Start health server first (opens PORT for Render Web Service)
    runner = await start_health_server()

    # 2) Start bot polling
    bot = Bot(token=token)
    dp = build_dispatcher()

    try:
        logging.info("Bot started polling...")
        await dp.start_polling(bot)
    finally:
        # graceful shutdown
        await bot.session.close()
        await runner.cleanup()
        logging.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())