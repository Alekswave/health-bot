import os
import asyncio
import logging
import threading

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message


# ----------------------------
# Telegram bot logic
# ----------------------------

async def main():
    token = os.getenv("BOT_TOKEN")

    if not token:
        raise ValueError("BOT_TOKEN is not set in environment variables")

    bot = Bot(token=token)
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

    logging.info("Bot started polling...")
    await dp.start_polling(bot)


# ----------------------------
# Health check server for Render
# ----------------------------

async def health_check(request):
    return web.Response(text="OK")


def start_health_server():
    app = web.Application()
    app.router.add_get("/", health_check)

    port = int(os.environ.get("PORT", 10000))
    web.run_app(app, port=port)


# ----------------------------
# Run both services
# ----------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # запускаємо web сервер у окремому потоці
    threading.Thread(target=start_health_server).start()

    # запускаємо Telegram бота
    asyncio.run(main())