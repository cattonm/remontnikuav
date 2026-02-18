import os
import json
import logging
import sys
import asyncio
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ContentType, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, FSInputFile
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
import google.generativeai as genai

# --- КОНФІГУРАЦІЯ ---
# Встав сюди свої ключі
BOT_TOKEN = "8550961266:AAGrj-GcUDrk37MIrdtXD6uaAd418w2qS6A"
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY') # Або встав ключ текстом сюди, якщо тестуєш

WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = 10000
WEBHOOK_URL = os.getenv('RENDER_EXTERNAL_URL')
WEBHOOK_PATH = "/webhook"
WEBAPP_URL = "https://remontnikuav.netlify.app" # Твій сайт

# Налаштування Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-pro')
else:
    logging.warning("⚠️ GEMINI_API_KEY не знайдено! AI не працюватиме.")

# Логи
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- ФУНКЦІЯ ЗБЕРЕЖЕННЯ (БАЗА ДАНИХ) ---
def save_order(data):
    file_name = "orders_db.json"
    existing_data = []
    
    # Читаємо старі записи, якщо є
    if os.path.exists(file_name):
        try:
            with open(file_name, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
        except:
            pass
            
    # Додаємо дату і записуємо
    data['timestamp'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing_data.append(data)
    
    with open(file_name, "w", encoding="utf-8") as f:
        json.dump(existing_data, f, ensure_ascii=False, indent=4)
    
    logging.info(f"💾 Замовлення збережено. Всього записів: {len(existing_data)}")

# --- ЗАПУСК ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    kb = [[KeyboardButton(text="📝 Заповнити анкету", web_app=WebAppInfo(url=WEBAPP_URL))]]
    await message.answer("Вітаю! Заповніть дані про об'єкт 👇", reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))

# --- ОБРОБКА ДАНИХ ---
@dp.message(F.content_type == ContentType.WEB_APP_DATA)
async def web_app_data_handler(message: Message):
    try:
        # 1. Отримуємо "сирі" дані
        data = json.loads(message.web_app_data.data)
        answers = data.get('answers', {})
        client_info = data.get('client', {})
        
        # 2. Зберігаємо в "Базу"
        save_order(data)
        
        await message.answer("⏳ Дані отримано. Обробляю через Gemini...")

        # 3. Формуємо запит для Gemini
        prompt = f"""
        Ти - професійний кошторисник та виконроб. Проаналізуй дані клієнта та об'єкта.
        
        КЛІЄНТ: {client_info.get('name')}, Тел: {client_info.get('phone')}
        ОБ'ЄКТ: {client_info.get('object_type')}, {client_info.get('address')}
        
        ТЕХНІЧНІ ПАРАМЕТРИ (ВІДПОВІДІ):
        """
        for k, v in answers.items():
            prompt += f"- {k}: {v}\n"
            
        prompt += """
        ЗАВДАННЯ:
        1. Сформуй професійний опис об'єкта (summary).
        2. Виділи потенційні складні моменти (наприклад, прихований монтаж дверей).
        3. Склади список основних етапів робіт на основі цих даних.
        """

        # 4. Відправляємо в Gemini
        if GEMINI_API_KEY:
            response = model.generate_content(prompt)
            ai_text = response.text
        else:
            ai_text = "⚠️ Gemini API Key не налаштовано."

        # 5. Віддаємо результат
        await message.answer(f"✅ **ЗВІТ ПО ОБ'ЄКТУ**\n\n{ai_text}", parse_mode="Markdown")
        
    except Exception as e:
        logging.error(f"Error: {e}")
        await message.answer(f"Помилка: {e}")

# --- WEBHOOK SETUP ---
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
