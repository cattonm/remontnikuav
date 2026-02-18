import os
import json
import logging
import sys
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import Message, ContentType
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# --- НАЛАШТУВАННЯ ЛОГІВ (Щоб бачити все в консолі Render) ---
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

# --- КОНФІГУРАЦІЯ ---
TOKEN = os.getenv('BOT_TOKEN')
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = 10000
WEBHOOK_URL = os.getenv('RENDER_EXTERNAL_URL')
WEBHOOK_PATH = "/webhook"

# Перевірка токена
if not TOKEN:
    logging.error("❌ ПОМИЛКА: BOT_TOKEN не знайдено! Перевірте Environment Variables на Render.")
    sys.exit(1)

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- ХЕНДЛЕР 1: Команда /start (Щоб перевірити, чи бот живий) ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    logging.info("✅ Отримано команду /start")
    await message.answer("Привіт! Я працюю. Відкрий Web App і надішли форму.")

# --- ХЕНДЛЕР 2: Дані з WEB APP (Ремонт) ---
# Ми прибрали жорсткий фільтр, тепер перевіряємо всередині функції
@dp.message(F.content_type == ContentType.WEB_APP_DATA)
async def web_app_data_handler(message: Message):
    logging.info("📩 Отримано дані з Web App!")
    
    try:
        raw_json = message.web_app_data.data
        data = json.loads(raw_json)
        
        # Формуємо красивий звіт
        answers = data.get('answers', {})
        price = data.get('estimated_price', 'Не вказано')
        
        text = f"🛠 **Нове замовлення!**\n\n"
        text += f"💰 **Сума:** {price}\n"
        text += "------------------\n"
        
        for key, value in answers.items():
            # Замінюємо технічні назви на красиві, якщо треба
            text += f"🔹 {key}: {value}\n"
            
        await message.answer(text)
        logging.info("✅ Відповідь надіслано успішно")
        
    except Exception as e:
        logging.error(f"❌ Помилка обробки даних: {e}")
        await message.answer(f"Сталася помилка обробки даних: {e}")

# --- ХЕНДЛЕР 3: ВСЕ ІНШЕ (Діагностика) ---
# Якщо бот не зрозумів тип повідомлення, він скаже про це
@dp.message()
async def echo_handler(message: Message):
    logging.warning(f"⚠️ Отримано невідоме повідомлення: {message.content_type}")
    await message.answer(f"Я отримав повідомлення типу: {message.content_type}. Але я чекаю на форму з Web App.")

# --- ЗАПУСК ---
async def on_startup(bot: Bot):
    if not WEBHOOK_URL:
        logging.error("❌ WEBHOOK_URL не знайдено! Бот не зможе отримувати повідомлення.")
        return
        
    full_webhook_url = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
    logging.info(f"🔗 Встановлюю вебхук на: {full_webhook_url}")
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
