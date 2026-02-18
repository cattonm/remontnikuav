import os
import json
import logging
import sys
from datetime import datetime
import asyncio

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ContentType, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
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
    model = genai.GenerativeModel('gemini-2.5-flash') # Використовуємо швидку модель
else:
    model = None

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- РОБОТА З БАЗОЮ ДАНИХ ---
DB_FILE = "orders_db.json"

def save_order(data):
    orders = get_all_orders()
    # Генеруємо унікальний ID на основі часу, щоб не плутатись
    data['id'] = str(int(datetime.now().timestamp()))
    data['created_at'] = datetime.now().strftime("%Y-%m-%d %H:%M")
    orders.append(data)
    
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=4)
    return data['id']

def get_all_orders():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: return []
    return []

def get_order_by_id(order_id):
    orders = get_all_orders()
    for order in orders:
        if order.get('id') == order_id:
            return order
    return None

# --- АДМІН-ПАНЕЛЬ: СПИСОК ЗАЯВОК ---
@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    orders = get_all_orders()
    if not orders:
        await message.answer("📂 База заявок порожня.")
        return

    # Створюємо кнопки для кожної заявки
    builder = []
    # Показуємо останні 10 заявок (нові зверху)
    for order in reversed(orders[-10:]):
        client = order.get('client', {})
        btn_text = f"{client.get('name')} | {client.get('phone')}"
        # В callback_data передаємо ID замовлення
        builder.append([InlineKeyboardButton(text=btn_text, callback_data=f"order_{order.get('id')}")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=builder)
    await message.answer("🗂 **Список останніх заявок:**", reply_markup=keyboard)

# --- ОБРОБКА ВИБОРУ ЗАЯВКИ ---
@dp.callback_query(F.data.startswith("order_"))
async def process_order_selection(callback: CallbackQuery):
    order_id = callback.data.split("_")[1]
    order = get_order_by_id(order_id)
    
    if not order:
        await callback.message.answer("Заявку не знайдено.")
        return

    client = order.get('client', {})
    
    # Меню дій для конкретної заявки
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Структурований звіт (AI)", callback_data=f"report_{order_id}")],
        [InlineKeyboardButton(text="💰 Прорахунок (Калькулятор)", callback_data=f"calc_{order_id}")]
    ])
    
    text = (
        f"👤 **Клієнт:** {client.get('name')}\n"
        f"📞 **Телефон:** {client.get('phone')}\n"
        f"🏠 **Об'єкт:** {client.get('object_type')}\n"
        f"📅 **Дата:** {order.get('created_at')}"
    )
    
    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()

# --- ДІЯ 1: ГЕНЕРАЦІЯ СУХОГО ЗВІТУ ---
@dp.callback_query(F.data.startswith("report_"))
async def generate_report(callback: CallbackQuery):
    order_id = callback.data.split("_")[1]
    order = get_order_by_id(order_id)
    answers = order.get('answers', {})
    client = order.get('client', {})

    await callback.message.answer("⏳ Генерую звіт...")

    if model:
        # НОВИЙ ЖОРСТКИЙ ПРОМПТ
        prompt = f"""
        РОЛЬ: Ти технічний секретар. Твоя єдина задача - структурувати дані.
        ЗАБОРОНЕНО: Вигадувати, додумувати, давати оцінку, писати вступні слова типу "Як досвідчений виконроб".
        
        ЗАВДАННЯ:
        Перетвори цей JSON у чіткий текстовий список українською мовою.
        
        ФОРМАТ ВІДПОВІДІ:
        1. ДАНІ КЛІЄНТА:
           - [Ім'я]
           - [Телефон]
           - [Адреса]
        
        2. ПАРАМЕТРИ ОБ'ЄКТА:
           - [Список відповідей з анкети: питання - відповідь]
        
        ВХІДНІ ДАНІ:
        Клієнт: {json.dumps(client, ensure_ascii=False)}
        Відповіді: {json.dumps(answers, ensure_ascii=False)}
        """
        
        try:
            response = model.generate_content(prompt)
            await callback.message.answer(f"📋 **ЗВІТ ПО ЗАЯВЦІ:**\n\n{response.text}")
        except Exception as e:
            await callback.message.answer(f"Помилка AI: {e}")
    else:
        await callback.message.answer("AI не налаштовано.")
    
    await callback.answer()

# --- ДІЯ 2: ПРОРАХУНОК (ЛОГІКА) ---
@dp.callback_query(F.data.startswith("calc_"))
async def calculate_price(callback: CallbackQuery):
    order_id = callback.data.split("_")[1]
    order = get_order_by_id(order_id)
    answers = order.get('answers', {})
    
    # Спроба знайти площу
    area_str = str(answers.get('area', '0'))
    # Видаляємо все крім цифр
    area_clean = ''.join(filter(str.isdigit, area_str))
    area = int(area_clean) if area_clean else 0
    
    # Примітивна логіка для прикладу (можна ускладнити)
    base_price = 100 # Ціна за м2 (умовна робота)
    material_coef = 1.0
    
    if "Ламінат" in str(answers): material_coef += 0.2
    if "Паркет" in str(answers): material_coef += 0.5
    if "Натяжна" in str(answers): material_coef += 0.1
    
    total = area * base_price * material_coef * 40 # *40 курс (умовно)
    
    text = (
        f"💰 **Попередній розрахунок:**\n"
        f"📏 Площа: {area} м²\n"
        f"📊 Коефіцієнт складності: {material_coef}\n"
        f"--------------------------\n"
        f"💵 **Орієнтовна вартість:** {total:,.0f} грн\n\n"
        f"*(Цей розрахунок є автоматичним і потребує уточнення майстром)*"
    )
    
    await callback.message.answer(text)
    await callback.answer()

# --- СТАНДАРТНІ ХЕНДЛЕРИ ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    kb = [[KeyboardButton(text="📝 Заповнити анкету", web_app=WebAppInfo(url=WEBAPP_URL))]]
    await message.answer(
        "Вітаю! Я бот для збору даних.\n"
        "Для клієнта: натисніть кнопку внизу.\n"
        "Для менеджера: введіть /admin щоб бачити заявки.", 
        reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    )

@dp.message(F.content_type == ContentType.WEB_APP_DATA)
async def web_app_data_handler(message: Message):
    try:
        data = json.loads(message.web_app_data.data)
        save_order(data)
        await message.answer("✅ Дані успішно збережено! Менеджер скоро зв'яжеться з вами.")
        
        # Повідомляємо адміна (тебе), що прийшла нова заявка
        # await bot.send_message(chat_id="ТВІЙ_ID", text="🔔 Нова заявка! Натисни /admin")
        
    except Exception as e:
        await message.answer(f"Помилка: {e}")

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
