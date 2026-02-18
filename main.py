import os
import json
import logging
import sys
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ContentType, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# Логи
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

# --- КОНФІГУРАЦІЯ ---
# Впишіть сюди ваш токен (бо через змінні у вас виникали складнощі, так надійніше зараз)
TOKEN = "8550961266:AAGrj-GcUDrk37MIrdtXD6uaAd418w2qS6A" 

WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = 10000
WEBHOOK_URL = os.getenv('RENDER_EXTERNAL_URL')
WEBHOOK_PATH = "/webhook"

# Посилання на ваш сайт Netlify
WEBAPP_URL = "https://remontnikuav.netlify.app"

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- ХЕНДЛЕР 1: Команда /start з КНОПКОЮ ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    logging.info("✅ Отримано команду /start")
    
    # Створюємо кнопку, яка правильно відкриває Web App
    kb = [
        [KeyboardButton(text="📝 Заповнити анкету", web_app=WebAppInfo(url=WEBAPP_URL))]
    ]
    keyboard = ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    
    await message.answer(
        "Привіт! Натисни кнопку нижче, щоб відкрити анкету 👇\n(Тільки так працюватиме відправка даних)", 
        reply_markup=keyboard
    )

# --- ХЕНДЛЕР 2: Дані з WEB APP ---
@dp.message(F.content_type == ContentType.WEB_APP_DATA)
async def web_app_data_handler(message: Message):
    logging.info("📩 Отримано дані з Web App!")
    try:
        data = json.loads(message.web_app_data.data)
        
        answers = data.get('answers', {})
        price = data.get('estimated_price', 'Не вказано')
        
        text = f"🛠 **Нове замовлення!**\n\n"
        text += f"💰 **Сума:** {price}\n"
        text += "------------------\n"
        
        for key, value in answers.items():
            text += f"🔹 {key}: {value}\n"
            
        await message.answer(text)
        
    except Exception as e:
        logging.error(f"❌ Помилка: {e}")
        await message.answer(f"Помилка: {e}")

# --- ЗАПУСК ---
async def on_startup(bot: Bot):
    full_webhook_url = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
    logging.info(f"🔗 Вебхук: {full_webhook_url}")
    await bot.set_webhook(full_webhook_url)

def main():
    dp.startup.register(on_startup)
    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    web.run_app(app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)

if __name__ == "__main__":
    main()
