import os
import json
import logging
import sys
from datetime import datetime

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

# Налаштування Gemini (актуальна модель 1.5 Flash)
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash')
else:
    model = None
    logging.warning("⚠️ GEMINI_API_KEY не налаштовано в Environment Variables!")

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- ЗБЕРЕЖЕННЯ В БАЗУ ---
def save_order(data):
    file_path = "orders_db.json"
    orders = []
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                orders = json.load(f)
        except Exception:
            orders = []
    
    data['created_at'] = datetime.now().isoformat()
    orders.append(data)
    
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=4)

# --- ОБРОБКА ПОВІДОМЛЕНЬ ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    kb = [[KeyboardButton(text="📝 Заповнити анкету", web_app=WebAppInfo(url=WEBAPP_URL))]]
    keyboard = ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    await message.answer("Вітаю! Натисніть кнопку нижче, щоб розпочати опитування 👇", reply_markup=keyboard)

@dp.message(F.content_type == ContentType.WEB_APP_DATA)
async def web_app_data_handler(message: Message):
    try:
        payload = json.loads(message.web_app_data.data)
        save_order(payload) # Зберігаємо дані відразу
        
        answers = payload.get('answers', {})
        client = payload.get('client', {})
        
        summary_text = f"👤 **Клієнт:** {client.get('name')}\n📞 **Тел:** {client.get('phone')}\n🏠 **Об'єкт:** {client.get('object_type')}\n\n⏳ Обробляю дані через AI..."
        await message.answer(summary_text)

        if model:
            # Формуємо промпт для Gemini
            prompt = f"Ти професійний будівельник. Сформуй ТЗ на основі відповідей клієнта: {json.dumps(answers, ensure_ascii=False)}. Клієнт: {client.get('name')}, об'єкт: {client.get('object_type')}."
            response = model.generate_content(prompt)
            await message.answer(f"✅ **Звіт Gemini:**\n\n{response.text}")
        else:
            await message.answer("❌ Звіт AI недоступний (відсутній API Key).")
            
    except Exception as e:
        logging.error(f"Помилка: {e}")
        await message.answer(f"⚠️ Сталася помилка: {e}")

# --- WEBHOOK ---
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
