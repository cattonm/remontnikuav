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
SPREADSHEET_NAME = "remonts sheets" # Твоя назва таблиці

WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = 10000
WEBHOOK_URL = os.getenv('RENDER_EXTERNAL_URL')
WEBHOOK_PATH = "/webhook"
WEBAPP_URL = "https://remontnikuav.netlify.app"

# Налаштування логування
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

# --- ПІДКЛЮЧЕННЯ СЕРВІСІВ ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash-latest')
else:
    model = None

# Google Sheets (через змінну оточення, щоб не було файлів)
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

# --- ЗБЕРЕЖЕННЯ ДАНИХ ---
def save_to_sheet(data):
    sheet = get_google_sheet()
    if not sheet: return False
    
    try:
        c = data.get('client', {})
        answers = json.dumps(data.get('answers', {}), ensure_ascii=False)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        # Порядок колонок: Дата | Ім'я | Телефон | Тип | Адреса | Відповіді
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

    # Отримуємо всі дані (get_all_values повертає список списків)
    rows = sheet.get_all_values()
    
    # Якщо тільки заголовок
    if len(rows) < 2: return None

    builder = InlineKeyboardBuilder()
    
    # Пропускаємо заголовок (start=1), але індекс рядка для gspread буде i+1
    # rows[0] - це заголовок. Дані починаються з rows[1], це рядок №2 в Excel
    for i, row in enumerate(rows[1:], start=2):
        # row[1] - Ім'я, row[2] - Телефон (згідно функції save_to_sheet)
        name = row[1] if len(row) > 1 else "Невідомо"
        phone = row[2] if len(row) > 2 else "..."
        
        btn_text = f"{name} | {phone}"
        builder.button(text=btn_text, callback_data=f"view_{i}") # view_2, view_3...

    builder.adjust(1)
    builder.button(text="🔄 Оновити", callback_data="show_list")
    return builder.as_markup()

# --- АДМІНКА ---
@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    await message.answer("Завантажую список заявок...", reply_markup=get_orders_keyboard())

@dp.callback_query(F.data == "show_list")
async def refresh_list(callback: CallbackQuery):
    kb = get_orders_keyboard()
    if kb:
        await callback.message.edit_text("Оберіть заявку:", reply_markup=kb)
    else:
        await callback.message.edit_text("Заявок поки немає.")

# --- ПЕРЕГЛЯД ЗАЯВКИ + ЗВІТ ---
@dp.callback_query(F.data.startswith("view_"))
async def view_order(callback: CallbackQuery):
    row_id = int(callback.data.split("_")[1])
    sheet = get_google_sheet()
    
    if not sheet:
        await callback.message.answer("Помилка таблиці.")
        return

    try:
        row_data = sheet.row_values(row_id)
        # Розбираємо рядок: Дата[0], Ім'я[1], Тел[2], Тип[3], Адреса[4], JSON[5]
        
        client_info = {
            "name": row_data[1] if len(row_data) > 1 else "",
            "phone": row_data[2] if len(row_data) > 2 else "",
            "type": row_data[3] if len(row_data) > 3 else "",
            "address": row_data[4] if len(row_data) > 4 else ""
        }
        raw_answers = row_data[5] if len(row_data) > 5 else "{}"

        # Кнопки дій
        kb = InlineKeyboardBuilder()
        kb.button(text="💰 Прорахунок", callback_data=f"calc_{row_id}")
        kb.button(text="🔙 Назад", callback_data="show_list")
        
        await callback.message.edit_text("⏳ AI аналізує анкету...", reply_markup=kb.as_markup())

        # ГЕНЕРАЦІЯ ЗВІТУ ЧЕРЕЗ GEMINI
        if model:
            prompt = f"""
            Роль: Ти помічник менеджера будівельної фірми.
            Завдання: Створи структуровану картку об'єкта на основі даних.
            
            КЛІЄНТ: {json.dumps(client_info, ensure_ascii=False)}
            ТЕХНІЧНІ ДАНІ: {raw_answers}
            
            Формат виводу:
            📍 **ОБ'ЄКТ**: [Тип] | [Адреса]
            👤 **КЛІЄНТ**: [Ім'я] | [Телефон]
            
            🛠 **ОСНОВНІ ЗАДАЧІ**:
            (Перерахуй список робіт коротко)
            
            ⚠️ **УВАГА**:
            (Якщо є складні моменти)
            """
            response = model.generate_content(prompt)
            await callback.message.edit_text(response.text, reply_markup=kb.as_markup())
        else:
            await callback.message.edit_text(f"Дані:\n{client_info}", reply_markup=kb.as_markup())

    except Exception as e:
        logging.error(f"View Error: {e}")
        await callback.message.answer("Помилка читання заявки.")

# --- ПРОРАХУНОК ---
@dp.callback_query(F.data.startswith("calc_"))
async def calc_order(callback: CallbackQuery):
    await callback.answer("Тут буде логіка калькулятора (формули).", show_alert=True)

# --- ВЕБХУК (ДЛЯ КЛІЄНТА) ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    kb = [[KeyboardButton(text="📝 Заповнити анкету", web_app=WebAppInfo(url=WEBAPP_URL))]]
    await message.answer("Меню:", reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))

@dp.message(F.content_type == ContentType.WEB_APP_DATA)
async def web_app_data_handler(message: Message):
    data = json.loads(message.web_app_data.data)
    if save_to_sheet(data):
        await message.answer("✅ Заявку прийнято!")
        # Сповіщення адміну (Введи свій ID)
        # await bot.send_message(CHAT_ID, "🔔 Нова заявка! /admin")
    else:
        await message.answer("❌ Помилка збереження.")

# --- ЗАПУСК ---
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
