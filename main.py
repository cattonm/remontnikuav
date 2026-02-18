import os
import json
import logging
import sys
from datetime import datetime
import asyncio

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ContentType, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- КОНФІГУРАЦІЯ ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GOOGLE_CREDS_JSON = os.getenv('GOOGLE_CREDS_JSON') 
SPREADSHEET_NAME = "remonts sheets" 

WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = 10000
WEBHOOK_URL = os.getenv('RENDER_EXTERNAL_URL')
WEBHOOK_PATH = "/webhook"
WEBAPP_URL = "https://remontnikuav.netlify.app"

logging.basicConfig(level=logging.INFO, stream=sys.stdout)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- GEMINI ---
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash')
else:
    model = None

# --- GOOGLE SHEETS ---
def get_google_sheet():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client.open(SPREADSHEET_NAME).sheet1
    except Exception as e:
        logging.error(f"Google Sheet Error: {e}")
        return None

# --- ЗБЕРЕЖЕННЯ ---
def save_to_sheet(data):
    sheet = get_google_sheet()
    if not sheet: return False
    try:
        c = data.get('client', {})
        answers = json.dumps(data.get('answers', {}), ensure_ascii=False)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        row = [timestamp, c.get('name'), c.get('phone'), c.get('object_type'), c.get('address'), answers]
        sheet.append_row(row)
        return True
    except Exception as e:
        logging.error(f"Save Error: {e}")
        return False

# --- КЛАВІАТУРИ ---
def get_orders_keyboard():
    sheet = get_google_sheet()
    if not sheet: return None
    rows = sheet.get_all_values()
    if len(rows) < 2: return None

    builder = InlineKeyboardBuilder()
    # start=2 бо 1-й рядок заголовок, а gspread рахує з 1
    for i, row in enumerate(rows[1:], start=2):
        name = row[1] if len(row) > 1 else "Невідомо"
        phone = row[2] if len(row) > 2 else "..."
        builder.button(text=f"{name} | {phone}", callback_data=f"view_{i}")

    builder.adjust(1)
    builder.button(text="🔄 Оновити список", callback_data="show_list")
    return builder.as_markup()

# --- АДМІНКА ---
@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    kb = get_orders_keyboard()
    msg = "📂 Список заявок:" if kb else "Заявок поки немає."
    await message.answer(msg, reply_markup=kb)

@dp.callback_query(F.data == "show_list")
async def refresh_list(callback: CallbackQuery):
    kb = get_orders_keyboard()
    msg = "📂 Список заявок:" if kb else "Заявок поки немає."
    await callback.message.edit_text(msg, reply_markup=kb)

# --- ПЕРЕГЛЯД КОРОТКОЇ КАРТКИ ---
@dp.callback_query(F.data.startswith("view_"))
async def view_order(callback: CallbackQuery):
    row_id = int(callback.data.split("_")[1])
    sheet = get_google_sheet()
    
    if not sheet:
        await callback.message.answer("Помилка доступу до таблиці.")
        return

    try:
        row_data = sheet.row_values(row_id)
        # Колонки: 0-Дата, 1-Ім'я, 2-Тел, 3-Тип, 4-Адреса
        name = row_data[1] if len(row_data) > 1 else "-"
        phone = row_data[2] if len(row_data) > 2 else "-"
        obj_type = row_data[3] if len(row_data) > 3 else "-"
        address = row_data[4] if len(row_data) > 4 else "-"

        text = (
            f"👤 **Клієнт:** {name}\n"
            f"📞 **Тел:** {phone}\n"
            f"🏠 **Об'єкт:** {obj_type}\n"
            f"📍 **Адреса:** {address}"
        )

        kb = InlineKeyboardBuilder()
        kb.button(text="📄 Згенерувати звіт", callback_data=f"gen_{row_id}")
        kb.button(text="💰 Прорахунок", callback_data=f"calc_{row_id}")
        kb.button(text="🗑 Видалити", callback_data=f"del_{row_id}")
        kb.button(text="🔙 Назад", callback_data="show_list")
        kb.adjust(1) # Кнопки в стовпчик

        await callback.message.edit_text(text, reply_markup=kb.as_markup())

    except Exception as e:
        await callback.message.answer("Заявку не знайдено (можливо, вона була видалена).")

# --- ГЕНЕРАЦІЯ ЗВІТУ (Тільки коли натиснули) ---
@dp.callback_query(F.data.startswith("gen_"))
async def generate_report_action(callback: CallbackQuery):
    row_id = int(callback.data.split("_")[1])
    sheet = get_google_sheet()
    
    await callback.message.answer("⏳ Формую чистий список робіт...")
    
    try:
        row_data = sheet.row_values(row_id)
        raw_answers = row_data[5] if len(row_data) > 5 else "{}"
        
        if model:
            # СУВОРИЙ ПРОМПТ
            prompt = f"""
            Ти форматувальник тексту.
            Завдання: Перетвори JSON з технічними даними ремонту у читабельний маркований список.
            
            ПРАВИЛА:
            1. Ніяких вступів, ніяких "Увага", ніяких порад, ніякої критики.
            2. Лише факти: "Питання: Відповідь".
            3. Якщо відповідь "Так/Ні" або число - пиши як є.
            
            ВХІДНІ ДАНІ: {raw_answers}
            """
            response = model.generate_content(prompt)
            await callback.message.answer(f"📋 **ТЕХНІЧНИЙ ОПИС:**\n\n{response.text}")
        else:
            await callback.message.answer("AI не підключено.")
            
    except Exception as e:
        await callback.message.answer(f"Помилка: {e}")
    
    await callback.answer()

# --- ВИДАЛЕННЯ ЗАЯВКИ ---
@dp.callback_query(F.data.startswith("del_"))
async def delete_order(callback: CallbackQuery):
    row_id = int(callback.data.split("_")[1])
    sheet = get_google_sheet()
    
    try:
        sheet.delete_rows(row_id)
        await callback.answer("✅ Заявку видалено!", show_alert=True)
        # Повертаємось до списку
        await refresh_list(callback)
    except Exception as e:
        await callback.answer(f"Помилка видалення: {e}", show_alert=True)

# --- ЗАГЛУШКА ПРОРАХУНКУ ---
@dp.callback_query(F.data.startswith("calc_"))
async def calc_stub(callback: CallbackQuery):
    await callback.answer("Формула прорахунку скоро буде тут.", show_alert=True)

# --- ВЕБХУК ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    kb = [[KeyboardButton(text="📝 Заповнити анкету", web_app=WebAppInfo(url=WEBAPP_URL))]]
    await message.answer("Меню:", reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))

@dp.message(F.content_type == ContentType.WEB_APP_DATA)
async def web_app_data_handler(message: Message):
    data = json.loads(message.web_app_data.data)
    if save_to_sheet(data):
        await message.answer("✅ Прийнято!")
        # Сповіщення адміну (встав ID, якщо треба)
        # await bot.send_message(12345678, "🔔 Нова заявка! /admin")

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
