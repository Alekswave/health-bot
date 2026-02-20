import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message
from aiogram.utils import executor

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

@dp.message_handler(commands=["start"])
async def start(message: Message):
    await message.answer("Привіт! Введи показники у форматі:\n120/80 70")

@dp.message_handler()
async def handle_data(message: Message):
    await message.answer(f"Отримано дані: {message.text}")

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)