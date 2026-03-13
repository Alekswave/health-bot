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

# -----------------------------
# CONFIG
# -----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in Render Environment Variables")

PORT = int(os.getenv("PORT", "10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")

if not RENDER_EXTERNAL_URL:
    raise RuntimeError("RENDER_EXTERNAL_URL is not available")

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


# -----------------------------
# HANDLERS
# -----------------------------
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привіт! Я бот для моніторингу тиску 😊\n\n"
        "Надішли показники у форматі:\n"
        "<b>120/80 68</b>\n\n"
        "де 120 — систолічний,\n"
        "80 — діастолічний,\n"
        "68 — пульс."
    )


@dp.message(F.text)
async def handle_text(message: Message):
    text = (message.text or "").strip()

    # якщо користувач ввів іншу команду
    if text.startswith("/"):
        return

    match = bp_regex.match(text)
    if not match:
        await message.answer("Не впізнав формат 😅\nСпробуй так: <b>120/80 68</b>")
        return

    systolic = int(match.group(1))
    diastolic = int(match.group(2))
    pulse = int(match.group(3))

    if not (60 <= systolic <= 260):
        await message.answer("Систолічний тиск виглядає некоректно.")
        return
    if not (40 <= diastolic <= 160):
        await message.answer("Діастолічний тиск виглядає некоректно.")
        return
    if diastolic >= systolic:
        await message.answer("Діастолічний не може бути більшим або рівним систолічному.")
        return
    if not (35 <= pulse <= 220):
        await message.answer("Пульс виглядає некоректно.")
        return

    await message.answer(
        f"✅ Дані отримано:\n\n"
        f"Тиск: <b>{systolic}/{diastolic}</b>\n"
        f"Пульс: <b>{pulse}</b>"
    )


# -----------------------------
# WEB / RENDER
# -----------------------------
async def health_handler(request: web.Request):
    return web.Response(text="ok")


async def on_startup(app: web.Application):
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook set to: {WEBHOOK_URL}")


async def on_shutdown(app: web.Application):
    await bot.delete_webhook()
    await bot.session.close()


def main():
    app = web.Application()

    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)

    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()