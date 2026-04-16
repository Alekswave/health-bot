import os
import re
import logging
import aiosqlite
from datetime import datetime, timedelta, timezone
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

DB_PATH = "health.db"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()

bp_regex = re.compile(r"^\s*(\d{2,3})\s*/\s*(\d{2,3})\s+(\d{2,3})\s*$")

# =============================
# DB
# =============================
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    systolic INTEGER NOT NULL,
    diastolic INTEGER NOT NULL,
    pulse INTEGER NOT NULL,
    created_at TEXT NOT NULL
)
"""


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(CREATE_TABLE_SQL)
        await db.commit()


async def save_measurement(user_id: int, systolic: int, diastolic: int, pulse: int):
    now_utc = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO measurements (user_id, systolic, diastolic, pulse, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, systolic, diastolic, pulse, now_utc)
        )
        await db.commit()


async def get_last_measurements(user_id: int, limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT systolic, diastolic, pulse, created_at
            FROM measurements
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit)
        )
        rows = await cursor.fetchall()
        return rows


async def get_measurements_for_days(user_id: int, days: int = 7):
    since = datetime.now(timezone.utc) - timedelta(days=days)

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT systolic, diastolic, pulse, created_at
            FROM measurements
            WHERE user_id = ? AND created_at >= ?
            ORDER BY id ASC
            """,
            (user_id, since.isoformat())
        )
        rows = await cursor.fetchall()
        return rows


# =============================
# HELPERS
# =============================
def classify_pressure(systolic: int, diastolic: int) -> str:
    if systolic >= 180 or diastolic >= 120:
        return "🚨 Дуже високий тиск"
    if systolic >= 140 or diastolic >= 90:
        return "⚠️ Підвищений тиск"
    if systolic < 90 or diastolic < 60:
        return "⚠️ Знижений тиск"
    return "✅ Тиск у межах норми"


def classify_pulse(pulse: int) -> str:
    if pulse >= 120:
        return "⚠️ Дуже високий пульс"
    if pulse >= 100:
        return "⚠️ Пульс підвищений"
    if pulse < 50:
        return "⚠️ Пульс знижений"
    return "✅ Пульс у межах норми"


def format_local_time(iso_str: str) -> str:
    dt = datetime.fromisoformat(iso_str)
    dt_local = dt.astimezone(timezone(timedelta(hours=2)))
    return dt_local.strftime("%d.%m.%Y %H:%M")


# =============================
# HANDLERS
# =============================
@dp.message(Command("start"))
async def start_handler(message: Message):
    await message.answer(
        "👋 <b>Привіт!</b>\n\n"
        "Я бот для моніторингу тиску.\n\n"
        "Надішли показники у форматі:\n"
        "<b>120/80 68</b>\n\n"
        "де:\n"
        "120 — верхній тиск\n"
        "80 — нижній тиск\n"
        "68 — пульс\n\n"
        "Команди:\n"
        "/history — останні 10 записів\n"
        "/stats — статистика за 7 днів"
    )


@dp.message(Command("history"))
async def history_handler(message: Message):
    rows = await get_last_measurements(message.from_user.id, limit=10)

    if not rows:
        await message.answer("Поки що немає збережених вимірювань.")
        return

    lines = ["<b>Останні 10 вимірювань:</b>\n"]
    for systolic, diastolic, pulse, created_at in rows:
        lines.append(
            f"• {format_local_time(created_at)} — "
            f"<b>{systolic}/{diastolic}</b>, пульс <b>{pulse}</b>"
        )

    await message.answer("\n".join(lines))


@dp.message(Command("stats"))
async def stats_handler(message: Message):
    rows = await get_measurements_for_days(message.from_user.id, days=7)

    if not rows:
        await message.answer("За останні 7 днів немає даних.")
        return

    systolic_values = [row[0] for row in rows]
    diastolic_values = [row[1] for row in rows]
    pulse_values = [row[2] for row in rows]

    avg_sys = sum(systolic_values) / len(systolic_values)
    avg_dia = sum(diastolic_values) / len(diastolic_values)
    avg_pulse = sum(pulse_values) / len(pulse_values)

    text = (
        "<b>Статистика за 7 днів:</b>\n\n"
        f"• Кількість записів: <b>{len(rows)}</b>\n"
        f"• Середній тиск: <b>{avg_sys:.0f}/{avg_dia:.0f}</b>\n"
        f"• Мін. SYS: <b>{min(systolic_values)}</b>\n"
        f"• Макс. SYS: <b>{max(systolic_values)}</b>\n"
        f"• Мін. DIA: <b>{min(diastolic_values)}</b>\n"
        f"• Макс. DIA: <b>{max(diastolic_values)}</b>\n"
        f"• Середній пульс: <b>{avg_pulse:.0f}</b>"
    )

    await message.answer(text)


@dp.message(F.text)
async def handle_text(message: Message):
    text = (message.text or "").strip()

    if text.startswith("/"):
        return

    match = bp_regex.match(text)
    if not match:
        await message.answer("❗ Формат: <b>120/80 68</b>")
        return

    systolic = int(match.group(1))
    diastolic = int(match.group(2))
    pulse = int(match.group(3))

    if not (60 <= systolic <= 260):
        await message.answer("⚠️ Некоректний верхній тиск")
        return

    if not (40 <= diastolic <= 160):
        await message.answer("⚠️ Некоректний нижній тиск")
        return

    if diastolic >= systolic:
        await message.answer("⚠️ Нижній тиск не може бути ≥ верхнього")
        return

    if not (35 <= pulse <= 220):
        await message.answer("⚠️ Некоректний пульс")
        return

    await save_measurement(message.from_user.id, systolic, diastolic, pulse)

    pressure_status = classify_pressure(systolic, diastolic)
    pulse_status = classify_pulse(pulse)

    await message.answer(
        f"✅ <b>Дані збережено</b>\n\n"
        f"Тиск: <b>{systolic}/{diastolic}</b>\n"
        f"Пульс: <b>{pulse}</b>\n\n"
        f"{pressure_status}\n"
        f"{pulse_status}\n\n"
        f"Доступно:\n"
        f"/history\n"
        f"/stats"
    )


# =============================
# WEB SERVER (Render)
# =============================
async def health(request):
    return web.Response(text="ok")


async def on_startup(app: web.Application):
    logger.info("Starting bot...")

    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(WEBHOOK_URL)

    logger.info(f"Webhook set to: {WEBHOOK_URL}")


async def on_shutdown(app: web.Application):
    logger.info("Shutting down bot...")
    await bot.session.close()
    # webhook НЕ видаляємо


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