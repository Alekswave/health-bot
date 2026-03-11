import asyncio
import logging
import os
import re
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def start_handler(message: Message):
    await message.answer(
        "👋 Привіт! Я бот для моніторингу тиску 😊\n\n"
        "Надішли показники у форматі:\n"
        "120/80 68\n\n"
        "де 120 — систолічний,\n"
        "80 — діастолічний,\n"
        "68 — пульс."
    )

@dp.message()
async def pressure_handler(message: Message):
    pattern = r"(\d{2,3})/(\d{2,3})\s+(\d{2,3})"
    match = re.match(pattern, message.text)

    if match:
        systolic, diastolic, pulse = match.groups()

        await message.answer(
            f"✅ Дані отримано:\n\n"
            f"Систолічний: {systolic}\n"
            f"Діастолічний: {diastolic}\n"
            f"Пульс: {pulse}"
        )
    else:
        await message.answer("❗ Надішли дані у форматі: 120/80 68")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())