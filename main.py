import os
import json
import logging
import sys
from datetime import datetime
import asyncio

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ContentType, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
import google.generativeai as genai

# --- КОНФІГУРАЦІЯ ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = 10000
WEBHOOK_URL = os.getenv('RENDER_EXTERNAL_URL')
WEBHOOK_PATH = "/webhook"
WEBAPP_URL = "https://remontnikuav.netlify.app"

# Налаштування Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash') # Ця модель працює!
else:
    model = None

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- ФУНКЦІЯ ДЛЯ ДОВГИХ ПОВІДОМЛЕНЬ ---
async def send_long_message(message: Message, text: str):
    if len(text) <= 4000:
        await message.answer(text, parse_mode=None) # Без Markdown, щоб уникнути помилок форматування
    else:
        # Ріжемо на шматки по 4000 символів
        for i in range(0, len(text), 4000):
            chunk = text[i:i+4000]
            await message.answer(chunk, parse_mode=None)
            await asyncio.sleep(0.5) # Пауза, щоб Телеграм не заблокував

# --- ЗБЕРЕЖЕННЯ В БАЗУ ---
def save_order(data):
    file_path = "orders_db.json"
    orders = []
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                orders = json.load(f)
        except: orders = []
    
    data['created_at'] = datetime.now().isoformat()
    orders.append(data)
    
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=4)

# --- ОБРОБКА ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    kb = [[KeyboardButton(text="📝 Заповнити анкету", web_app=WebAppInfo(url=WEBAPP_URL))]]
    await message.answer(f"Вітаю! Натисніть кнопку для запуску 👇", reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))

@dp.message(F.content_type == ContentType.WEB_APP_DATA)
async def web_app_data_handler(message: Message):
    try:
        payload = json.loads(message.web_app_data.data)
        save_order(payload)
        
        client = payload.get('client', {})
        answers = payload.get('answers', {})
        
        # 1. Підтвердження отримання
        info_msg = (
            f"✅ Дані отримано!\n"
            f"👤 {client.get('name')} ({client.get('phone')})\n"
            f"🏠 {client.get('object_type')}\n"
            f"⏳ Генерую детальний звіт..."
        )
        await message.answer(info_msg)

        # 2. Робота з AI
        if model:
            prompt = (
                f"Ти досвідчений виконроб. Проаналізуй цей об'єкт:\n"
                f"Клієнт: {client.get('name')}, Об'єкт: {client.get('object_type')}, Адреса: {client.get('address')}.\n"
                f"Відповіді по ремонту: {json.dumps(answers, ensure_ascii=False)}\n\n"
                f"ЗАВДАННЯ:\n"
                f"1. Напиши детальне Технічне Завдання (ТЗ) по етапах.\n"
                f"2. Вкажи, на що звернути увагу (ризики).\n"
                f"3. Орієнтовний список чорнових матеріалів."
            )
            
            response = model.generate_content(prompt)
            # Використовуємо безпечну функцію відправки
            await send_long_message(message, response.text)
        else:
            await message.answer("⚠️ AI не налаштовано.")

    except Exception as e:
        logging.error(f"Error: {e}")
        await message.answer(f"Сталася помилка: {e}")

async def on_startup(bot: Bot):
    await bot.set_webhook(f"{WEBHOOK_URL}{WEBHOOK_PATH}")

def main():
    dp.startup.register(on_startup)
    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    web.run_app(app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)

if __name__ == "__main__":
    main()
