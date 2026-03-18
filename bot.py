import os
import re
import logging
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# =============================
# CONFIG
# =============================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

PORT = int(os.getenv("PORT", "10000"))

RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
if not RENDER_EXTERNAL_URL:
    raise RuntimeError("RENDER_EXTERNAL_URL is not set")

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()

bp_regex = re.compile(r"^\s*(\d{2,3})\s*/\s*(\d{2,3})\s+(\d{2,3})\s*$")

# =============================
# HANDLERS
# =============================
@dp.message(Command("start"))
async def start_handler(message: Message):
    await message.answer(
        "👋 Привіт!\n\n"
        "Надішли показники у форматі:\n"
        "<b>120/80 68</b>\n\n"
        "де:\n"
        "120 — верхній тиск\n"
        "80 — нижній тиск\n"
        "68 — пульс"
    )


@dp.message(F.text)
async def handle_text(message: Message):
    text = (message.text or "").strip()

    if text.startswith("/"):
        return

    match = bp_regex.match(text)
    if not match:
        await message.answer("❗ Формат: <b>120/80 68</b>")
        return

    sys = int(match.group(1))
    dia = int(match.group(2))
    pulse = int(match.group(3))

    if not (60 <= sys <= 260):
        await message.answer("⚠️ Некоректний верхній тиск")
        return

    if not (40 <= dia <= 160):
        await message.answer("⚠️ Некоректний нижній тиск")
        return

    if dia >= sys:
        await message.answer("⚠️ Нижній тиск не може бути ≥ верхнього")
        return

    if not (35 <= pulse <= 220):
        await message.answer("⚠️ Некоректний пульс")
        return

    await message.answer(
        f"✅ Дані прийнято:\n\n"
        f"Тиск: <b>{sys}/{dia}</b>\n"
        f"Пульс: <b>{pulse}</b>"
    )

# =============================
# WEB SERVER (Render)
# =============================
async def health(request):
    return web.Response(text="ok")


async def on_startup(app: web.Application):
    logger.info("Starting bot...")

    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(WEBHOOK_URL)

    logger.info(f"Webhook set to: {WEBHOOK_URL}")


async def on_shutdown(app: web.Application):
    logger.info("Shutting down bot...")
    await bot.session.close()
    # ❗ НЕ видаляємо webhook!


def main():
    app = web.Application()

    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot
    )
    webhook_handler.register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()