import os
import json
import logging
import sys
import math
import re
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

from security import ADMIN_PASSWORD, MASTER_ADMIN_ID, load_auth, save_auth, is_authorized

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

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash-lite')
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

def save_to_sheet(data):
    sheet = get_google_sheet()
    if not sheet: return False
    try:
        c = data.get('client', {})
        answers = json.dumps(data, ensure_ascii=False)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        area = c.get('area', '0')
        address_full = f"{c.get('address')} ({area} м² | Пов: {c.get('floor', '1')} | Ліфт: {c.get('elevator', 'Немає')})"
        
        row = [timestamp, c.get('name'), c.get('phone'), c.get('object_type'), address_full, answers, ""]
        sheet.append_row(row)
        return True
    except Exception as e:
        logging.error(f"Save Error: {e}")
        return False

def save_report_to_cell(row_id, report_text):
    sheet = get_google_sheet()
    if not sheet: return
    try:
        sheet.update_cell(row_id, 7, report_text)
    except Exception as e:
        logging.error(f"Report Save Error: {e}")

def get_cached_report(row_id):
    sheet = get_google_sheet()
    if not sheet: return None
    try:
        return sheet.cell(row_id, 7).value
    except:
        return None

# --- КАЛЬКУЛЯТОР ВАРТОСТІ ---
def calculate_budget(data_json):
    costs = {
        "rough": [0, 0, 0],      
        "electric": [0, 0, 0],   
        "doors": [0, 0, 0],      
        "rooms": [0, 0, 0],      
        "baths": [0, 0, 0],
        "logistics": [0, 0, 0]
    }
    
    client = data_json.get("client", {})
    answers = data_json.get("answers", {})
    measurements = answers.get("measurements", {})
    
    total_area = float(client.get("area", 0) or 0)
    floor = int(client.get("floor", 1) or 1)
    elevator = client.get("elevator", "Немає")
    
    def get_sq(zone_id, key):
        try: return float(measurements.get(zone_id, {}).get(key, 0))
        except: return 0.0

    # 1. ЧОРНОВІ РОБОТИ
    screed_ans = answers.get("screed_done", "")
    if "Мокра" in screed_ans:
        costs["rough"][0] += total_area * 1100; costs["rough"][1] += total_area * 700; costs["rough"][2] += total_area * 700
    elif "Напівсуха" in screed_ans:
        costs["rough"][0] += total_area * 500; costs["rough"][1] += total_area * 500; costs["rough"][2] += total_area * 500
        
    if answers.get("plumbing_done") == "Ні":
        costs["rough"][0] += total_area * 1100; costs["rough"][1] += total_area * 300; costs["rough"][2] += total_area * 300

    # 2. ЕЛЕКТРИКА
    sockets = 0
    if answers.get('kitchen_needed') != 'Ні': sockets += 10
    if answers.get('hallway_needed') != 'Ні': sockets += 4
    sockets += int(answers.get('rooms_count', 0)) * 8
    sockets += int(answers.get('baths_count', 0)) * 4
    
    warm_floors = answers.get('warm_floor', [])
    sockets += len([w for w in warm_floors if w != 'Не потребується'])
    
    k_other = answers.get("kitchen_other", {})
    for tech in ["Посудомийна машина", "Подрібнювач відходів", "Мікрохвильова піч", "Духова шафа", "Підсвітка робочої поверхні"]:
        if tech in k_other: sockets += 1

    if answers.get("electricity_done") == "Ні":
        costs["electric"][0] += total_area * 1200; costs["electric"][1] += total_area * 800; costs["electric"][2] += total_area * 800
    costs["electric"][0] += sockets * 180; costs["electric"][1] += sockets * 250; costs["electric"][2] += sockets * 250

    # 3. ДВЕРІ
    if answers.get("entrance_door") == "Так":
        costs["doors"][0] += 5000; costs["doors"][1] += 15000; costs["doors"][2] += 50000
        
    int_door = answers.get("interior_door", "")
    doors_count = int(answers.get('rooms_count', 0)) + int(answers.get('baths_count', 0))
    if "Прихований" in int_door:
        costs["doors"][0] += doors_count * 30000; costs["doors"][1] += doors_count * 15000; costs["doors"][2] += doors_count * 27000
    elif "Стандарт" in int_door:
        costs["doors"][0] += doors_count * 3650; costs["doors"][1] += doors_count * 8000; costs["doors"][2] += doors_count * 15000

    # 4. ПОКРИТТЯ ТА ОЗДОБЛЕННЯ
    for zone_id in measurements.keys():
        floor_sq = get_sq(zone_id, "floor")
        wall_sq = get_sq(zone_id, "walls")
        prefix = zone_id.split('_')[0] if "room" not in zone_id and "bath" not in zone_id else zone_id
        is_bath = "bath" in prefix
        
        # --- САНВУЗОЛ ---
        if is_bath:
            tile_sq = floor_sq * 4.5
            costs["baths"][0] += tile_sq * 3000
            costs["baths"][1] += tile_sq * 1800; costs["baths"][2] += tile_sq * 1800
            
            if answers.get(f"{prefix}_toilet", {}).get("type") == "Інсталяція":
                costs["baths"][0] += 4900; costs["baths"][1] += 12000; costs["baths"][2] += 30000
            tub_type = answers.get(f"{prefix}_tub", {}).get("type", "")
            if "Акрил" in tub_type or "Окремостояча" in tub_type:
                costs["baths"][0] += 3800; costs["baths"][1] += 15000; costs["baths"][2] += 80000

        # --- ЖИТЛОВІ ЗОНИ ---
        if not is_bath:
            f_type = answers.get(f"{prefix}_floor", "")
            if isinstance(f_type, dict): f_type = f_type.get("type", "")
            
            if "Ламінат" in f_type:
                costs["rooms"][0] += floor_sq * 405; costs["rooms"][1] += floor_sq * 600; costs["rooms"][2] += floor_sq * 900
            elif "Кварц" in f_type:
                costs["rooms"][0] += floor_sq * 565; costs["rooms"][1] += floor_sq * 1200; costs["rooms"][2] += floor_sq * 1800
            elif "Керамограніт" in f_type or "Плитка" in f_type:
                costs["rooms"][0] += floor_sq * 715; costs["rooms"][1] += floor_sq * 1500; costs["rooms"][2] += floor_sq * 2500
            elif "Паркет" in f_type:
                costs["rooms"][0] += floor_sq * 850; costs["rooms"][1] += floor_sq * 2500; costs["rooms"][2] += floor_sq * 5000
            
            # Стіни та УКОСИ
            w_type = answers.get(f"{prefix}_walls", "")
            slopes_len = wall_sq * 0.35
            w_work = 0; w_mat_min = 0; w_mat_max = 0
            
            if "Шпалери" in w_type:
                w_work = 1000; w_mat_min = 200; w_mat_max = 400
            elif "Фарбування" in w_type:
                w_work = 1865; w_mat_min = 250; w_mat_max = 450
            elif "Штукатурка" in w_type or "Декор" in w_type:
                w_work = 2210; w_mat_min = 500; w_mat_max = 1500
            
            costs["rooms"][0] += wall_sq * w_work; costs["rooms"][1] += wall_sq * w_mat_min; costs["rooms"][2] += wall_sq * w_mat_max
            costs["rooms"][0] += slopes_len * w_work; costs["rooms"][1] += slopes_len * w_mat_min; costs["rooms"][2] += slopes_len * w_mat_max
            
            # Плінтус та тіньовий шов стелі
            if floor_sq > 0:
                perimeter = math.sqrt(floor_sq) * 4
                base_t = answers.get("baseboard", "")
                
                if "Стандартний" in base_t:
                    costs["rooms"][0] += perimeter * 215; costs["rooms"][1] += perimeter * 150; costs["rooms"][2] += perimeter * 150
                elif "Тіньовий" in base_t:
                    costs["rooms"][0] += perimeter * 1000; costs["rooms"][1] += perimeter * 400; costs["rooms"][2] += perimeter * 400
                elif "Прихований" in base_t:
                    costs["rooms"][0] += perimeter * 1600; costs["rooms"][1] += perimeter * 600; costs["rooms"][2] += perimeter * 600
                
                if answers.get("ceiling_shadow") == "Так":
                    costs["rooms"][0] += perimeter * 500 

    # 5. СТЕЛЯ БАЗОВА
    ceil_t = answers.get("ceiling", "")
    if "Натяжна" in ceil_t:
        costs["rooms"][0] += total_area * 300; costs["rooms"][1] += total_area * 390; costs["rooms"][2] += total_area * 390
    elif "Гіпсокартон" in ceil_t:
        costs["rooms"][0] += total_area * 700; costs["rooms"][1] += total_area * 440; costs["rooms"][2] += total_area * 440

    total_work = sum(c[0] for c in costs.values())
    total_mat_min = sum(c[1] for c in costs.values())
    total_mat_max = sum(c[2] for c in costs.values())

    return {
        "costs": costs, "total_work": round(total_work),
        "total_mat_min": round(total_mat_min), "total_mat_max": round(total_mat_max),
        "sockets": sockets
    }

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

def get_main_menu_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Заповнити анкету", web_app=WebAppInfo(url=WEBAPP_URL))],
            [KeyboardButton(text="🔐 Кабінет менеджера")]
        ], resize_keyboard=True
    )

# ==========================================
# ОБРОБНИКИ (КОМАНДИ ТА КНОПКИ)
# ==========================================

# СТАРТ - Перевіряємо авторизацію одразу
@dp.message(Command("start"))
async def cmd_start(message: Message):
    if not is_authorized(message.from_user.id):
        await message.answer(
            f"👋 **Вітаю, {message.from_user.first_name}!**\n\n"
            "🔒 Цей бот є закритим інструментом.\n"
            "Будь ласка, введіть пароль доступу:", 
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True) # Ховаємо будь-які кнопки
        )
        return

    await message.answer(
        f"👋 **Вітаю, {message.from_user.first_name}!**\n\n"
        "Головне меню відкрито. Оберіть дію нижче:", 
        reply_markup=get_main_menu_keyboard(), 
        parse_mode="Markdown"
    )

# Секретна панель супер-адміна
@dp.message(F.text == "Super#secusers")
async def secret_admin_panel(message: Message):
    try: await message.delete() 
    except: pass
    
    if message.from_user.id != MASTER_ADMIN_ID:
        return

    auth_data = load_auth()
    if not auth_data:
        return await message.answer("🕵️‍♂️ База авторизованих користувачів порожня.")

    kb = InlineKeyboardBuilder()
    for uid, info in auth_data.items():
        name = info.get('name', 'Невідомо')
        username = info.get('username', '')
        label = f"❌ {name} (@{username})" if username else f"❌ {name}"
        kb.button(text=label, callback_data=f"revoke_{uid}")
    
    kb.adjust(1)
    await message.answer("🕵️‍♂️ **Секретна панель доступу:**\nНатисніть на людину, щоб забрати в неї доступ.", reply_markup=kb.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("revoke_"))
async def revoke_access(callback: CallbackQuery):
    if callback.from_user.id != MASTER_ADMIN_ID:
        return await callback.answer("⛔️ Немає доступу.", show_alert=True)

    target_uid = callback.data.split("_")[1]
    auth_data = load_auth()
    
    if target_uid in auth_data:
        del auth_data[target_uid]
        save_auth(auth_data)
        await callback.answer("✅ Доступ скасовано!", show_alert=True)
        
        if not auth_data:
            return await callback.message.edit_text("🕵️‍♂️ Всіх користувачів видалено з бази.")
            
        kb = InlineKeyboardBuilder()
        for uid, info in auth_data.items():
            name = info.get('name', 'Невідомо')
            username = info.get('username', '')
            label = f"❌ {name} (@{username})" if username else f"❌ {name}"
            kb.button(text=label, callback_data=f"revoke_{uid}")
        kb.adjust(1)
        await callback.message.edit_reply_markup(reply_markup=kb.as_markup())

# КАБІНЕТ МЕНЕДЖЕРА
@dp.message(F.text == "🔐 Кабінет менеджера")
@dp.message(Command("admin"))
async def open_admin_panel(message: Message):
    if not is_authorized(message.from_user.id):
        return await message.answer("⛔️ **Доступ заборонено.**\nБудь ласка, введіть пароль.", parse_mode="Markdown")

    kb = get_orders_keyboard()
    if kb: await message.answer("📂 **Список активних заявок:**", reply_markup=kb)
    else: await message.answer("📭 Список заявок порожній.")

# ==========================================
# ПЕРЕХОПЛЕННЯ ТЕКСТУ (ЛОГІКА ПАРОЛІВ ТА ЛОГІВ)
# ==========================================
@dp.message(F.text)
async def process_password_attempts(message: Message):
    user_id = message.from_user.id
    
    # Якщо людина вже має доступ - просто ігноруємо випадковий текст
    if is_authorized(user_id):
        return
        
    # Людина БЕЗ доступу ввела правильний пароль
    if message.text == ADMIN_PASSWORD:
        auth_data = load_auth()
        auth_data[str(user_id)] = {
            "name": message.from_user.full_name,
            "username": message.from_user.username or "немає_юзернейму"
        }
        save_auth(auth_data)
        
        try: await message.delete() 
        except: pass
        
        await message.answer("✅ **Доступ дозволено!** Ваші дані збережено. Головне меню відкрито.", reply_markup=get_main_menu_keyboard(), parse_mode="Markdown")
        
        # Лог УСПІХУ (надсилаємо тобі, якщо зайшов хтось інший)
        if user_id != MASTER_ADMIN_ID:
            log_text = (
                f"🟢 **УСПІШНА АВТОРИЗАЦІЯ**\n\n"
                f"👤 **Ім'я:** {message.from_user.full_name}\n"
                f"🔖 **Username:** @{message.from_user.username or 'немає'}\n"
                f"🆔 **ID:** `{user_id}`"
            )
            try: await bot.send_message(MASTER_ADMIN_ID, log_text, parse_mode="Markdown")
            except: pass

    # Людина БЕЗ доступу ввела НЕПРАВИЛЬНИЙ пароль
    else:
        try: await message.delete() 
        except: pass
        
        await message.answer("⛔️ **Невірний пароль.** Доступ заборонено.", parse_mode="Markdown")
        
        # Лог НЕВДАЧІ (надсилаємо тобі завжди)
        log_text = (
            f"🔴 **НЕВДАЛА СПРОБА ВХОДУ**\n\n"
            f"👤 **Ім'я:** {message.from_user.full_name}\n"
            f"🔖 **Username:** @{message.from_user.username or 'немає'}\n"
            f"🆔 **ID:** `{user_id}`\n"
            f"🔑 **Введений текст:** `{message.text}`"
        )
        try: await bot.send_message(MASTER_ADMIN_ID, log_text, parse_mode="Markdown")
        except: pass


# ==========================================
# ОБРОБНИКИ КНОПОК ЗАЯВОК
# ==========================================
@dp.callback_query(F.data == "show_list")
async def refresh_list(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id): return await callback.answer("⛔️ Доступ заборонено", show_alert=True)
    kb = get_orders_keyboard()
    msg = "📂 **Список заявок:**" if kb else "📭 Порожньо."
    await callback.message.edit_text(msg, reply_markup=kb)

@dp.callback_query(F.data.startswith("view_"))
async def view_order(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id): return await callback.answer("⛔️ Доступ заборонено", show_alert=True)
    
    row_id = int(callback.data.split("_")[1])
    sheet = get_google_sheet()
    if not sheet: return

    try:
        row_data = sheet.row_values(row_id)
        name = row_data[1] if len(row_data) > 1 else "-"
        phone = row_data[2] if len(row_data) > 2 else "-"
        obj_type = row_data[3] if len(row_data) > 3 else "-"
        address = row_data[4] if len(row_data) > 4 else "-"
        existing_report = row_data[6] if len(row_data) > 6 else ""

        text = (
            f"👤 **Клієнт:** {name}\n📞 **Телефон:** `{phone}`\n"
            f"🏠 **Об'єкт:** {obj_type}\n📍 **Адреса / Деталі:** {address}"
        )

        kb = InlineKeyboardBuilder()
        if existing_report and len(existing_report) > 10:
            kb.button(text="📂 Відкрити ТЗ", callback_data=f"showrep_{row_id}")
        else:
            kb.button(text="✨ Згенерувати ТЗ", callback_data=f"gen_{row_id}")
            
        kb.button(text="💰 Прорахувати кошторис", callback_data=f"calc_{row_id}")
        kb.button(text="🗑 Видалити заявку", callback_data=f"del_{row_id}")
        kb.button(text="🔙 Назад", callback_data="show_list")
        kb.adjust(1) 

        await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="Markdown")
    except:
        await callback.message.answer("Помилка даних заявки.")

@dp.callback_query(F.data.startswith("showrep_"))
async def show_saved_report(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id): return await callback.answer("⛔️ Доступ заборонено", show_alert=True)
    
    row_id = int(callback.data.split("_")[1])
    report = get_cached_report(row_id)
    if report:
        kb = InlineKeyboardBuilder()
        kb.button(text="🔄 Перегенерувати", callback_data=f"gen_{row_id}")
        kb.button(text="🔙 Назад до заявки", callback_data=f"view_{row_id}")
        kb.adjust(1)
        await callback.message.edit_text(f"📋 <b>ЗБЕРЕЖЕНИЙ ЗВІТ:</b>\n\n{report}", reply_markup=kb.as_markup(), parse_mode="HTML")
    else:
        await callback.answer("Звіт не знайдено.", show_alert=True)

@dp.callback_query(F.data.startswith("gen_"))
async def generate_report_action(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id): return await callback.answer("⛔️ Доступ заборонено", show_alert=True)
    
    row_id = int(callback.data.split("_")[1])
    sheet = get_google_sheet()
    
    await callback.message.answer("⏳ **Генеруємо технічне завдання...**")
    try:
        row_data = sheet.row_values(row_id)
        raw_answers = row_data[5] if len(row_data) > 5 else "{}"
        
        if model:
            prompt = (
                f"Ти професійний виконроб. Створи гарний та структурований звіт (технічне завдання) по об'єкту на основі даних нижче.\n"
                f"СУВОРА ВИМОГА: Використовуй ВИКЛЮЧНО ті назви приміщень, які є в даних. КАТЕГОРИЧНО ЗАБОРОНЕНО використовувати синоніми.\n"
                f"ФОРМАТУВАННЯ: Використовуй ТІЛЬКИ теги <b>жирний</b> та <i>курсив</i>. Для нових рядків використовуй звичайний перенос, для списків - звичайне тире (-). "
                f"КАТЕГОРИЧНО ЗАБОРОНЕНО використовувати теги <br>, <ul>, <li> та символи Markdown (*, _, #, `).\n\n"
                f"Дані: {raw_answers}"
            )
            response = model.generate_content(prompt)
            
            report_text = response.text.replace("```html", "").replace("```", "").strip()
            report_text = re.sub(r'<br\s*/?>', '\n', report_text, flags=re.IGNORECASE)
            report_text = re.sub(r'</?ul>', '', report_text, flags=re.IGNORECASE)
            report_text = re.sub(r'<li>', '- ', report_text, flags=re.IGNORECASE)
            report_text = re.sub(r'</li>', '\n', report_text, flags=re.IGNORECASE)
            report_text = report_text.replace("**", "").replace("*", "")
            
            save_report_to_cell(row_id, report_text)
            
            kb = InlineKeyboardBuilder()
            kb.button(text="🔙 Назад до заявки", callback_data=f"view_{row_id}")
            
            await callback.message.answer(f"📋 <b>ПАСПОРТ ОБ'ЄКТА</b>\n\n{report_text}", reply_markup=kb.as_markup(), parse_mode="HTML")
        else:
            await callback.message.answer("⚠️ AI не підключено.")
    except Exception as e:
        await callback.message.answer(f"Помилка: {e}")
    await callback.answer()

@dp.callback_query(F.data.startswith("calc_"))
async def run_calculation(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id): return await callback.answer("⛔️ Доступ заборонено", show_alert=True)
    
    row_id = int(callback.data.split("_")[1])
    sheet = get_google_sheet()
    
    await callback.answer("Аналізуємо заміри та рахуємо... ⏳")
    
    try:
        row_data = sheet.row_values(row_id)
        raw_data = row_data[5] if len(row_data) > 5 else "{}"
        data_json = json.loads(raw_data)
        
        b = calculate_budget(data_json)
        c = b["costs"]
        
        client_info = data_json.get("client", {})
        measurements = data_json.get("answers", {}).get("measurements", {})
        
        total_area = client_info.get("area", 0)
        floor = client_info.get("floor", 1)
        elevator = client_info.get("elevator", "Немає")
        
        details = f"📐 **ОБСЯГИ ТА ЗАМІРИ:**\n"
        details += f"▪️ **Площа загальна:** {total_area} м² (Поверх: {floor} | Ліфт: {elevator})\n"
        details += f"▪️ **Електроточки:** ~{b['sockets']} шт.\n"
        
        if measurements:
            details += f"▪️ **Приміщення:**\n"
            name_map = {"hallway": "Передпокій", "kitchen": "Кухня", "balcony": "Балкон", "wardrobe": "Гардероб", "basement": "Підвал", "attic": "Горище"}
            for k, v in measurements.items():
                f_sq = v.get("floor", 0)
                w_sq = v.get("walls", 0)
                
                if "room_" in k: n_name = f"Кімната {k.split('_')[1]}"
                elif "bath_" in k: n_name = f"Санвузол {k.split('_')[1]}"
                else: n_name = name_map.get(k, k)
                
                if "bath" in k:
                    details += f"  - {n_name}: підлога {f_sq} м² *(плитка вкругову ~{float(f_sq)*4.5:.1f} м²)*\n"
                else:
                    details += f"  - {n_name}: підлога {f_sq} м² | стіни {w_sq} м² *(+укоси)*\n"
        
        text = f"💰 **ДЕТАЛЬНИЙ КОШТОРИС ОБ'ЄКТА**\n\n{details}\n"
        text += f"💵 **ФІНАНСОВИЙ РОЗПОДІЛ:**\n\n"
            
        if c["rough"][0] > 0:
            text += f"🧱 **Чорнові роботи (Стяжка, Каналізація):**\nРобота: {c['rough'][0]:,.0f} грн | Матеріали: ~{c['rough'][1]:,.0f} грн\n\n"
        
        text += f"⚡️ **Електрика (Точки + розводка):**\nРобота: {c['electric'][0]:,.0f} грн | Матеріали: ~{c['electric'][1]:,.0f} грн\n\n"
        
        if c["doors"][0] > 0:
            text += f"🚪 **Двері (Вхідні + Міжкімнатні):**\nРобота: {c['doors'][0]:,.0f} грн | Матеріали: {c['doors'][1]:,.0f} - {c['doors'][2]:,.0f} грн\n\n"
            
        text += f"🛋 **Оздоблення кімнат (Підлога, Стіни+Укоси, Стеля, Плінтус):**\nРобота: {c['rooms'][0]:,.0f} грн | Матеріали: {c['rooms'][1]:,.0f} - {c['rooms'][2]:,.0f} грн\n\n"
        
        if c["baths"][0] > 0:
            text += f"🛁 **Санвузли (Плитка, Сантехніка):**\nРобота: {c['baths'][0]:,.0f} грн | Матеріали: {c['baths'][1]:,.0f} - {c['baths'][2]:,.0f} грн\n\n"
            
        text += f"📊 **ПІДСУМКОВИЙ БЮДЖЕТ:**\n"
        text += f"🛠 **Робота:** ~{b['total_work']:,.0f} грн\n"
        text += f"📦 **Матеріали:** від {b['total_mat_min']:,.0f} грн до {b['total_mat_max']:,.0f} грн\n"
        text += f"💵 **Всього:** від **{(b['total_work'] + b['total_mat_min']):,.0f} грн** до **{(b['total_work'] + b['total_mat_max']):,.0f} грн**"
        
        kb = InlineKeyboardBuilder()
        kb.button(text="🔙 Назад до заявки", callback_data=f"view_{row_id}")
        
        await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="Markdown")
        
    except Exception as e:
        await callback.message.answer(f"Помилка розрахунку: {e}")

@dp.callback_query(F.data.startswith("del_"))
async def delete_order(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id): return await callback.answer("⛔️ Доступ заборонено", show_alert=True)
    
    row_id = int(callback.data.split("_")[1])
    sheet = get_google_sheet()
    try:
        sheet.delete_rows(row_id)
        await callback.answer("✅ Видалено!", show_alert=True)
        await refresh_list(callback)
    except Exception as e:
        await callback.answer(f"Помилка: {e}", show_alert=True)

@dp.message(F.content_type == ContentType.WEB_APP_DATA)
async def web_app_data_handler(message: Message):
    if not is_authorized(message.from_user.id): return await message.answer("⛔️ Доступ заборонено.")
    
    data = json.loads(message.web_app_data.data)
    if save_to_sheet(data):
        await message.answer("✅ **Заявку прийнято!**", parse_mode="Markdown")
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
