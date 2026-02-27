import os
import re
import asyncio
import logging
from aiohttp import web
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties


load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set. Add it to .env locally and to Render Environment Variables.")

PORT = int(os.getenv("PORT", "10000"))  # Render gives PORT; locally fallback 10000

logging.basicConfig(level=logging.INFO)

router = Router()

# Регулярка для формату: "120/80 68" або "120/80,68" або "120/80  68"
BP_RE = re.compile(r"^\s*(\d{2,3})\s*/\s*(\d{2,3})\s*[, ]\s*(\d{2,3})\s*$")


@router.message(CommandStart())
async def cmd_start(message: Message):
    text = (
        "Привіт! Я бот для моніторингу тиску 😊\n\n"
        "Надішли показники у форматі:\n"
        "`120/80 68`\n\n"
        "де 120 — систолічний, 80 — діастолічний, 68 — пульс."
    )
    await message.answer(text, parse_mode=ParseMode.MARKDOWN)


@router.message(F.text)
async def handle_text(message: Message):
    txt = message.text.strip()

    # Ігноруємо інші команди, щоб не відповідати "Ти написав..." на /help тощо
    if txt.startswith("/"):
        return

    m = BP_RE.match(txt)
    if not m:
        await message.answer(
            "Не розпізнав формат 🤔\n"
            "Спробуй так: `120/80 68`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    sys_bp = int(m.group(1))
    dia_bp = int(m.group(2))
    pulse = int(m.group(3))

    # Проста валідація (можеш підкрутити під себе)
    if not (60 <= sys_bp <= 260 and 40 <= dia_bp <= 160 and 30 <= pulse <= 220):
        await message.answer(
            "Значення виглядають нетипово. Перевір, будь ласка, і спробуй ще раз у форматі `120/80 68`.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    await message.answer(
        f"✅ Записано:\n"
        f"• Тиск: *{sys_bp}/{dia_bp}*\n"
        f"• Пульс: *{pulse}*",
        parse_mode=ParseMode.MARKDOWN
    )


# --- Health server for Render (щоб сервіс не падав через відсутність порту) ---
async def health(request):
    return web.Response(text="ok")


async def start_health_server():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"Health server started on port {PORT}")


async def main():
    bot = Bot(
        token=TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)  # дефолтний parse_mode; в answer можна перевизначати
    )

    dp = Dispatcher()
    dp.include_router(router)

    await start_health_server()

    # На всякий випадок: видаляємо вебхук, щоб polling точно працював
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Webhook deleted. Starting polling...")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())