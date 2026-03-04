import asyncio
import io
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, BufferedInputFile

# ---------------------------
# CONFIG
# ---------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set. Add it to Render Environment variables.")

# Render sets PORT for Web Service
PORT = int(os.getenv("PORT", "10000"))

# DB path (Render free can be ephemeral)
DB_PATH = os.getenv("DB_PATH", "health.db")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bp_regex = re.compile(
    r"^\s*(\d{2,3})\s*(?:/|\s)\s*(\d{2,3})\s+(?:p\s*)?(\d{2,3})\s*$",
    re.IGNORECASE
)


# ---------------------------
# DB
# ---------------------------
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS measurements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  systolic INTEGER NOT NULL,
  diastolic INTEGER NOT NULL,
  pulse INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
"""

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(CREATE_TABLE_SQL)
        await db.commit()


async def save_measurement(user_id: int, sys_: int, dia: int, pulse: int):
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO measurements (user_id, systolic, diastolic, pulse, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, sys_, dia, pulse, now)
        )
        await db.commit()


async def fetch_last(user_id: int, limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT systolic, diastolic, pulse, created_at FROM measurements WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit)
        )
        rows = await cur.fetchall()
    return rows


async def fetch_range(user_id: int, since_utc: datetime):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT systolic, diastolic, pulse, created_at FROM measurements WHERE user_id=? AND created_at>=? ORDER BY created_at ASC",
            (user_id, since_utc.isoformat())
        )
        rows = await cur.fetchall()
    return rows


# ---------------------------
# HEALTH LOGIC (simple, safe)
# ---------------------------
def classify_bp(sys_: int, dia: int) -> str:
    """
    Rough classification (not medical advice).
    """
    if sys_ >= 180 or dia >= 120:
        return "🚨 Дуже високий тиск (кризовий рівень). Якщо самопочуття погане — звернись по невідкладну допомогу."
    if sys_ >= 140 or dia >= 90:
        return "⚠️ Підвищений тиск (гіпертензія)."
    if 120 <= sys_ <= 129 and dia < 80:
        return "ℹ️ Тиск трохи підвищений (пограничний)."
    if sys_ < 90 or dia < 60:
        return "⚠️ Знижений тиск (гіпотензія)."
    return "✅ Тиск у межах норми."


def classify_pulse(pulse: int) -> str:
    if pulse >= 120:
        return "⚠️ Дуже високий пульс."
    if pulse >= 100:
        return "ℹ️ Пульс підвищений."
    if pulse < 50:
        return "ℹ️ Пульс знижений."
    return "✅ Пульс у межах норми."


def validate_values(sys_: int, dia: int, pulse: int) -> str | None:
    if not (60 <= sys_ <= 260):
        return "Систолічний тиск виглядає некоректно. Перевір формат (наприклад: 120/80 68)."
    if not (40 <= dia <= 160):
        return "Діастолічний тиск виглядає некоректно. Перевір формат (наприклад: 120/80 68)."
    if dia >= sys_:
        return "Діастолічний не може бути >= систолічного. Перевір дані."
    if not (35 <= pulse <= 220):
        return "Пульс виглядає некоректно. Перевір дані."
    return None


# ---------------------------
# CHART
# ---------------------------
def make_chart_png(points: list[tuple[int, int, int, str]]) -> bytes:
    """
    points: [(sys, dia, pulse, created_at_iso), ...] ASC by time
    """
    import matplotlib.pyplot as plt  # local import for faster boot in some envs

    times = []
    sys_vals = []
    dia_vals = []
    pulse_vals = []

    for sys_, dia, pulse, ts in points:
        dt = datetime.fromisoformat(ts)
        # show in local-ish time: UTC+2 (you can change)
        dt_local = dt.astimezone(timezone(timedelta(hours=2)))
        times.append(dt_local)
        sys_vals.append(sys_)
        dia_vals.append(dia)
        pulse_vals.append(pulse)

    fig = plt.figure()
    ax = fig.add_subplot(111)

    ax.plot(times, sys_vals, marker="o", label="SYS")
    ax.plot(times, dia_vals, marker="o", label="DIA")
    ax.plot(times, pulse_vals, marker="o", label="PULSE")

    ax.set_title("Тиск і пульс за 7 днів")
    ax.set_xlabel("Дата/час")
    ax.set_ylabel("Значення")
    ax.legend()
    fig.autofmt_xdate()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


# ---------------------------
# WEB SERVER (for Render port)
# ---------------------------
async def health_handler(request):
    return web.Response(text="ok")

async def create_web_app():
    app = web.Application()
    app.router.add_get("/health", health_handler)
    app.router.add_get("/", health_handler)
    return app

async def run_web_server():
    app = await create_web_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Health server started on port {PORT}")
    # keep alive
    while True:
        await asyncio.sleep(3600)


# ---------------------------
# BOT
# ---------------------------
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: Message):
    text = (
        "Привіт! Я бот для моніторингу тиску 😊\n\n"
        "Надішли показники у форматі:\n"
        "<b>120/80 68</b>\n"
        "де 120 — систолічний, 80 — діастолічний, 68 — пульс.\n\n"
        "Команди:\n"
        "• /history — останні 10 вимірювань\n"
        "• /stats — статистика за 7 днів\n"
        "• /chart — графік за 7 днів\n"
    )
    await message.answer(text)


@dp.message(Command("history"))
async def cmd_history(message: Message):
    rows = await fetch_last(message.from_user.id, limit=10)
    if not rows:
        await message.answer("Поки що немає збережених вимірювань. Надішли: <b>120/80 68</b>")
        return

    lines = ["<b>Останні вимірювання:</b>"]
    for sys_, dia, pulse, ts in rows:
        dt = datetime.fromisoformat(ts).astimezone(timezone(timedelta(hours=2)))
        lines.append(f"• {dt.strftime('%d.%m %H:%M')} — <b>{sys_}/{dia}</b> пульс <b>{pulse}</b>")

    await message.answer("\n".join(lines))


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    since = datetime.now(timezone.utc) - timedelta(days=7)
    rows = await fetch_range(message.from_user.id, since)

    if not rows:
        await message.answer("За останні 7 днів немає даних. Надішли: <b>120/80 68</b>")
        return

    sys_vals = [r[0] for r in rows]
    dia_vals = [r[1] for r in rows]
    pulse_vals = [r[2] for r in rows]

    def avg(x): return sum(x) / len(x)

    text = (
        "<b>Статистика за 7 днів:</b>\n"
        f"• Кількість вимірювань: <b>{len(rows)}</b>\n"
        f"• Середній тиск: <b>{avg(sys_vals):.0f}/{avg(dia_vals):.0f}</b>\n"
        f"• Мін/Макс SYS: <b>{min(sys_vals)}</b> / <b>{max(sys_vals)}</b>\n"
        f"• Мін/Макс DIA: <b>{min(dia_vals)}</b> / <b>{max(dia_vals)}</b>\n"
        f"• Середній пульс: <b>{avg(pulse_vals):.0f}</b>\n"
    )
    await message.answer(text)


@dp.message(Command("chart"))
async def cmd_chart(message: Message):
    since = datetime.now(timezone.utc) - timedelta(days=7)
    rows = await fetch_range(message.from_user.id, since)

    if len(rows) < 2:
        await message.answer("Для графіка потрібно хоча б 2 вимірювання за 7 днів.")
        return

    png = make_chart_png(rows)
    file = BufferedInputFile(png, filename="chart.png")
    await message.answer_photo(file, caption="Графік за 7 днів")


@dp.message(F.text)
async def handle_text(message: Message):
    txt = (message.text or "").strip()

    m = bp_regex.match(txt)
    if not m:
        await message.answer("Не впізнав формат 😅\nСпробуй так: <b>120/80 68</b>")
        return

    sys_ = int(m.group(1))
    dia = int(m.group(2))
    pulse = int(m.group(3))

    err = validate_values(sys_, dia, pulse)
    if err:
        await message.answer(f"⚠️ {err}")
        return

    await save_measurement(message.from_user.id, sys_, dia, pulse)

    bp_status = classify_bp(sys_, dia)
    pulse_status = classify_pulse(pulse)

    # коротка відповідь + підказка
    reply = (
        f"✅ Збережено: <b>{sys_}/{dia}</b> пульс <b>{pulse}</b>\n\n"
        f"{bp_status}\n{pulse_status}\n\n"
        "Подивитись історію: /history\n"
        "Статистика: /stats\n"
        "Графік: /chart\n"
        "<i>Це не медична консультація.</i>"
    )
    await message.answer(reply)


async def main():
    await init_db()

    # Run polling + web server together
    await asyncio.gather(
        run_web_server(),
        dp.start_polling(bot),
    )


if __name__ == "__main__":
    asyncio.run(main())