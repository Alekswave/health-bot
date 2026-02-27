import asyncio
import logging
import os
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# -------------------------------
# Logging
# -------------------------------
logging.basicConfig(level=logging.INFO)

# -------------------------------
# ENV
# -------------------------------
TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))

if not TOKEN:
    raise RuntimeError("BOT_TOKEN not found in environment variables")

# -------------------------------
# Bot + Dispatcher (aiogram 3.7+)
# -------------------------------
bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()


# -------------------------------
# Handlers
# -------------------------------

@dp.message(F.text == "/start")
async def start_handler(message: Message):
    await message.answer("👋 Бот запущений та працює!")


@dp.message()
async def echo_handler(message: Message):
    await message.answer(f"Ти написав: {message.text}")


# -------------------------------
# Health server for Render
# -------------------------------
async def health(request):
    return web.Response(text="OK")


async def start_health_server():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logging.info(f"Health server started on port {PORT}")

    # тримаємо сервер живим
    while True:
        await asyncio.sleep(3600)


# -------------------------------
# Polling
# -------------------------------
async def start_polling():
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Webhook deleted. Starting polling...")
    await dp.start_polling(bot)


# -------------------------------
# Main
# -------------------------------
async def main():
    await asyncio.gather(
        start_health_server(),
        start_polling()
    )


if __name__ == "__main__":
    asyncio.run(main())