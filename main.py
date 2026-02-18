import os
import json
import asyncio
import logging
from aiogram import Bot, Dispatcher, F, types
from aiogram.types import Message, ContentType
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from dotenv import load_dotenv
load_dotenv()


BOT_TOKEN="8550961266:AAGrj-GcUDrk37MIrdtXD6uaAd418w2qS6A"






# --- КОНФІГУРАЦІЯ(Ці змінні ми додамо в налаштуваннях Render) ---
TOKEN = os.getenv('BOT_TOKEN')  # Токен від BotFather
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = 10000
WEBHOOK_URL = os.getenv('RENDER_EXTERNAL_URL')  # Render сам дасть цю адресу
WEBHOOK_PATH = "/webhook"

# Налаштування бота
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- ОБРОБКА ДАНИХ З MINI APP ---
@dp.message(F.content_type == ContentType.WEB_APP_DATA)
async def web_app_data_handler(message: Message):
    # 1. Отримуємо дані
    raw_json = message.web_app_data.data
    data = json.loads(raw_json)
    
    answers = data.get('answers', {})
    price = data.get('estimated_price', '0')
    
    # 2. Формуємо відповідь
    text = f"✅ **Заявку отримано!**\n\n"
    text += f"💰 Орієнтовна сума: {price}\n"
    text += "------------------\n"
    
    for question_id, answer in answers.items():
        text += f"🔹 {question_id}: {answer}\n"
        
    text += "\n⏳ Дані передано менеджеру на перевірку."
    
    # Тут можна додати відправку в Gemini або Google Sheets
    
    await message.answer(text)

# --- ЗАПУСК ВЕБ-СЕРВЕРА (Щоб Render не "засинав") ---
async def on_startup(bot: Bot):
    full_webhook_url = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
    print(f"Встановлюю вебхук на: {full_webhook_url}")
    await bot.set_webhook(full_webhook_url)

def main():
    dp.startup.register(on_startup)
    
    # Створюємо веб-додаток aiohttp
    app = web.Application()
    
    # Налаштовуємо обробку запитів від Telegram
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    
    setup_application(app, dp, bot=bot)
    
    # Запускаємо сервер
    web.run_app(app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
