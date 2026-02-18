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

# --- ЗБЕРЕЖЕННЯ ЗАЯВКИ ---
def save_to_sheet(data):
    sheet = get_google_sheet()
    if not sheet: return False
    try:
        c = data.get('client', {})
        answers = json.dumps(data.get('answers', {}), ensure_ascii=False)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        # Колонки: Дата | Ім'я | Телефон | Тип | Адреса | JSON | ЗВІТ (пусто)
        row = [timestamp, c.get('name'), c.get('phone'), c.get('object_type'), c.get('address'), answers, ""]
        sheet.append_row(row)
        return True
    except Exception as e:
        logging.error(f"Save Error: {e}")
        return False

# --- ЗБЕРЕЖЕННЯ ЗВІТУ В ТАБЛИЦЮ ---
def save_report_to_cell(row_id, report_text):
    sheet = get_google_sheet()
    if not sheet: return
    try:
        # Оновлюємо 7-му колонку (G) у відповідному рядку
        sheet.update_cell(row_id, 7, report_text)
    except Exception as e:
        logging.error(f"Report Save Error: {e}")

# --- ОТРИМАННЯ ЗВІТУ З ТАБЛИЦІ ---
def get_cached_report(row_id):
    sheet = get_google_sheet()
    if not sheet: return None
    try:
        # Беремо значення з 7-ї колонки
        return sheet.cell(row_id, 7).value
    except:
        return None

# --- КЛАВІАТУРИ ---
def get_orders_keyboard():
    sheet = get_google_sheet()
    if not sheet: return None
    rows = sheet.get_all_values()
    if len(rows) < 2: return None

    builder = InlineKeyboardBuilder()
    for i, row in enumerate(rows[1:], start=2):
        name = row[1] if len(row) > 1 else "Невідомо"
        phone = row[2] if len(row) > 2 else "..."
        builder.button(text=f"{name} | {phone}", callback_data=f"view_{i}")

    builder.adjust(1)
    builder.button(text="🔄 Оновити список", callback_data="show_list")
    return builder.as_markup()

# --- ЛОГІКА МЕНЕДЖЕРА ---

@dp.message(F.text == "🔐 Кабінет менеджера")
@dp.message(Command("admin"))
async def open_admin_panel(message: Message):
    kb = get_orders_keyboard()
    if kb:
        await message.answer("📂 **Список активних заявок:**", reply_markup=kb)
    else:
        await message.answer("📭 Список заявок порожній.")

@dp.callback_query(F.data == "show_list")
async def refresh_list(callback: CallbackQuery):
    kb = get_orders_keyboard()
    msg = "📂 **Список заявок:**" if kb else "📭 Порожньо."
    await callback.message.edit_text(msg, reply_markup=kb)

# --- ПЕРЕГЛЯД КАРТКИ (З ПЕРЕВІРКОЮ НАЯВНОСТІ ЗВІТУ) ---
@dp.callback_query(F.data.startswith("view_"))
async def view_order(callback: CallbackQuery):
    row_id = int(callback.data.split("_")[1])
    sheet = get_google_sheet()
    
    if not sheet:
        await callback.message.answer("⚠️ Помилка таблиці.")
        return

    try:
        row_data = sheet.row_values(row_id)
        name = row_data[1] if len(row_data) > 1 else "-"
        phone = row_data[2] if len(row_data) > 2 else "-"
        obj_type = row_data[3] if len(row_data) > 3 else "-"
        address = row_data[4] if len(row_data) > 4 else "-"
        
        # Перевіряємо, чи є вже звіт у 7-й колонці (індекс 6 у списку)
        existing_report = row_data[6] if len(row_data) > 6 else ""

        text = (
            f"👤 **Клієнт:** {name}\n"
            f"📞 **Телефон:** `{phone}`\n"
            f"🏠 **Об'єкт:** {obj_type}\n"
            f"📍 **Адреса:** {address}"
        )

        kb = InlineKeyboardBuilder()
        
        # ЛОГІКА КНОПКИ ЗВІТУ
        if existing_report and len(existing_report) > 10:
            # Якщо звіт вже є - кнопка "Відкрити"
            kb.button(text="📂 Відкрити збережений звіт", callback_data=f"showrep_{row_id}")
        else:
            # Якщо звіту немає - кнопка "Згенерувати"
            kb.button(text="✨ Згенерувати ТЗ (AI)", callback_data=f"gen_{row_id}")
            
        kb.button(text="💰 Розрахунок", callback_data=f"calc_{row_id}")
        kb.button(text="🗑 Видалити заявку", callback_data=f"del_{row_id}")
        kb.button(text="🔙 Назад", callback_data="show_list")
        kb.adjust(1) 

        await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="Markdown")

    except Exception as e:
        await callback.message.answer("Помилка даних заявки.")

# --- ПОКАЗ ВЖЕ ЗБЕРЕЖЕНОГО ЗВІТУ ---
@dp.callback_query(F.data.startswith("showrep_"))
async def show_saved_report(callback: CallbackQuery):
    row_id = int(callback.data.split("_")[1])
    
    report = get_cached_report(row_id)
    if report:
        # Додаємо кнопку "Оновити", якщо раптом треба перегенерувати
        kb = InlineKeyboardBuilder()
        kb.button(text="🔄 Перегенерувати (AI)", callback_data=f"gen_{row_id}")
        kb.button(text="🔙 Назад до заявки", callback_data=f"view_{row_id}")
        kb.adjust(1)
        
        await callback.message.edit_text(f"📋 **ЗБЕРЕЖЕНИЙ ЗВІТ:**\n\n{report}", reply_markup=kb.as_markup(), parse_mode="Markdown")
    else:
        await callback.answer("Помилка: Звіт не знайдено.", show_alert=True)

# --- ГЕНЕРАЦІЯ ЗВІТУ + ЗБЕРЕЖЕННЯ ---
@dp.callback_query(F.data.startswith("gen_"))
async def generate_report_action(callback: CallbackQuery):
    row_id = int(callback.data.split("_")[1])
    sheet = get_google_sheet()
    
    await callback.message.answer("⏳ **AI аналізує та зберігає звіт...**")
    
    try:
        row_data = sheet.row_values(row_id)
        raw_answers = row_data[5] if len(row_data) > 5 else "{}"
        
        if model:
            prompt = f"""
            Ти професійний будівельний кошторисник.
            Створи "Паспорт Об'єкта" для майстрів.
            СТИЛЬ:
            - Жирний шрифт для ключів.
            - Емодзі для розділів.
            - Жодних порад чи вступу. Тільки факти.
            ВХІДНІ ДАНІ: {raw_answers}
            """
            response = model.generate_content(prompt)
            report_text = response.text
            
            # 1. ЗБЕРІГАЄМО В ТАБЛИЦЮ
            save_report_to_cell(row_id, report_text)
            
            # 2. ПОКАЗУЄМО
            kb = InlineKeyboardBuilder()
            kb.button(text="🔙 Назад до заявки", callback_data=f"view_{row_id}")
            
            await callback.message.answer(f"📋 **ПАСПОРТ ОБ'ЄКТА (Збережено)**\n\n{report_text}", reply_markup=kb.as_markup(), parse_mode="Markdown")
        else:
            await callback.message.answer("⚠️ AI модуль не підключено.")
            
    except Exception as e:
        await callback.message.answer(f"Помилка: {e}")
    
    await callback.answer()

# --- ВИДАЛЕННЯ ---
@dp.callback_query(F.data.startswith("del_"))
async def delete_order(callback: CallbackQuery):
    row_id = int(callback.data.split("_")[1])
    sheet = get_google_sheet()
    try:
        sheet.delete_rows(row_id)
        await callback.answer("✅ Видалено!", show_alert=True)
        await refresh_list(callback)
    except Exception as e:
        await callback.answer(f"Помилка: {e}", show_alert=True)

# --- ЗАГЛУШКА ПРОРАХУНКУ ---
@dp.callback_query(F.data.startswith("calc_"))
async def calc_stub(callback: CallbackQuery):
    await callback.answer("Скоро тут будуть цифри 💵", show_alert=True)

# --- СТАРТ ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Заповнити анкету", web_app=WebAppInfo(url=WEBAPP_URL))],
            [KeyboardButton(text="🔐 Кабінет менеджера")]
        ], resize_keyboard=True
    )
    await message.answer(
        f"👋 **Вітаю, {message.from_user.first_name}!**\n\n"
        "Я допоможу сформувати кошторис та визначити обсяг робіт.\n"
        "Натисніть **'Заповнити анкету'**, щоб почати.", 
        reply_markup=kb, parse_mode="Markdown"
    )

# --- ВЕБХУК ---
@dp.message(F.content_type == ContentType.WEB_APP_DATA)
async def web_app_data_handler(message: Message):
    data = json.loads(message.web_app_data.data)
    if save_to_sheet(data):
        await message.answer("✅ **Заявку прийнято!** Очікуйте дзвінка.", parse_mode="Markdown")
    else:
        await message.answer("⚠️ Помилка. Спробуйте ще раз.")

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
