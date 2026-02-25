import os
import re
import asyncio
import sqlite3
from datetime import datetime, timezone

from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties

from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application


# =========================
# ENV
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in environment variables")

PORT = int(os.getenv("PORT", "10000"))

# Render часто підставляє зовнішній URL як RENDER_EXTERNAL_URL.
# Якщо раптом його нема — задай WEBHOOK_BASE_URL вручну у Render.
WEBHOOK_BASE_URL = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("WEBHOOK_BASE_URL")
if not WEBHOOK_BASE_URL:
    raise ValueError("WEBHOOK_BASE_URL (or RENDER_EXTERNAL_URL) is not set")

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = WEBHOOK_BASE_URL.rstrip("/") + WEBHOOK_PATH


# =========================
# DB (SQLite)
# =========================
DB_PATH = os.getenv("DB_PATH", "health.db")


def _db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS measurements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            dt_utc TEXT NOT NULL,
            sys INTEGER NOT NULL,
            dia INTEGER NOT NULL,
            pulse INTEGER NOT NULL
        )
        """
    )
    conn.commit()
    return conn


DB = _db_connect()


async def db_execute(sql: str, params: tuple = ()) -> None:
    def _run():
        cur = DB.cursor()
        cur.execute(sql, params)
        DB.commit()

    await asyncio.to_thread(_run)


async def db_fetchall(sql: str, params: tuple = ()) -> list[tuple]:
    def _run():
        cur = DB.cursor()
        cur.execute(sql, params)
        return cur.fetchall()

    return await asyncio.to_thread(_run)


async def db_fetchone(sql: str, params: tuple = ()) -> tuple | None:
    def _run():
        cur = DB.cursor()
        cur.execute(sql, params)
        return cur.fetchone()

    return await asyncio.to_thread(_run)


# =========================
# ANALYSIS LOGIC
# =========================
def bp_category(sys: int, dia: int) -> str:
    if sys >= 180 or dia >= 120:
        return "🚨 *Дуже високий тиск (можливий криз)*"
    if sys >= 160 or dia >= 100:
        return "🔴 *Гіпертензія 2 ступеня*"
    if sys >= 140 or dia >= 90:
        return "🟠 *Гіпертензія 1 ступеня*"
    if sys >= 130 or dia >= 85:
        return "🟡 *Високий нормальний*"
    if sys >= 120 and dia < 80:
        return "🟢 *Нормальний*"
    if sys < 120 and dia < 80:
        return "🟢 *Оптимальний*"
    return "ℹ️ *Змішаний діапазон*"


def pulse_comment(pulse: int) -> str:
    if pulse >= 120:
        return "🚨 *Дуже високий пульс*"
    if pulse >= 100:
        return "🟠 *Тахікардія (підвищений пульс)*"
    if pulse < 50:
        return "🟡 *Низький пульс (брадикардія)*"
    return "🟢 *Пульс у межах норми*"


def urgent_flags(sys: int, dia: int, pulse: int) -> list[str]:
    flags = []
    if sys >= 180 or dia >= 120:
        flags.append("🚨 Тиск дуже високий. Якщо є біль у грудях/задишка/сильний головний біль/оніміння/порушення мови — *викликай 103*.")
    if sys < 90 or dia < 60:
        flags.append("⚠️ Тиск низький. Якщо є запаморочення/слабкість/непритомність — краще прилягти, вода, контроль повторно.")
    if pulse >= 120:
        flags.append("🚨 Пульс дуже високий. Якщо є біль у грудях/задишка/запаморочення — *невідкладно* звернись по допомогу.")
    return flags


def trend_comment(rows: list[tuple]) -> str | None:
    if len(rows) < 3:
        return None
    s1, d1 = rows[0][1], rows[0][2]
    s3, d3 = rows[2][1], rows[2][2]
    ds = s1 - s3
    dd = d1 - d3
    if ds >= 10 or dd >= 6:
        return "📈 Є тенденція *до підвищення* за останні 3 вимірювання."
    if ds <= -10 or dd <= -6:
        return "📉 Є тенденція *до зниження* за останні 3 вимірювання."
    return "➖ Без помітної тенденції за останні 3 вимірювання."


def short_reco(sys: int, dia: int, pulse: int) -> str:
    if sys >= 140 or dia >= 90:
        return (
            "Рекомендації:\n"
            "• Посиди/відпочинь 5–10 хв, повтори вимір.\n"
            "• Уникай кофеїну/куріння найближчі 2–3 год.\n"
            "• Якщо тримається підвищеним — краще узгодити план з лікарем."
        )
    if sys < 90 or dia < 60:
        return (
            "Рекомендації:\n"
            "• Вода, спокій, повільно змінюй положення тіла.\n"
            "• Повтори вимір через 10–15 хв.\n"
            "• Якщо є симптоми — консультація лікаря."
        )
    if pulse >= 100:
        return (
            "Рекомендації:\n"
            "• Відпочинь 5–10 хв, дихання повільне.\n"
            "• Перевір, чи не було кофеїну/стресу/фізнавантаження.\n"
            "• Якщо часто повторюється — варто обговорити з лікарем."
        )
    return (
        "Рекомендації:\n"
        "• Продовжуй регулярні виміри (бажано в один і той самий час).\n"
        "• Для точності: 5 хв спокою, рука на рівні серця, 2 виміри."
    )


# =========================
# PARSING
# =========================
MEASURE_RE = re.compile(r"^\s*(\d{2,3})\s*/\s*(\d{2,3})\s+(\d{2,3})\s*$")


def parse_measurement(text: str) -> tuple[int, int, int] | None:
    m = MEASURE_RE.match(text)
    if not m:
        return None
    sys = int(m.group(1))
    dia = int(m.group(2))
    pulse = int(m.group(3))
    if not (60 <= sys <= 260):
        return None
    if not (40 <= dia <= 160):
        return None
    if not (30 <= pulse <= 220):
        return None
    return sys, dia, pulse


# =========================
# BOT SETUP
# =========================
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
)
dp = Dispatcher()


# =========================
# HANDLERS
# =========================
@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Привіт 👋\n\n"
        "Введи показники у форматі:\n"
        "`120/80 70`\n"
        "де `120/80` — тиск, `70` — пульс.\n\n"
        "Команди:\n"
        "• /last — останній запис\n"
        "• /history — 10 останніх\n"
        "• /clear — очистити історію\n"
        "• /help — допомога"
    )


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "Як користуватись:\n"
        "1) Надішли: `120/80 70`\n"
        "2) Отримаєш збереження + базовий аналіз\n\n"
        "Команди:\n"
        "• /last\n"
        "• /history\n"
        "• /clear"
    )


@dp.message(Command("clear"))
async def cmd_clear(message: Message) -> None:
    user_id = message.from_user.id
    await db_execute("DELETE FROM measurements WHERE user_id = ?", (user_id,))
    await message.answer("🧹 Історію очищено.")


@dp.message(Command("last"))
async def cmd_last(message: Message) -> None:
    user_id = message.from_user.id
    row = await db_fetchone(
        "SELECT dt_utc, sys, dia, pulse FROM measurements WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    if not row:
        await message.answer("Поки що немає записів. Надішли показники у форматі `120/80 70`.")
        return
    dt_utc, sys, dia, pulse = row
    await message.answer(
        f"*Останній запис:*\n"
        f"• Час (UTC): `{dt_utc}`\n"
        f"• Тиск: *{sys}/{dia}*\n"
        f"• Пульс: *{pulse}*"
    )


@dp.message(Command("history"))
async def cmd_history(message: Message) -> None:
    user_id = message.from_user.id
    rows = await db_fetchall(
        "SELECT dt_utc, sys, dia, pulse FROM measurements WHERE user_id = ? ORDER BY id DESC LIMIT 10",
        (user_id,),
    )
    if not rows:
        await message.answer("Поки що немає історії. Надішли показники у форматі `120/80 70`.")
        return

    lines = ["*Останні 10 записів:*"]
    for dt_utc, sys, dia, pulse in rows:
        lines.append(f"• `{dt_utc}` — *{sys}/{dia}*  pulse *{pulse}*")
    await message.answer("\n".join(lines))


@dp.message(F.text)
async def on_text(message: Message) -> None:
    user_id = message.from_user.id
    parsed = parse_measurement(message.text or "")
    if not parsed:
        await message.answer(
            "Не зрозумів формат 😅\n"
            "Спробуй так: `120/80 70`\n"
            "Команди: /help /last /history /clear"
        )
        return

    sys, dia, pulse = parsed
    dt_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    await db_execute(
        "INSERT INTO measurements(user_id, dt_utc, sys, dia, pulse) VALUES(?,?,?,?,?)",
        (user_id, dt_utc, sys, dia, pulse),
    )

    last_rows = await db_fetchall(
        "SELECT dt_utc, sys, dia, pulse FROM measurements WHERE user_id = ? ORDER BY id DESC LIMIT 10",
        (user_id,),
    )

    cat = bp_category(sys, dia)
    pcom = pulse_comment(pulse)
    flags = urgent_flags(sys, dia, pulse)
    trend = trend_comment(last_rows)
    reco = short_reco(sys, dia, pulse)

    parts = [
        "✅ *Дані збережено*",
        f"• Тиск: *{sys}/{dia}*",
        f"• Пульс: *{pulse}*",
        "",
        f"{cat}",
        f"{pcom}",
    ]

    if trend:
        parts.append(trend)

    if flags:
        parts.append("")
        parts.append("*Увага:*")
        parts.extend([f"• {f}" for f in flags])

    parts.append("")
    parts.append(reco)

    await message.answer("\n".join(parts))


# =========================
# AIOHTTP APP + WEBHOOK
# =========================
async def handle_root(request: web.Request) -> web.Response:
    return web.Response(text="OK")


async def on_startup(app: web.Application) -> None:
    # Ставимо webhook (Telegram буде штовхати апдейти на /webhook)
    await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
    print(f"Webhook set to: {WEBHOOK_URL}")


async def on_shutdown(app: web.Application) -> None:
    await bot.delete_webhook()
    await bot.session.close()


def main() -> None:
    app = web.Application()
    app.router.add_get("/", handle_root)

    # Прив'язка aiogram до aiohttp
    request_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    request_handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()