import os
import re
import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.enums import ParseMode

from aiohttp import web


# =========================
# CONFIG
# =========================
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in environment variables")

# Render gives PORT for web services. We'll start a tiny health server there.
PORT = int(os.getenv("PORT", "10000"))
DB_PATH = os.getenv("DB_PATH", "health.db")

# For Ukraine local time: UTC+2 / UTC+3 depends on DST. We'll keep UTC in DB and show local as UTC+2 by default.
UA_TZ = timezone(timedelta(hours=2))


# =========================
# DB
# =========================
def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ts_utc TEXT NOT NULL,
            sys INTEGER NOT NULL,
            dia INTEGER NOT NULL,
            pulse INTEGER NOT NULL
        )
    """)
    conn.commit()
    return conn


def db_add_reading(user_id: int, sys: int, dia: int, pulse: int):
    conn = db_connect()
    try:
        ts_utc = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO readings(user_id, ts_utc, sys, dia, pulse) VALUES (?, ?, ?, ?, ?)",
            (user_id, ts_utc, sys, dia, pulse)
        )
        conn.commit()
    finally:
        conn.close()


def db_get_last(user_id: int):
    conn = db_connect()
    try:
        cur = conn.execute(
            "SELECT ts_utc, sys, dia, pulse FROM readings WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user_id,)
        )
        row = cur.fetchone()
        return row
    finally:
        conn.close()


def db_get_last_n(user_id: int, n: int = 10):
    conn = db_connect()
    try:
        cur = conn.execute(
            "SELECT ts_utc, sys, dia, pulse FROM readings WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, n)
        )
        rows = cur.fetchall()
        return rows
    finally:
        conn.close()


def db_clear(user_id: int):
    conn = db_connect()
    try:
        conn.execute("DELETE FROM readings WHERE user_id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def db_stats_last_days(user_id: int, days: int = 7):
    """Return averages + count for last X days."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    conn = db_connect()
    try:
        cur = conn.execute(
            """
            SELECT COUNT(*),
                   AVG(sys),
                   AVG(dia),
                   AVG(pulse)
            FROM readings
            WHERE user_id=? AND ts_utc >= ?
            """,
            (user_id, since.isoformat())
        )
        row = cur.fetchone()
        return row  # (count, avg_sys, avg_dia, avg_pulse)
    finally:
        conn.close()


# =========================
# PARSING
# =========================
# Accept formats:
# "120/80 70"
# "120 80 70"
# "120/80,70"
# "120/80    70"
READING_RE = re.compile(
    r"^\s*(\d{2,3})\s*/\s*(\d{2,3})\s*[, ]+\s*(\d{2,3})\s*$"
)

READING_RE_ALT = re.compile(
    r"^\s*(\d{2,3})\s+(\d{2,3})\s+(\d{2,3})\s*$"
)


def parse_reading(text: str):
    m = READING_RE.match(text)
    if not m:
        m = READING_RE_ALT.match(text)
    if not m:
        return None

    sys = int(m.group(1))
    dia = int(m.group(2))
    pulse = int(m.group(3))

    # basic sanity
    if not (60 <= sys <= 260):  # allow wide range
        return None
    if not (40 <= dia <= 160):
        return None
    if not (30 <= pulse <= 220):
        return None
    if dia >= sys:
        return None

    return sys, dia, pulse


# =========================
# ANALYSIS (advanced, but safe)
# =========================
def bp_category(sys: int, dia: int):
    """
    Practical categories (non-diagnostic):
    - Very low
    - Low
    - Normal
    - Elevated
    - High (stage 1)
    - High (stage 2)
    - Crisis
    """
    # Crisis thresholds
    if sys >= 180 or dia >= 120:
        return "crisis"

    # Low
    if sys < 90 or dia < 60:
        # Very low
        if sys < 80 or dia < 50:
            return "very_low"
        return "low"

    # Normal/elevated/high
    # If any component is in a higher category -> use it
    if sys >= 140 or dia >= 90:
        return "high_2"
    if 130 <= sys <= 139 or 80 <= dia <= 89:
        return "high_1"
    if 120 <= sys <= 129 and dia < 80:
        return "elevated"
    return "normal"


def pulse_category(pulse: int):
    if pulse >= 130:
        return "very_fast"
    if pulse > 100:
        return "fast"
    if pulse < 45:
        return "very_slow"
    if pulse < 60:
        return "slow"
    return "normal"


def pulse_pressure_category(pp: int):
    # Pulse pressure = sys - dia
    if pp < 25:
        return "low"
    if pp > 60:
        return "high"
    return "normal"


def trend_hint(user_id: int, sys: int, dia: int, pulse: int):
    """
    Compare with last reading (if exists) and 7-day average (if enough data).
    """
    last = db_get_last(user_id)
    # Note: last is already the current one after saving, so we should fetch last 2
    last2 = db_get_last_n(user_id, 2)
    prev = last2[1] if len(last2) == 2 else None

    parts = []

    if prev:
        _, ps, pd, ppulse = prev
        d_sys = sys - ps
        d_dia = dia - pd
        d_p = pulse - ppulse

        def fmt_delta(x):
            return f"+{x}" if x > 0 else f"{x}"

        parts.append(
            f"🔎 Зміна vs попередній запис: САТ {fmt_delta(d_sys)}, ДАТ {fmt_delta(d_dia)}, пульс {fmt_delta(d_p)}."
        )

    cnt, avg_sys, avg_dia, avg_p = db_stats_last_days(user_id, days=7)
    if cnt and cnt >= 5 and avg_sys and avg_dia and avg_p:
        parts.append(
            f"📈 Середнє за 7 днів ({cnt} вимір.): ~{avg_sys:.0f}/{avg_dia:.0f}, пульс ~{avg_p:.0f}."
        )

    return "\n".join(parts).strip()


def build_advice(sys: int, dia: int, pulse: int, pp: int):
    cat = bp_category(sys, dia)
    pcat = pulse_category(pulse)
    ppcat = pulse_pressure_category(pp)

    lines = []
    flags = []

    # Core summary
    lines.append(f"📊 *Показники*")
    lines.append(f"• Тиск: *{sys}/{dia}* мм рт. ст.")
    lines.append(f"• Пульс: *{pulse}* уд/хв")
    lines.append(f"• Пульсовий тиск: *{pp}* мм рт. ст.")

    # Interpret BP
    if cat == "normal":
        lines.append("🟢 Тиск: в межах норми для більшості людей.")
    elif cat == "elevated":
        lines.append("🟡 Тиск: *підвищений* (пограничний).")
        lines.append("• Порада: відпочинок 5–10 хв, повторити вимір 2 рази й взяти середнє.")
    elif cat == "high_1":
        lines.append("🟠 Тиск: *підвищений* (≈ 130–139 / 80–89).")
        lines.append("• Порада: повторити вимір через 10–15 хв спокою; якщо так тримається часто — варто обговорити з кардіологом.")
    elif cat == "high_2":
        lines.append("🔴 Тиск: *високий* (≥140 або ≥90).")
        lines.append("• Порада: повторити вимір; якщо повторно високий — запиши в щоденник і звʼяжись з лікарем для корекції терапії.")
        flags.append("high_bp")
    elif cat == "low":
        lines.append("🟡 Тиск: *низький* (<90/60).")
        lines.append("• Порада: вода/теплий чай, спокій, перевірити самопочуття, повторити вимір через 10 хв.")
        flags.append("low_bp")
    elif cat == "very_low":
        lines.append("🔴 Тиск: *дуже низький* (потенційно небезпечно, якщо є симптоми).")
        lines.append("• Порада: ляж, підніми ноги, вода; якщо слабкість/непритомність/холодний піт — звернись по допомогу.")
        flags.append("very_low_bp")
    elif cat == "crisis":
        lines.append("🚨 *Критично високий тиск* (≥180 або ≥120).")
        lines.append("• *Негайно* повтори вимір через 3–5 хв спокою.")
        lines.append("• Якщо є біль у грудях, задишка, слабкість/оніміння, порушення мови/зору, сильний головний біль — *дзвони 103*.")
        flags.append("crisis")

    # Pulse interpretation
    if pcat == "normal":
        lines.append("🟢 Пульс: нормальний (60–100).")
    elif pcat == "slow":
        lines.append("🟡 Пульс: знижений (<60). Якщо це після спорту/у тренованих — може бути ок. Якщо є запаморочення/слабкість — краще перевірити.")
        flags.append("slow_pulse")
    elif pcat == "very_slow":
        lines.append("🔴 Пульс: *дуже низький* (<45). Якщо є симптоми (слабкість, непритомність) — потрібна медична оцінка.")
        flags.append("very_slow_pulse")
    elif pcat == "fast":
        lines.append("🟠 Пульс: підвищений (>100). Відпочинок 10 хв, вода; якщо тримається — звернись до лікаря.")
        flags.append("fast_pulse")
    elif pcat == "very_fast":
        lines.append("🔴 Пульс: *дуже високий* (≥130). Якщо є біль у грудях, задишка, запаморочення — *терміново* медична допомога.")
        flags.append("very_fast_pulse")

    # Pulse pressure
    if ppcat == "normal":
        lines.append("🟢 Пульсовий тиск: у типовому діапазоні.")
    elif ppcat == "low":
        lines.append("🟡 Пульсовий тиск: низький (<25). Якщо є слабкість/запаморочення — повтори вимір і звернись до лікаря.")
        flags.append("low_pp")
    elif ppcat == "high":
        lines.append("🟠 Пульсовий тиск: підвищений (>60). Може зростати при стресі/фіз.навантаженні; якщо часто — обговори з кардіологом.")
        flags.append("high_pp")

    # Safety notes (you have high cardiovascular risk + aortic stent history)
    lines.append("")
    lines.append("⚠️ *Увага:* це автоматичний аналіз, не діагноз.")
    lines.append("Якщо є *біль у грудях, задишка, раптова слабкість/оніміння, порушення мови/зору, непритомність* — *103*.")

    return "\n".join(lines).strip()


def format_dt_local(ts_utc_iso: str):
    dt_utc = datetime.fromisoformat(ts_utc_iso)
    dt_local = dt_utc.astimezone(UA_TZ)
    return dt_local.strftime("%d.%m.%Y %H:%M")


# =========================
# BOT HANDLERS
# =========================
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.MARKDOWN)
dp = Dispatcher()


HELP_TEXT = (
    "Привіт 👋\n\n"
    "Введи показники у форматі:\n"
    "`120/80 70`\n"
    "де `120/80` — тиск, `70` — пульс.\n\n"
    "Команди:\n"
    "• /help — допомога\n"
    "• /last — останній запис\n"
    "• /history — 10 останніх\n"
    "• /clear — очистити історію\n"
    "• /stats — середнє за 7 днів\n"
)


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(HELP_TEXT)


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT)


@dp.message(Command("last"))
async def cmd_last(message: Message):
    row = db_get_last(message.from_user.id)
    if not row:
        await message.answer("Поки що немає записів. Введи, наприклад: `120/80 70`")
        return
    ts_utc, sys, dia, pulse = row
    await message.answer(
        f"🕒 Останній запис: *{format_dt_local(ts_utc)}*\n"
        f"• Тиск: *{sys}/{dia}*\n"
        f"• Пульс: *{pulse}*"
    )


@dp.message(Command("history"))
async def cmd_history(message: Message):
    rows = db_get_last_n(message.from_user.id, 10)
    if not rows:
        await message.answer("Історія порожня. Введи перший запис у форматі: `120/80 70`")
        return

    lines = ["📚 *Останні 10 записів:*"]
    for ts_utc, sys, dia, pulse in rows:
        lines.append(f"• {format_dt_local(ts_utc)} — *{sys}/{dia}*  (пульс *{pulse}*)")
    await message.answer("\n".join(lines))


@dp.message(Command("clear"))
async def cmd_clear(message: Message):
    db_clear(message.from_user.id)
    await message.answer("✅ Історію очищено.")


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    cnt, avg_sys, avg_dia, avg_p = db_stats_last_days(message.from_user.id, days=7)
    if not cnt or cnt == 0:
        await message.answer("Немає даних за останні 7 днів. Додай кілька вимірів.")
        return
    await message.answer(
        f"📈 *Статистика за 7 днів*\n"
        f"• К-сть вимірів: *{cnt}*\n"
        f"• Середній тиск: *{avg_sys:.0f}/{avg_dia:.0f}*\n"
        f"• Середній пульс: *{avg_p:.0f}*"
    )


@dp.message(F.text)
async def on_text(message: Message):
    text = (message.text or "").strip()

    parsed = parse_reading(text)
    if not parsed:
        await message.answer(
            "Не розпізнав формат 😅\n"
            "Спробуй так: `120/80 70`\n"
            "Або: `120 80 70`"
        )
        return

    sys, dia, pulse = parsed
    pp = sys - dia

    # Save to DB
    db_add_reading(message.from_user.id, sys, dia, pulse)

    # Build analysis
    analysis = build_advice(sys, dia, pulse, pp)
    trend = trend_hint(message.from_user.id, sys, dia, pulse)
    if trend:
        analysis = f"{analysis}\n\n{trend}"

    await message.answer(analysis)


# =========================
# HEALTH SERVER FOR RENDER
# =========================
async def health_handler(request):
    return web.Response(text="ok")


async def start_health_server():
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"Health server started on port {PORT}")


# =========================
# MAIN
# =========================
async def main():
    await start_health_server()
    logging.info("Bot polling started...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())