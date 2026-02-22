import os
import json
import logging
import sys
import math
import re
import html
import time
import csv
import io
from datetime import datetime
import asyncio
import hashlib
import hmac
from urllib.parse import parse_qsl

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ContentType, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials

from security import ADMIN_PASSWORD, MASTER_ADMIN_ID, is_authorized, get_all_authorized_users, add_authorized_user, remove_authorized_user, clear_auth_cache
from lexicon import GEMINI_PROMPT, MSG_START_AUTH, MSG_START_MAIN, MSG_AUTH_SUCCESS, MSG_AUTH_FAIL, MSG_ACCESS_DENIED, MSG_ACCESS_DENIED_ALERT

# --- КОНФІГУРАЦІЯ ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GOOGLE_CREDS_JSON = os.getenv('GOOGLE_CREDS_JSON') 
SPREADSHEET_NAME = "remonts sheets" 

WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = 10000
WEBHOOK_URL = os.getenv('RENDER_EXTERNAL_URL')
WEBHOOK_PATH = "/webhook"
WEBAPP_URL = "https://siteremontt.vercel.app"

WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET', 'DefaultSecretToken12345')

logging.basicConfig(level=logging.INFO, stream=sys.stdout)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash-lite')
else:
    model = None

# ==========================================
# БЕЗПЕКА ТА АНТИСПАМ (THROTTLING)
# ==========================================
_THROTTLE_CACHE = {}

def is_throttled(user_id, action, delay=10):
    """Блокує спам-натискання кнопок менеджерами."""
    key = f"{user_id}_{action}"
    now = time.time()
    if key in _THROTTLE_CACHE and now - _THROTTLE_CACHE[key] < delay:
        return True
    _THROTTLE_CACHE[key] = now
    return False

def validate_telegram_data(init_data: str, bot_token: str):
    """Банківська крипто-перевірка даних від Telegram Mini App."""
    try:
        parsed_data = dict(parse_qsl(init_data))
        if 'hash' not in parsed_data: return None
        hash_val = parsed_data.pop('hash')
        sorted_data = sorted(parsed_data.items(), key=lambda x: x[0])
        data_check_string = '\n'.join([f"{k}={v}" for k, v in sorted_data])
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if calc_hash == hash_val:
            user_data = json.loads(parsed_data.get('user', '{}'))
            return user_data.get('id')
        return None
    except Exception:
        return None

# ==========================================
# ЖУРНАЛ АУДИТУ (ЛОГИ ДІЙ МЕНЕДЖЕРІВ)
# ==========================================
def _log_action_sync(user_name, action):
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        doc = gspread.authorize(creds).open(SPREADSHEET_NAME)
        try:
            ws = doc.worksheet("Logs")
        except gspread.exceptions.WorksheetNotFound:
            ws = doc.add_worksheet(title="Logs", rows="100", cols="3")
            ws.append_row(["Дата і Час", "Менеджер", "Дія"])
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ws.append_row([timestamp, user_name, action])
    except Exception as e:
        logging.error(f"Audit Log Error: {e}")

async def async_log_action(user_name, action):
    """Асинхронний запис логу, щоб не гальмувати бота."""
    asyncio.create_task(asyncio.to_thread(_log_action_sync, user_name, action))


# ==========================================
# ДИНАМІЧНИЙ ПРАЙС-ЛИСТ З GOOGLE SHEETS
# ==========================================
_PRICES_CACHE = None

def _get_prices_sync():
    global _PRICES_CACHE
    if _PRICES_CACHE is not None:
        return _PRICES_CACHE

    DEFAULT_PRICES = {
        "logistics_base": [150, 0, 0], "logistics_stair": [30, 0, 0], "logistics_elev": [10, 0, 0],
        "screed_wet": [1100, 700, 700], "screed_dry": [500, 500, 500],
        "plumbing": [1100, 300, 300],
        "electric_wire": [1200, 800, 800], "electric_point": [180, 250, 250],
        "door_entrance": [5000, 15000, 50000], "door_hidden": [30000, 15000, 27000], "door_std": [3650, 8000, 15000],
        "bath_tile": [3000, 1800, 1800], "bath_install": [4900, 12000, 30000], "bath_tub": [3800, 15000, 80000],
        "room_lam": [405, 600, 900], "room_quartz": [565, 1200, 1800], "room_keram": [715, 1500, 2500], "room_parket": [850, 2500, 5000],
        "wall_paper": [1000, 200, 400], "wall_paint": [1865, 250, 450], "wall_stucco": [2210, 500, 1500],
        "base_std": [215, 150, 150], "base_shadow": [1000, 400, 400], "base_hidden": [1600, 600, 600],
        "ceil_shadow_add": [500, 0, 0], "ceil_stretch": [300, 390, 390], "ceil_gips": [700, 440, 440]
    }

    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        doc = client.open(SPREADSHEET_NAME)

        try:
            sheet = doc.worksheet("Прайс")
        except gspread.exceptions.WorksheetNotFound:
            sheet = doc.add_worksheet(title="Прайс", rows="100", cols="4")
            sheet.append_row(["Ключ (НЕ ЗМІНЮВАТИ)", "Робота", "Матеріал_мін", "Матеріал_макс"])
            rows_to_add = [[k, v[0], v[1], v[2]] for k, v in DEFAULT_PRICES.items()]
            sheet.append_rows(rows_to_add)
            _PRICES_CACHE = DEFAULT_PRICES
            return DEFAULT_PRICES

        records = sheet.get_all_values()
        loaded_prices = {}
        for row in records[1:]:
            if len(row) >= 1 and row[0]:
                k = row[0].strip()
                w = float(row[1]) if len(row) > 1 and row[1].replace('.','',1).isdigit() else 0
                m1 = float(row[2]) if len(row) > 2 and row[2].replace('.','',1).isdigit() else 0
                m2 = float(row[3]) if len(row) > 3 and row[3].replace('.','',1).isdigit() else 0
                loaded_prices[k] = [w, m1, m2]

        final_prices = DEFAULT_PRICES.copy()
        final_prices.update(loaded_prices)
        _PRICES_CACHE = final_prices
        return final_prices
    except Exception as e:
        return DEFAULT_PRICES

async def async_get_prices():
    return await asyncio.to_thread(_get_prices_sync)


# ==========================================
# СИНХРОННІ ОПЕРАЦІЇ GOOGLE SHEETS
# ==========================================
def _get_google_sheet():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client.open(SPREADSHEET_NAME).sheet1
    except Exception as e:
        return None

def _save_to_sheet_sync(data):
    sheet = _get_google_sheet()
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
        return False

def _update_row_sync(row_id, data):
    sheet = _get_google_sheet()
    if not sheet: return False
    try:
        c = data.get('client', {})
        answers_json = json.dumps(data, ensure_ascii=False)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M") + " (Оновлено)"
        area = c.get('area', '0')
        address_full = f"{c.get('address')} ({area} м² | Пов: {c.get('floor', '1')} | Ліфт: {c.get('elevator', 'Немає')})"
        
        row_data = [timestamp, c.get('name'), c.get('phone'), c.get('object_type'), address_full, answers_json, ""]
        cell_list = sheet.range(f'A{row_id}:G{row_id}')
        for i, val in enumerate(row_data): cell_list[i].value = val
        sheet.update_cells(cell_list)
        return True
    except Exception as e:
        return False

def _get_row_data_sync(row_id):
    sheet = _get_google_sheet()
    if sheet:
        try: return sheet.row_values(row_id)
        except: return None
    return None

def _save_report_sync(row_id, text):
    sheet = _get_google_sheet()
    if sheet:
        try: sheet.update_cell(row_id, 7, text)
        except Exception: pass

def _delete_row_sync(row_id):
    sheet = _get_google_sheet()
    if sheet:
        try: sheet.delete_rows(row_id)
        except Exception: pass

def _get_orders_keyboard_sync(page=1):
    sheet = _get_google_sheet()
    if not sheet: return None
    try:
        rows = sheet.get_all_values()
        if not rows or len(rows) < 2: return None

        data_rows = rows[1:] 
        per_page = 10
        total_pages = math.ceil(len(data_rows) / per_page)
        
        if page < 1: page = 1
        if page > total_pages: page = total_pages
        
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        page_rows = data_rows[start_idx:end_idx]

        builder = InlineKeyboardBuilder()
        for i, row in enumerate(page_rows):
            actual_row_id = start_idx + i + 2 
            name = row[1] if len(row) > 1 else "Невідомо"
            phone = row[2] if len(row) > 2 else "..."
            builder.button(text=f"{name} | {phone}", callback_data=f"view_{actual_row_id}")

        builder.adjust(1)
        nav_buttons = []
        if page > 1: nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"page_{page-1}"))
        nav_buttons.append(InlineKeyboardButton(text=f"Стор. {page}/{total_pages}", callback_data="ignore"))
        if page < total_pages: nav_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"page_{page+1}"))

        if len(data_rows) > per_page: builder.row(*nav_buttons)
        builder.row(InlineKeyboardButton(text="🔄 Оновити список", callback_data=f"page_{page}"))
        return builder.as_markup()
    except Exception as e:
        return None

async def async_save_to_sheet(data): return await asyncio.to_thread(_save_to_sheet_sync, data)
async def async_update_row(row_id, data): return await asyncio.to_thread(_update_row_sync, row_id, data)
async def async_get_row_data(row_id): return await asyncio.to_thread(_get_row_data_sync, row_id)
async def async_save_report(row_id, text): await asyncio.to_thread(_save_report_sync, row_id, text)
async def async_delete_row(row_id): await asyncio.to_thread(_delete_row_sync, row_id)
async def async_get_orders_keyboard(page=1): return await asyncio.to_thread(_get_orders_keyboard_sync, page)

# ==========================================
# API ДЛЯ WEBAPP
# ==========================================
async def api_get_order(request):
    headers = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type' }
    if request.method == 'OPTIONS': return web.Response(headers=headers)
        
    row_id = request.rel_url.query.get('id')
    if not row_id: return web.json_response({"error": "No ID"}, status=400, headers=headers)
    
    row_data = await async_get_row_data(int(row_id))
    if not row_data: return web.json_response({"error": "Not found"}, status=404, headers=headers)
        
    try:
        payload = json.loads(row_data[5])
        return web.json_response(payload, headers=headers)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500, headers=headers)

async def api_save_order(request):
    headers = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'POST, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type, X-Telegram-Init-Data' }
    if request.method == 'OPTIONS': return web.Response(headers=headers)
        
    init_data = request.headers.get('X-Telegram-Init-Data')
    if not init_data: return web.json_response({"error": "Unauthorized"}, status=401, headers=headers)
        
    user_id = validate_telegram_data(init_data, BOT_TOKEN)
    if not user_id or not is_authorized(user_id):
        return web.json_response({"error": "Access Denied"}, status=403, headers=headers)
        
    try:
        data = await request.json()
        edit_id = data.get("edit_id")
        
        # Визначаємо ім'я менеджера для Логів
        auth_users = get_all_authorized_users()
        manager_name = auth_users.get(str(user_id), {}).get("name", f"ID: {user_id}")
        
        if edit_id:
            async def background_save():
                if await async_update_row(int(edit_id), data):
                    await async_log_action(manager_name, f"✏️ Відредагував об'єкт (Рядок {edit_id})")
                    try: await bot.send_message(chat_id=user_id, text=f"✅ **Заявку оновлено!** (Рядок {edit_id})", parse_mode="Markdown")
                    except: pass
            
            asyncio.create_task(background_save())
            return web.json_response({"success": True}, headers=headers)
            
        return web.json_response({"error": "No edit_id"}, status=400, headers=headers)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500, headers=headers)

# ==========================================
# КАЛЬКУЛЯТОР ВАРТОСТІ 
# ==========================================
def calculate_budget(data_json, PRICES):
    costs = { "rough": [0,0,0], "electric": [0,0,0], "doors": [0,0,0], "rooms": [0,0,0], "baths": [0,0,0], "logistics": [0,0,0] }
    client = data_json.get("client", {})
    answers = data_json.get("answers", {})
    measurements = answers.get("measurements", {})
    
    total_area = float(client.get("area", 0) or 0)
    floor = int(client.get("floor", 1) or 1)
    elevator = client.get("elevator", "Немає")
    
    def get_sq(zone_id, key):
        try: return float(measurements.get(zone_id, {}).get(key, 0))
        except: return 0.0

    logistics_work = total_area * PRICES["logistics_base"][0]
    if elevator == "Немає" and floor > 1: logistics_work += (total_area * PRICES["logistics_stair"][0] * floor)
    elif elevator == "Пасажирський": logistics_work += (total_area * PRICES["logistics_elev"][0] * floor)
    costs["logistics"][0] += logistics_work

    screed_ans = answers.get("screed_done", "")
    if "Мокра" in screed_ans: 
        costs["rough"][0] += total_area * PRICES["screed_wet"][0]; costs["rough"][1] += total_area * PRICES["screed_wet"][1]; costs["rough"][2] += total_area * PRICES["screed_wet"][2]
    elif "Напівсуха" in screed_ans: 
        costs["rough"][0] += total_area * PRICES["screed_dry"][0]; costs["rough"][1] += total_area * PRICES["screed_dry"][1]; costs["rough"][2] += total_area * PRICES["screed_dry"][2]
        
    if answers.get("plumbing_done") == "Ні": 
        costs["rough"][0] += total_area * PRICES["plumbing"][0]; costs["rough"][1] += total_area * PRICES["plumbing"][1]; costs["rough"][2] += total_area * PRICES["plumbing"][2]

    sockets = 0
    if answers.get('kitchen_needed') != 'Ні': sockets += 10
    if answers.get('hallway_needed') != 'Ні': sockets += 4
    sockets += int(answers.get('rooms_count', 0)) * 8
    sockets += int(answers.get('baths_count', 0)) * 4
    warm_floors = answers.get('warm_floor', [])
    sockets += len([w for w in warm_floors if w != 'Не потребується'])
    for tech in ["Посудомийна машина", "Подрібнювач відходів", "Мікрохвильова піч", "Духова шафа", "Підсвітка робочої поверхні"]:
        if tech in answers.get("kitchen_other", {}): sockets += 1

    if answers.get("electricity_done") == "Ні":
        costs["electric"][0] += total_area * PRICES["electric_wire"][0]; costs["electric"][1] += total_area * PRICES["electric_wire"][1]; costs["electric"][2] += total_area * PRICES["electric_wire"][2]
    costs["electric"][0] += sockets * PRICES["electric_point"][0]; costs["electric"][1] += sockets * PRICES["electric_point"][1]; costs["electric"][2] += sockets * PRICES["electric_point"][2]

    if answers.get("entrance_door") == "Так":
        costs["doors"][0] += PRICES["door_entrance"][0]; costs["doors"][1] += PRICES["door_entrance"][1]; costs["doors"][2] += PRICES["door_entrance"][2]
        
    int_door = answers.get("interior_door", "")
    doors_count = int(answers.get('rooms_count', 0)) + int(answers.get('baths_count', 0))
    if "Прихований" in int_door: 
        costs["doors"][0] += doors_count * PRICES["door_hidden"][0]; costs["doors"][1] += doors_count * PRICES["door_hidden"][1]; costs["doors"][2] += doors_count * PRICES["door_hidden"][2]
    elif "Стандарт" in int_door: 
        costs["doors"][0] += doors_count * PRICES["door_std"][0]; costs["doors"][1] += doors_count * PRICES["door_std"][1]; costs["doors"][2] += doors_count * PRICES["door_std"][2]

    for zone_id in measurements.keys():
        floor_sq = get_sq(zone_id, "floor")
        wall_sq = get_sq(zone_id, "walls")
        prefix = zone_id.split('_')[0] if "room" not in zone_id and "bath" not in zone_id else zone_id
        is_bath = "bath" in prefix
        
        if is_bath:
            tile_sq = floor_sq * 4.5
            costs["baths"][0] += tile_sq * PRICES["bath_tile"][0]; costs["baths"][1] += tile_sq * PRICES["bath_tile"][1]; costs["baths"][2] += tile_sq * PRICES["bath_tile"][2]
            if answers.get(f"{prefix}_toilet", {}).get("type") == "Інсталяція":
                costs["baths"][0] += PRICES["bath_install"][0]; costs["baths"][1] += PRICES["bath_install"][1]; costs["baths"][2] += PRICES["bath_install"][2]
            tub_type = answers.get(f"{prefix}_tub", {}).get("type", "")
            if "Акрил" in tub_type or "Окремостояча" in tub_type:
                costs["baths"][0] += PRICES["bath_tub"][0]; costs["baths"][1] += PRICES["bath_tub"][1]; costs["baths"][2] += PRICES["bath_tub"][2]

        if not is_bath:
            f_type = answers.get(f"{prefix}_floor", "")
            if isinstance(f_type, dict): f_type = f_type.get("type", "")
            
            p_floor = [0,0,0]
            if "Ламінат" in f_type: p_floor = PRICES["room_lam"]
            elif "Кварц" in f_type: p_floor = PRICES["room_quartz"]
            elif "Керамограніт" in f_type or "Плитка" in f_type: p_floor = PRICES["room_keram"]
            elif "Паркет" in f_type: p_floor = PRICES["room_parket"]
            costs["rooms"][0] += floor_sq * p_floor[0]; costs["rooms"][1] += floor_sq * p_floor[1]; costs["rooms"][2] += floor_sq * p_floor[2]
            
            w_type = answers.get(f"{prefix}_walls", "")
            slopes_len = wall_sq * 0.35
            p_wall = [0,0,0]
            if "Шпалери" in w_type: p_wall = PRICES["wall_paper"]
            elif "Фарбування" in w_type: p_wall = PRICES["wall_paint"]
            elif "Штукатурка" in w_type or "Декор" in w_type: p_wall = PRICES["wall_stucco"]
            
            costs["rooms"][0] += wall_sq * p_wall[0]; costs["rooms"][1] += wall_sq * p_wall[1]; costs["rooms"][2] += wall_sq * p_wall[2]
            costs["rooms"][0] += slopes_len * p_wall[0]; costs["rooms"][1] += slopes_len * p_wall[1]; costs["rooms"][2] += slopes_len * p_wall[2]
            
            if floor_sq > 0:
                perimeter = math.sqrt(floor_sq) * 4
                base_t = answers.get("baseboard", "")
                p_base = [0,0,0]
                if "Стандартний" in base_t: p_base = PRICES["base_std"]
                elif "Тіньовий" in base_t: p_base = PRICES["base_shadow"]
                elif "Прихований" in base_t: p_base = PRICES["base_hidden"]
                costs["rooms"][0] += perimeter * p_base[0]; costs["rooms"][1] += perimeter * p_base[1]; costs["rooms"][2] += perimeter * p_base[2]
                if answers.get("ceiling_shadow") == "Так": costs["rooms"][0] += perimeter * PRICES["ceil_shadow_add"][0]

    ceil_t = answers.get("ceiling", "")
    p_ceil = [0,0,0]
    if "Натяжна" in ceil_t: p_ceil = PRICES["ceil_stretch"]
    elif "Гіпсокартон" in ceil_t: p_ceil = PRICES["ceil_gips"]
    costs["rooms"][0] += total_area * p_ceil[0]; costs["rooms"][1] += total_area * p_ceil[1]; costs["rooms"][2] += total_area * p_ceil[2]

    total_work = sum(c[0] for c in costs.values())
    total_mat_min = sum(c[1] for c in costs.values())
    total_mat_max = sum(c[2] for c in costs.values())

    return { "costs": costs, "total_work": round(total_work), "total_mat_min": round(total_mat_min), "total_mat_max": round(total_mat_max), "sockets": sockets }

def get_main_menu_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📝 Заповнити анкету", web_app=WebAppInfo(url=WEBAPP_URL))], [KeyboardButton(text="🔐 Кабінет менеджера")]], resize_keyboard=True)

# ==========================================
# ОБРОБНИКИ КОМАНД
# ==========================================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    if not is_authorized(message.from_user.id): return await message.answer(MSG_START_AUTH.format(name=message.from_user.first_name), parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True))
    await message.answer(MSG_START_MAIN.format(name=message.from_user.first_name), reply_markup=get_main_menu_keyboard(), parse_mode="Markdown")

@dp.message(F.text == "Super#secusers")
async def secret_admin_panel(message: Message):
    try: await message.delete() 
    except: pass
    if message.from_user.id != MASTER_ADMIN_ID: return
    auth_data = get_all_authorized_users()
    if not auth_data: return await message.answer("🕵️‍♂️ База порожня.")
    kb = InlineKeyboardBuilder()
    for uid, info in auth_data.items(): kb.button(text=f"❌ {info.get('name', '')} (@{info.get('username', '')})", callback_data=f"revoke_{uid}")
    kb.adjust(1)
    await message.answer("🕵️‍♂️ **Секретна панель доступу:**", reply_markup=kb.as_markup(), parse_mode="Markdown")

@dp.message(F.text == "Super#reload_cache")
async def cmd_reload_cache(message: Message):
    try: await message.delete() 
    except: pass
    if message.from_user.id != MASTER_ADMIN_ID: return
    clear_auth_cache()
    global _PRICES_CACHE
    _PRICES_CACHE = None
    await message.answer("🔄 **Кеш успішно очищено!**\nБот оновив ціни і перечитав доступи.", parse_mode="Markdown")

# НОВИЙ ФУНКЦІОНАЛ: МИТТЄВИЙ БЕКАП
@dp.message(F.text == "Super#backup")
async def cmd_backup(message: Message):
    try: await message.delete() 
    except: pass
    if message.from_user.id != MASTER_ADMIN_ID: return

    await message.answer("⏳ Збираю дані для резервної копії...")
    
    def _get_csv():
        sheet = _get_google_sheet()
        if not sheet: return None
        data = sheet.get_all_values()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerows(data)
        # Додаємо BOM, щоб Excel правильно читав кирилицю
        return ("\xef\xbb\xbf" + output.getvalue()).encode('utf-8')
        
    try:
        csv_data = await asyncio.to_thread(_get_csv)
        if csv_data:
            date_str = datetime.now().strftime("%Y_%m_%d_%H%M")
            file = BufferedInputFile(csv_data, filename=f"remont_base_{date_str}.csv")
            await message.answer_document(file, caption="📦 **Ваш резервний бекап бази даних!**\nЙого можна відкрити в Excel або завантажити назад в Google Таблиці.", parse_mode="Markdown")
            await async_log_action("ВЛАСНИК", "📥 Завантажив бекап бази (CSV)")
        else:
            await message.answer("❌ Помилка підключення до бази.")
    except Exception as e:
        await message.answer(f"❌ Помилка бекапу: {e}")

@dp.callback_query(F.data.startswith("revoke_"))
async def revoke_access(callback: CallbackQuery):
    if callback.from_user.id != MASTER_ADMIN_ID: return await callback.answer(MSG_ACCESS_DENIED_ALERT, show_alert=True)
    target_uid = callback.data.split("_")[1]
    if remove_authorized_user(target_uid):
        await callback.answer("✅ Доступ скасовано!", show_alert=True)
        auth_data = get_all_authorized_users()
        kb = InlineKeyboardBuilder()
        for uid, info in auth_data.items(): kb.button(text=f"❌ {info.get('name', '')}", callback_data=f"revoke_{uid}")
        kb.adjust(1)
        await callback.message.edit_reply_markup(reply_markup=kb.as_markup())

@dp.message(F.text == "🔐 Кабінет менеджера")
@dp.message(Command("admin"))
async def open_admin_panel(message: Message):
    if not is_authorized(message.from_user.id): return await message.answer(MSG_ACCESS_DENIED, parse_mode="Markdown")
    await message.answer("⏳ Завантажую базу даних...")
    kb = await async_get_orders_keyboard(page=1)
    if kb: await message.answer("📂 **Список активних заявок:**", reply_markup=kb)
    else: await message.answer("📭 Список заявок порожній.")

@dp.message(F.text)
async def process_password_attempts(message: Message):
    user_id = message.from_user.id
    if is_authorized(user_id): return
    if message.text == ADMIN_PASSWORD:
        add_authorized_user(user_id, message.from_user.full_name, message.from_user.username or "немає_юзернейму")
        try: await message.delete() 
        except: pass
        await message.answer(MSG_AUTH_SUCCESS, reply_markup=get_main_menu_keyboard(), parse_mode="Markdown")
        if user_id != MASTER_ADMIN_ID:
            safe_name = html.escape(message.from_user.full_name)
            safe_username = html.escape(message.from_user.username or 'немає')
            log_text = f"🟢 <b>УСПІШНА АВТОРИЗАЦІЯ</b>\n\n👤 <b>Ім'я:</b> {safe_name}\n🔖 <b>Username:</b> @{safe_username}\n🆔 <b>ID:</b> <code>{user_id}</code>"
            try: await bot.send_message(MASTER_ADMIN_ID, log_text, parse_mode="HTML")
            except: pass
    else:
        try: await message.delete() 
        except: pass
        await message.answer(MSG_AUTH_FAIL, parse_mode="Markdown")
        safe_name = html.escape(message.from_user.full_name)
        safe_username = html.escape(message.from_user.username or 'немає')
        safe_text = html.escape(message.text)
        log_text = f"🔴 <b>НЕВДАЛА СПРОБА ВХОДУ</b>\n\n👤 <b>Ім'я:</b> {safe_name}\n🔖 <b>Username:</b> @{safe_username}\n🆔 <b>ID:</b> <code>{user_id}</code>\n🔑 <b>Введено:</b> <code>{safe_text}</code>"
        try: await bot.send_message(MASTER_ADMIN_ID, log_text, parse_mode="HTML")
        except: pass

@dp.callback_query(F.data.startswith("page_"))
async def change_page(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id): return await callback.answer(MSG_ACCESS_DENIED_ALERT, show_alert=True)
    page = int(callback.data.split("_")[1])
    kb = await async_get_orders_keyboard(page=page)
    msg = "📂 **Список заявок:**" if kb else "📭 Порожньо."
    await callback.message.edit_text(msg, reply_markup=kb)

@dp.callback_query(F.data == "show_list")
async def show_first_page(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id): return await callback.answer(MSG_ACCESS_DENIED_ALERT, show_alert=True)
    kb = await async_get_orders_keyboard(page=1)
    msg = "📂 **Список заявок:**" if kb else "📭 Порожньо."
    await callback.message.edit_text(msg, reply_markup=kb)

@dp.callback_query(F.data == "ignore")
async def ignore_callback(callback: CallbackQuery):
    await callback.answer()

@dp.callback_query(F.data.startswith("view_"))
async def view_order(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id): return await callback.answer(MSG_ACCESS_DENIED_ALERT, show_alert=True)
    row_id = int(callback.data.split("_")[1])
    row_data = await async_get_row_data(row_id)
    if not row_data: return await callback.message.answer("Помилка: не вдалося завантажити заявку.")

    name = row_data[1] if len(row_data) > 1 else "-"
    phone = row_data[2] if len(row_data) > 2 else "-"
    obj_type = row_data[3] if len(row_data) > 3 else "-"
    address = row_data[4] if len(row_data) > 4 else "-"
    existing_report = row_data[6] if len(row_data) > 6 else ""

    text = f"👤 **Клієнт:** {name}\n📞 **Телефон:** `{phone}`\n🏠 **Об'єкт:** {obj_type}\n📍 **Адреса:** {address}"
    kb = InlineKeyboardBuilder()
    if existing_report and len(existing_report) > 10: kb.button(text="📂 Відкрити ТЗ", callback_data=f"showrep_{row_id}")
    else: kb.button(text="✨ Згенерувати ТЗ", callback_data=f"gen_{row_id}")
    kb.button(text="💰 Прорахувати кошторис", callback_data=f"calc_{row_id}")
    kb.button(text="✏️ Редагувати анкету", web_app=WebAppInfo(url=f"{WEBAPP_URL}?edit_id={row_id}"))
    kb.button(text="🗑 Видалити заявку", callback_data=f"del_{row_id}")
    kb.button(text="🔙 Назад", callback_data="show_list")
    kb.adjust(1) 
    await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("showrep_"))
async def show_saved_report(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id): return await callback.answer(MSG_ACCESS_DENIED_ALERT, show_alert=True)
    row_id = int(callback.data.split("_")[1])
    row_data = await async_get_row_data(row_id)
    report = row_data[6] if row_data and len(row_data) > 6 else None
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
    user_id = callback.from_user.id
    if not is_authorized(user_id): return await callback.answer(MSG_ACCESS_DENIED_ALERT, show_alert=True)
    
    # ЗАХИСТ ВІД ЗАЛИПАННЯ АБО СПАМУ (ШІ працює повільно, не даємо клікати 10 разів)
    if is_throttled(user_id, "generate_tz", delay=12):
        return await callback.answer("⏳ Зачекайте! Генерація ТЗ вже йде...", show_alert=True)
        
    row_id = int(callback.data.split("_")[1])
    await callback.message.answer("⏳ **Генеруємо технічне завдання...**")
    
    try:
        row_data = await async_get_row_data(row_id)
        if not row_data: return await callback.message.answer("Помилка завантаження даних.")
        raw_answers = row_data[5] if len(row_data) > 5 else "{}"
        
        if model:
            prompt = GEMINI_PROMPT.format(raw_answers=raw_answers)
            response = await asyncio.to_thread(model.generate_content, prompt)
            
            report_text = response.text.replace("```html", "").replace("```", "").strip()
            report_text = re.sub(r'<br\s*/?>', '\n', report_text, flags=re.IGNORECASE)
            report_text = re.sub(r'</?ul>', '', report_text, flags=re.IGNORECASE)
            report_text = re.sub(r'<li>', '- ', report_text, flags=re.IGNORECASE)
            report_text = re.sub(r'</li>', '\n', report_text, flags=re.IGNORECASE)
            report_text = report_text.replace("**", "").replace("*", "")
            
            await async_save_report(row_id, report_text)
            kb = InlineKeyboardBuilder()
            kb.button(text="🔙 Назад до заявки", callback_data=f"view_{row_id}")
            await callback.message.answer(f"📋 <b>ПАСПОРТ ОБ'ЄКТА</b>\n\n{report_text}", reply_markup=kb.as_markup(), parse_mode="HTML")
            
            # ЗАПИС У ЛОГИ
            await async_log_action(callback.from_user.full_name, f"✨ Згенерував Технічне Завдання (Рядок {row_id})")
        else:
            await callback.message.answer("⚠️ AI не підключено.")
    except Exception as e:
        await callback.message.answer(f"Помилка: {e}")
    await callback.answer()

@dp.callback_query(F.data.startswith("calc_"))
async def run_calculation(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id): return await callback.answer(MSG_ACCESS_DENIED_ALERT, show_alert=True)
    if is_throttled(callback.from_user.id, "calc", delay=3):
        return await callback.answer("⏳ Рахуємо...", show_alert=True)
        
    row_id = int(callback.data.split("_")[1])
    await callback.answer("Аналізуємо заміри та рахуємо... ⏳")
    
    try:
        row_data = await async_get_row_data(row_id)
        raw_data = row_data[5] if row_data and len(row_data) > 5 else "{}"
        data_json = json.loads(raw_data)
        prices = await async_get_prices()
        b = calculate_budget(data_json, prices)
        c = b["costs"]
        client_info = data_json.get("client", {})
        measurements = data_json.get("answers", {}).get("measurements", {})
        
        total_area = client_info.get("area", 0)
        floor = client_info.get("floor", 1)
        elevator = client_info.get("elevator", "Немає")
        
        details = f"📐 **ОБСЯГИ ТА ЗАМІРИ:**\n▪️ **Площа загальна:** {total_area} м² (Поверх: {floor} | Ліфт: {elevator})\n▪️ **Електроточки:** ~{b['sockets']} шт.\n"
        
        if measurements:
            details += f"▪️ **Приміщення:**\n"
            name_map = {"hallway": "Передпокій", "kitchen": "Кухня", "balcony": "Балкон", "wardrobe": "Гардероб", "basement": "Підвал", "attic": "Горище"}
            for k, v in measurements.items():
                f_sq = v.get("floor", 0)
                w_sq = v.get("walls", 0)
                if "room_" in k: n_name = f"Кімната {k.split('_')[1]}"
                elif "bath_" in k: n_name = f"Санвузол {k.split('_')[1]}"
                else: n_name = name_map.get(k, k)
                
                if "bath" in k: details += f"  - {n_name}: підлога {f_sq} м² *(плитка вкругову ~{float(f_sq)*4.5:.1f} м²)*\n"
                else: details += f"  - {n_name}: підлога {f_sq} м² | стіни {w_sq} м² *(+укоси)*\n"
        
        text = f"💰 **ДЕТАЛЬНИЙ КОШТОРИС ОБ'ЄКТА**\n\n{details}\n💵 **ФІНАНСОВИЙ РОЗПОДІЛ:**\n\n"
        if c["rough"][0] > 0: text += f"🧱 **Чорнові роботи (Стяжка, Каналізація):**\nРобота: {c['rough'][0]:,.0f} грн | Матеріали: ~{c['rough'][1]:,.0f} грн\n\n"
        text += f"⚡️ **Електрика (Точки + розводка):**\nРобота: {c['electric'][0]:,.0f} грн | Матеріали: ~{c['electric'][1]:,.0f} грн\n\n"
        if c["doors"][0] > 0: text += f"🚪 **Двері (Вхідні + Міжкімнатні):**\nРобота: {c['doors'][0]:,.0f} грн | Матеріали: {c['doors'][1]:,.0f} - {c['doors'][2]:,.0f} грн\n\n"
        text += f"🛋 **Оздоблення кімнат (Підлога, Стіни+Укоси, Стеля, Плінтус):**\nРобота: {c['rooms'][0]:,.0f} грн | Матеріали: {c['rooms'][1]:,.0f} - {c['rooms'][2]:,.0f} грн\n\n"
        if c["baths"][0] > 0: text += f"🛁 **Санвузли (Плитка, Сантехніка):**\nРобота: {c['baths'][0]:,.0f} грн | Матеріали: {c['baths'][1]:,.0f} - {c['baths'][2]:,.0f} грн\n\n"
            
        text += f"📊 **ПІДСУМКОВИЙ БЮДЖЕТ:**\n🛠 **Робота:** ~{b['total_work']:,.0f} грн\n📦 **Матеріали:** від {b['total_mat_min']:,.0f} грн до {b['total_mat_max']:,.0f} грн\n💵 **Всього:** від **{(b['total_work'] + b['total_mat_min']):,.0f} грн** до **{(b['total_work'] + b['total_mat_max']):,.0f} грн**"
        
        kb = InlineKeyboardBuilder()
        kb.button(text="🔙 Назад до заявки", callback_data=f"view_{row_id}")
        await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="Markdown")
        
        await async_log_action(callback.from_user.full_name, f"💰 Прорахував кошторис (Рядок {row_id})")
    except Exception as e:
        await callback.message.answer(f"Помилка розрахунку: {e}")

@dp.callback_query(F.data.startswith("del_"))
async def delete_order(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id): return await callback.answer(MSG_ACCESS_DENIED_ALERT, show_alert=True)
    row_id = int(callback.data.split("_")[1])
    try:
        await async_delete_row(row_id)
        await callback.answer("✅ Видалено!", show_alert=True)
        kb = await async_get_orders_keyboard(page=1)
        msg = "📂 **Список заявок:**" if kb else "📭 Порожньо."
        await callback.message.edit_text(msg, reply_markup=kb)
        
        # ЗАПИС У ЛОГИ
        await async_log_action(callback.from_user.full_name, f"🗑 ВИДАЛИВ заявку (Рядок {row_id})")
    except Exception as e:
        await callback.answer(f"Помилка: {e}", show_alert=True)

@dp.message(F.content_type == ContentType.WEB_APP_DATA)
async def web_app_data_handler(message: Message):
    if not is_authorized(message.from_user.id): return await message.answer(MSG_ACCESS_DENIED)
    data = json.loads(message.web_app_data.data)
    if await async_save_to_sheet(data):
        await message.answer("✅ **Нову заявку прийнято!**", parse_mode="Markdown")
        
        # ЗАПИС У ЛОГИ
        client_name = data.get('client', {}).get('name', 'Невідомий клієнт')
        await async_log_action(message.from_user.full_name, f"🆕 СТВОРИВ нову заявку: {client_name}")
    else:
        await message.answer("⚠️ Помилка збереження. Спробуйте ще раз.")

async def on_startup(bot: Bot):
    try:
        await bot.set_webhook(f"{WEBHOOK_URL}{WEBHOOK_PATH}", secret_token=WEBHOOK_SECRET)
        logging.info("✅ Webhook успішно встановлено!")
    except Exception as e:
        logging.error(f"❌ Помилка встановлення Webhook: {e}")

async def on_shutdown(bot: Bot):
    logging.info("💤 Вимикаємо бота... закриваємо з'єднання.")
    await bot.session.close()

def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    app = web.Application()
    
    app.router.add_get('/api/get_order', api_get_order)
    app.router.add_options('/api/get_order', api_get_order)
    
    app.router.add_post('/api/save_order', api_save_order)
    app.router.add_options('/api/save_order', api_save_order)
    
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=WEBHOOK_SECRET).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    web.run_app(app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)

if __name__ == "__main__":
    main()
