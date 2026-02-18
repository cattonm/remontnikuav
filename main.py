import os
import json
import logging
import sys
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ContentType, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
import google.generativeai as genai

# Для роботи з Google Таблицями
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- КОНФІГУРАЦІЯ ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GOOGLE_CREDS_JSON = os.getenv('GOOGLE_CREDS_JSON') # Сюди вставимо вміст JSON-файлу
SPREADSHEET_NAME = "Заявки Ремонт" # Назва вашої таблиці (має співпадати точно!)

WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = 10000
WEBHOOK_URL = os.getenv('RENDER_EXTERNAL_URL')
WEBHOOK_PATH = "/webhook"
WEBAPP_URL = "https://remontnikuav.netlify.app"

logging.basicConfig(level=logging.INFO, stream=sys.stdout)

# --- ПІДКЛЮЧЕННЯ ДО СЕРВІСІВ ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# 1. Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash-latest')
else:
    model = None

# 2. Google Sheets
def get_gspread_client():
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        # Завантажуємо ключі з змінної оточення Render
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        logging.error(f"Помилка підключення до Google Sheets: {e}")
        return None

# --- ЗБЕРЕЖЕННЯ В ТАБЛИЦЮ ---
def save_to_sheet(data):
    client = get_gspread_client()
    if not client: return "Error"
    
    try:
        sheet = client.open(SPREADSHEET_NAME).sheet1
        
        # Формуємо рядок для таблиці
        c = data.get('client', {})
        answers_str = json.dumps(data.get('answers', {}), ensure_ascii=False)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        # Порядок колонок: Дата | Ім'я | Телефон | Тип | Адреса | Відповіді (JSON)
        row = [timestamp, c.get('name'), c.get('phone'), c.get('object_type'), c.get('address'), answers_str]
        
        sheet.append_row(row)
        return timestamp # Повертаємо ID (в нашому випадку час)
    except Exception as e:
        logging.error(f"Не вдалося записати в таблицю: {e}")
        return "Error"

# --- ПОШУК ЗАЯВКИ (З таблиці) ---
def find_order_in_sheet(phone_query):
    client = get_gspread_client()
    if not client: return None
    
    try:
        sheet = client.open(SPREADSHEET_NAME).sheet1
        records = sheet.get_all_records() # Це працює, якщо перший рядок - заголовки
        # Якщо заголовків немає, краще використовувати get_all_values()
        
        # Шукаємо по телефону (3 колонка, індекс 2 у списку, або по ключу якщо є заголовки)
        # Припустимо, ми просто беремо всі значення і шукаємо
        rows = sheet.get_all_values()
        
        for i, row in enumerate(rows):
            # row[2] - це телефон (за логікою save_to_sheet)
            if len(row) > 2 and phone_query in str(row[2]): 
                # Повертаємо структуру даних
                return {
                    "id": i, # номер рядка як ID
                    "client": {
                        "name": row[1],
                        "phone": row[2],
                        "object_type": row[3],
                        "address": row[4]
                    },
                    "answers": json.loads(row[5]) if len(row) > 5 else {}
                }
        return None
    except Exception as e:
        logging.error(f"Помилка пошуку: {e}")
        return None

# --- АДМІНКА ---
@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    await message.answer(
        "👨‍💼 **Панель менеджера**\n\n"
        "Щоб знайти анкету, введіть команду:\n"
        "`/find 0501234567` (номер телефону)\n\n"
        "Або відкрийте Google Таблицю для повного списку."
    )

@dp.message(Command("find"))
async def cmd_find(message: Message):
    try:
        phone = message.text.split()[1]
    except IndexError:
        await message.answer("⚠️ Введіть номер: `/find 098...`")
        return

    order = find_order_in_sheet(phone)
    if not order:
        await message.answer("❌ Заявку з таким номером не знайдено в таблиці.")
        return

    c = order['client']
    text = (
        f"📂 **Знайдено заявку!**\n"
        f"👤 {c['name']}\n"
        f"📞 {c['phone']}\n"
        f"🏠 {c['object_type']}\n"
        f"📍 {c['address']}"
    )
    
    # Кнопки дій
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Звіт для менеджера", callback_data=f"rep_{c['phone']}")],
        [InlineKeyboardButton(text="💰 Калькулятор", callback_data=f"calc_{c['phone']}")]
    ])
    
    await message.answer(text, reply_markup=kb)

# --- ГЕНЕРАЦІЯ ЗВІТУ ---
@dp.callback_query(F.data.startswith("rep_"))
async def make_report(callback: CallbackQuery):
    phone = callback.data.split("_")[1]
    order = find_order_in_sheet(phone)
    
    if not order or not model:
        await callback.message.answer("Помилка доступу до даних.")
        return

    await callback.message.answer("⏳ Формую картку об'єкта...")

    # --- МЕНЕДЖЕРСЬКИЙ ПРОМПТ ---
    # Чіткий, структурований, без води.
    prompt = f"""
    РОЛЬ: Ти помічник виконроба. Твоя мета - створити стислу "Картку Об'єкта" для менеджера.
    МОВА: Українська.
    
    ДАНІ КЛІЄНТА:
    {json.dumps(order['client'], ensure_ascii=False)}
    
    ТЕХНІЧНІ ВІДПОВІДІ (АНКЕТА):
    {json.dumps(order['answers'], ensure_ascii=False)}
    
    ЗАВДАННЯ: Сформуй звіт у такому форматі:
    
    📍 **ОБ'ЄКТ**: [Тип] за адресою [Адреса]
    👤 **КЛІЄНТ**: [Ім'я], [Телефон]
    
    🔨 **ОСНОВНІ РОБОТИ**:
    - Підлога: [Що вибрано]
    - Стіни: [Що вибрано]
    - Стеля: [Що вибрано]
    - Двері: [Деталі]
    
    ⚠️ **ВАЖЛИВІ НЮАНСИ**:
    (Тут випиши, якщо є: тепла підлога, перепланування, або специфічні побажання з анкети. Якщо немає - пропусти).
    
    📋 **РЕКОМЕНДОВАНІ НАСТУПНІ КРОКИ**:
    (Коротко: наприклад "Виїзд на замір", "Розрахунок чорнових матеріалів").
    """
    
    try:
        response = model.generate_content(prompt)
        await callback.message.answer(response.text)
    except Exception as e:
        await callback.message.answer(f"Помилка AI: {e}")
        
    await callback.answer()

@dp.callback_query(F.data.startswith("calc_"))
async def calc_stub(callback: CallbackQuery):
    await callback.message.answer("🧮 Тут буде модуль прорахунку (чекаю формули).")
    await callback.answer()

# --- ПРИЙОМ ДАНИХ (WEBHOOK) ---
@dp.message(F.content_type == ContentType.WEB_APP_DATA)
async def web_app_data_handler(message: Message):
    data = json.loads(message.web_app_data.data)
    
    # 1. Зберігаємо в Google Таблицю
    save_to_sheet(data)
    
    # 2. Відповідь клієнту (мінімалізм)
    await message.answer("👍 Вашу заявку прийнято!")

    # 3. АДМІНУ (Миттєве сповіщення з адресою!)
    c = data.get('client', {})
    
    admin_text = (
        f"🔔 **НОВЕ ЗАМОВЛЕННЯ!**\n\n"
        f"👤 {c.get('name')}\n"
        f"📞 {c.get('phone')}\n"
        f"🏠 {c.get('object_type')}\n"
        f"📍 {c.get('address')}"
    )
    # Надіслати адміну в особисті (ВСТАВ СВІЙ ID ТУТ)
    try:
        await bot.send_message(chat_id=123456789, text=admin_text) # Замінити 123456789 на ID
    except Exception as e:
        logging.error(f"Не вдалося надіслати адміну: {e}")

# --- ЗАПУСК ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    kb = [[KeyboardButton(text="📝 Заповнити анкету", web_app=WebAppInfo(url=WEBAPP_URL))]]
    await message.answer("Вітаю! Заповніть дані.", reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))

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

