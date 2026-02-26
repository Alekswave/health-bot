import asyncio
import logging
import os
from aiohttp import web

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode

# якщо у тебе є routers:
# from handlers import router

logging.basicConfig(level=logging.INFO)

PORT = int(os.getenv("PORT", "10000"))
TOKEN = os.getenv("BOT_TOKEN")

dp = Dispatcher()
# dp.include_router(router)

async def health_app():
    app = web.Application()

    async def health(request):
        return web.Response(text="ok")

    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    return app

async def run_web():
    app = await health_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"Health server started on port {PORT}")

    # тримаємо веб-сервер живим
    while True:
        await asyncio.sleep(3600)

async def run_polling():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is empty. Check Render Environment.")

    bot = Bot(token=TOKEN, parse_mode=ParseMode.HTML)

    # ключове: вимикаємо webhook, щоб polling працював
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Webhook deleted. Polling started.")

    await dp.start_polling(bot)

async def main():
    await asyncio.gather(
        run_web(),
        run_polling()
    )

if __name__ == "__main__":
    asyncio.run(main())