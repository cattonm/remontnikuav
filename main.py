import os
import json
import logging
import sys
import math
import html
import time
import csv
import io
from datetime import datetime
import asyncio
import hashlib
import hmac
from urllib.parse import parse_qsl
from functools import wraps

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
from calculator import calculate_budget, apply_virtual_measurements

from art_curator import art_router

BOT_TOKEN = os.getenv('BOT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GOOGLE_CREDS_JSON = os.getenv('GOOGLE_CREDS_JSON') 
SPREADSHEET_NAME = "remonts sheets" 

GROUP_CHAT_ID = "-5265068775" # Замінити на свій ID групи

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

_THROTTLE_CACHE = {}
_LOCKS = {}
_PRICES_CACHE = None
_PRICES_CACHE_TIME = 0
_PRICES_CACHE_TTL = 300 

def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Telegram-Init-Data'
    return response

def cors(handler):
    @wraps(handler)
    async def wrapper(request):
        if request.method == 'OPTIONS':
            return add_cors_headers(web.Response())
        response = await handler(request)
        return add_cors_headers(response)
    return wrapper

def is_throttled(user_id, action, delay=10):
    key = f"{user_id}_{action}"
    now = time.time()
    if key in _THROTTLE_CACHE and now - _THROTTLE_CACHE[key] < delay:
        return True
    _THROTTLE_CACHE[key] = now
    return False

def validate_telegram_data(init_data: str, bot_token: str):
    try:
        parsed_data = dict(parse_qsl(init_data))
        if 'hash' not in parsed_data: return None
        hash_val = parsed_data.pop('hash')
        sorted_data = sorted(parsed_data.items(), key=lambda x: x[0])
        data_check_string = '\n'.join([f"{k}={v}" for k, v in sorted_data])
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if calc_hash == hash_val:
            return json.loads(parsed_data.get('user', '{}')).get('id')
        return None
    except:
        return None

async def notify_admin_about_error(context_msg, error_details):
    try:
        text = f"🚨 <b>СИСТЕМНА ПОМИЛКА БОТА</b>\n\n<b>Процес:</b> {context_msg}\n<b>Деталі:</b> <code>{html.escape(str(error_details))}</code>"
        await bot.send_message(chat_id=MASTER_ADMIN_ID, text=text, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Failed to notify admin: {e}")

def _log_action_sync(user_name, action):
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        doc = gspread.authorize(ServiceAccountCredentials.from_json_keyfile_dict(json.loads(GOOGLE_CREDS_JSON), scope)).open(SPREADSHEET_NAME)
        try:
            ws = doc.worksheet("Logs")
        except:
            ws = doc.add_worksheet(title="Logs", rows="100", cols="3")
            ws.append_row(["Дата і Час", "Менеджер", "Дія"])
        ws.append_row([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user_name, action])
    except: pass

async def async_log_action(user_name, action):
    asyncio.create_task(asyncio.to_thread(_log_action_sync, user_name, action))

def _get_google_sheet():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        return gspread.authorize(ServiceAccountCredentials.from_json_keyfile_dict(json.loads(GOOGLE_CREDS_JSON), scope)).open(SPREADSHEET_NAME).sheet1
    except Exception as e:
        return None

def _save_to_sheet_sync(data):
    sheet = _get_google_sheet()
    if not sheet: return False, "Неможливо підключитися до Google Таблиці (Можливо, злетіли права або ліміти API)."
    try:
        c = data.get('client', {})
        answers = json.dumps(data, ensure_ascii=False)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        address_full = f"{c.get('address')} ({c.get('area', '0')} м² | Пов: {c.get('floor', '1')} | Ліфт: {c.get('elevator', 'Немає')})"
        row_data = [timestamp, c.get('name'), c.get('phone'), c.get('object_type'), address_full, answers, "", "активна"]
        
        col_a = sheet.col_values(1)
        last_real_row = 0
        for i, val in enumerate(col_a):
            if val.strip() != "":
                last_real_row = i + 1
        
        next_row = last_real_row + 1
        if next_row > sheet.row_count:
            sheet.add_rows(10)
            
        cell_list = sheet.range(f'A{next_row}:H{next_row}')
        for i, val in enumerate(row_data):
            cell_list[i].value = str(val)
        sheet.update_cells(cell_list)
        return True, ""
    except Exception as e:
        print(f"Sheet save error: {e}")
        return False, str(e)

def _update_row_sync(row_id, data):
    sheet = _get_google_sheet()
    if not sheet: return False, "Неможливо підключитися до Google Таблиці."
    try:
        c = data.get('client', {})
        answers_json = json.dumps(data, ensure_ascii=False)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M") + " (Оновлено)"
        address_full = f"{c.get('address')} ({c.get('area', '0')} м² | Пов: {c.get('floor', '1')} | Ліфт: {c.get('elevator', 'Немає')})"
        current_row = sheet.row_values(row_id)
        status = current_row[7] if len(current_row) > 7 else "активна"
        row_data = [timestamp, c.get('name'), c.get('phone'), c.get('object_type'), address_full, answers_json, current_row[6] if len(current_row) > 6 else "", status]
        cell_list = sheet.range(f'A{row_id}:H{row_id}')
        for i, val in enumerate(row_data):
            cell_list[i].value = val
        sheet.update_cells(cell_list)
        return True, ""
    except Exception as e:
        return False, str(e)

def _get_row_data_sync(row_id):
    sheet = _get_google_sheet()
    if sheet:
        try: return sheet.row_values(row_id)
        except: return None
    return None

def _delete_row_sync(row_id, user_name):
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        doc = gspread.authorize(ServiceAccountCredentials.from_json_keyfile_dict(json.loads(GOOGLE_CREDS_JSON), scope)).open(SPREADSHEET_NAME)
        sheet = doc.sheet1
        row_data = sheet.row_values(row_id)
        if row_data:
            if len(row_data) < 8: row_data.append("видалена")
            else: row_data[7] = "видалена"
            cell_list = sheet.range(f'A{row_id}:H{row_id}')
            for i, val in enumerate(row_data[:8]): cell_list[i].value = val
            sheet.update_cells(cell_list)
            try: trash_ws = doc.worksheet("Кошик")
            except:
                trash_ws = doc.add_worksheet(title="Кошик", rows="100", cols="9")
                trash_ws.append_row(["Час видалення", "Хто видалив", "Створено", "Ім'я", "Телефон", "Тип", "Адреса", "JSON", "Звіт"])
            trash_ws.append_row([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user_name] + row_data[:7])
        return True, ""
    except Exception as e: 
        return False, str(e)

def _save_report_sync(row_id, text):
    sheet = _get_google_sheet()
    if sheet:
        try: sheet.update_cell(row_id, 7, text)
        except: pass

def _get_orders_keyboard_sync(page=1):
    sheet = _get_google_sheet()
    if not sheet: return None
    try:
        all_rows = sheet.get_all_values()
        if not all_rows or len(all_rows) < 2: return None
        active_rows = []
        for i, row in enumerate(all_rows[1:], start=2):
            if not row[0].strip() or len(row) < 2: continue
            status = row[7] if len(row) > 7 else "активна"
            if status == "активна": active_rows.append((i, row))
        
        total_active = len(active_rows)
        per_page = 10
        total_pages = math.ceil(total_active / per_page) if total_active > 0 else 1
        if page < 1: page = 1
        if page > total_pages: page = total_pages
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        page_rows = active_rows[start_idx:end_idx]

        builder = InlineKeyboardBuilder()
        for actual_row_id, row in page_rows:
            builder.button(text=f"{row[1] if len(row)>1 else '-'} | {row[2] if len(row)>2 else '-'}", callback_data=f"view_{actual_row_id}")
        builder.adjust(1)
        
        nav_buttons = []
        if page > 1: nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"page_{page-1}"))
        nav_buttons.append(InlineKeyboardButton(text=f"Стор. {page}/{total_pages}", callback_data="ignore"))
        if page < total_pages: nav_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"page_{page+1}"))
        if total_active > per_page: builder.row(*nav_buttons)
        builder.row(InlineKeyboardButton(text="🔄 Оновити список", callback_data=f"page_{page}"))
        return builder.as_markup()
    except: return None

async def async_save_to_sheet(data): return await asyncio.to_thread(_save_to_sheet_sync, data)
async def async_update_row(row_id, data): return await asyncio.to_thread(_update_row_sync, row_id, data)
async def async_get_row_data(row_id): return await asyncio.to_thread(_get_row_data_sync, row_id)
async def async_save_report(row_id, text): await asyncio.to_thread(_save_report_sync, row_id, text)
async def async_delete_row(row_id, user_name): return await asyncio.to_thread(_delete_row_sync, row_id, user_name)
async def async_get_orders_keyboard(page=1): return await asyncio.to_thread(_get_orders_keyboard_sync, page)

def _get_prices_sync():
    # ЄДИНЕ ДЖЕРЕЛО ПРАВДИ: Ціни Стандарт (мін) і Преміум (макс)
    DEFAULT_PRICES = {
        "logistics_base": [150, 0, 0], "logistics_stair": [30, 0, 0], "logistics_elev": [10, 0, 0],
        "screed_wet": [1100, 700, 700], "screed_dry": [500, 500, 500], "plumbing": [1100, 300, 300],
        "rough_plaster": [805, 340, 400], "electric_wire": [2100, 1000, 2000], "electric_point": [180, 100, 200],
        "warm_floor_elec": [550, 400, 500], 
        "door_entrance_mdf": [4700, 15000, 50000], "door_entrance_armor": [5500, 15000, 50000], # Двері: 15к-50к
        "door_hidden": [30000, 15000, 27000], "door_std": [3650, 8000, 15000],
        "tile_floor_mosaic": [2600, 1500, 2500], "tile_floor_std": [1900, 1500, 2500], "tile_floor_large": [3100, 1500, 2500],
        "tile_wall_mosaic": [2800, 1500, 2500], "tile_wall_std": [2100, 1500, 2500], "tile_wall_large": [3300, 1500, 2500],
        "toilet_okrem": [2000, 5000, 20000], "toilet_install": [4900, 12000, 30000], # Унітази: 5к-20к / 12к-30к
        "bath_tub": [3800, 15000, 100000], # Ванна: 15к-100к
        "room_lam": [405, 600, 900], "room_quartz": [565, 1200, 1800], "room_parket": [850, 2500, 5000], "linoleum": [150, 300, 600],
        "wall_paper": [1000, 200, 400], "wall_paint": [1865, 250, 450], "wall_decor": [2210, 500, 1500], "whitewash": [100, 50, 100], "wood_rails": [800, 1500, 3500],
        "wall_primer": [55, 0, 0], "wall_vagonka": [1200, 1500, 1500], "wall_koroid": [600, 250, 250],
        "base_std": [215, 115, 200], "base_shadow": [1435, 400, 800], "base_hidden": [1600, 600, 600],
        "ceil_stretch": [400, 390, 390], "ceil_gips": [2500, 650, 650], 
        "ceil_shadow_add": [350, 150, 300], "wall_decor_panels": [5000, 8000, 15000], 
        "kitchen_workspace_led": [1000, 2000, 2000], "balcony_workspace": [1500, 3500, 3500],
        "radiator": [3400, 3000, 12000], "ac": [13000, 15000, 45000], # Радіатор: 3к-12к / Кондиціонер: 15к-45к
        "soundproof": [830, 1000, 2500], "curtains": [500, 3000, 10000],
        "boiler_100": [2800, 8000, 25000], "boiler_300": [5000, 8000, 25000], # Бойлер: 8к-25к
        "towel_dryer": [1200, 3500, 15000], "hygienic_shower": [1900, 3000, 12000], # Рушникосушка: 3.5к-15к / Гіг.душ: 3к-12к
        "mirror_led": [600, 1500, 12000], # Дзеркало: 1.5к-12к (робота змінюється в calculator.py)
        "tech_washer": [1050, 15000, 40000], "tech_kitchen": [1050, 10000, 30000], "tech_osmos": [2000, 8000, 25000], # Техніка
        "sink_cabinet": [1600, 10000, 40000], # Умивальник: 10к-40к
        "mixer_std": [1000, 2000, 15000], "mixer_hidden": [1900, 5000, 25000], # Змішувачі: 2к-15к / 5к-25к
        "sill_plastic": [800, 1500, 1500], "sill_wood": [1500, 3000, 3000], "sill_stone": [2000, 4000, 8000],
        "balcony_warm": [600, 600, 800], "kitchen_apron": [4000, 3000, 8000],
        "balcony_glazing_outer": [1000, 4800, 9000], "balcony_glazing_block": [1500, 4800, 9000],
        "light_point": [250, 300, 800], "light_chandelier": [750, 3500, 3500], "light_track": [780, 1450, 3600], "light_led": [390, 0, 0],
        "shower_tray": [3000, 8000, 20000], "shower_trap": [10000, 3000, 5000], "shower_glass": [3500, 8000, 15000], "shower_doors": [3500, 12000, 20000],
        "demo_door_ent": [1200, 0, 0], "demo_door_int": [500, 0, 0], "demo_walls": [400, 0, 0], 
        "build_gkl": [1100, 600, 600], "build_brick": [1100, 1000, 1000], "build_gazoblok": [850, 600, 600],
        "demo_floor_wood": [250, 0, 0], "demo_floor_lin": [120, 0, 0], "demo_screed": [320, 0, 0]
    }
    return DEFAULT_PRICES

async def async_get_prices(): return await asyncio.to_thread(_get_prices_sync)

async def clean_locks_periodically():
    while True:
        await asyncio.sleep(60)
        now = time.time()
        expired = [rid for rid, lock in _LOCKS.items() if lock["expires"] < now]
        for rid in expired: del _LOCKS[rid]

@cors
async def api_get_order(request):
    init_data = request.headers.get('X-Telegram-Init-Data')
    user_id = validate_telegram_data(init_data, BOT_TOKEN) if init_data else None
    if not user_id or not is_authorized(user_id): return web.json_response({"error": "Access Denied"}, status=403)
    
    # Виправлено: тепер бекенд шукає edit_id
    row_id = request.rel_url.query.get('edit_id') 
    if not row_id: return web.json_response({"error": "No ID"}, status=400)
    
    now = time.time()
    if str(row_id) in _LOCKS:
        lock = _LOCKS[str(row_id)]
        if lock["expires"] > now and lock["user_id"] != user_id:
            return web.json_response({"error": f"🔒 Цю заявку зараз редагує {lock['user_name']}!"}, status=423)
            
    auth_users = get_all_authorized_users()
    _LOCKS[str(row_id)] = { "user_id": user_id, "user_name": auth_users.get(str(user_id), {}).get("name", "Колега"), "expires": now + 600 }
    
    row_data = await async_get_row_data(int(row_id))
    if not row_data: return web.json_response({"error": "Not found"}, status=404)
    
    try: return web.json_response(json.loads(row_data[5]))
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

@cors
async def api_save_order(request):
    init_data = request.headers.get('X-Telegram-Init-Data')
    if not init_data: return web.json_response({"error": "Unauthorized"}, status=401)
    user_id = validate_telegram_data(init_data, BOT_TOKEN)
    if not user_id or not is_authorized(user_id): return web.json_response({"error": "Access Denied"}, status=403)
    try:
        data = await request.json()
        edit_id = data.get("edit_id")
        manager_name = get_all_authorized_users().get(str(user_id), {}).get("name", f"ID: {user_id}")
        if edit_id:
            existing = await async_get_row_data(int(edit_id))
            if not existing: return web.json_response({"error": "Row not found"}, status=404)
            success, error_msg = await async_update_row(int(edit_id), data)
            if success:
                if str(edit_id) in _LOCKS: del _LOCKS[str(edit_id)]
                await async_log_action(manager_name, f"✏️ Відредагував об'єкт (Рядок {edit_id})")
                try: await bot.send_message(chat_id=user_id, text=f"✅ **Заявку оновлено!** (Рядок {edit_id})", parse_mode="Markdown")
                except: pass
                return web.json_response({"success": True})
            else: 
                await notify_admin_about_error(f"Оновлення заявки (ID: {edit_id})", error_msg)
                return web.json_response({"error": "Update failed"}, status=500)
        return web.json_response({"error": "No edit_id"}, status=400)
    except Exception as e: 
        await notify_admin_about_error("API Збереження (Загальна помилка)", e)
        return web.json_response({"error": str(e)}, status=500)
@cors
async def api_ping(request):
    return web.Response(text="Pong! Bot is alive 24/7")
@cors
async def api_live_calc(request):
    try:
        data = await request.json()
        data_with_virtual_meas = apply_virtual_measurements(data)
        prices = await async_get_prices()
        b = calculate_budget(data_with_virtual_meas, prices)
        return web.json_response({"work": b["total_work"], "mat_min": b["total_mat_min"]})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

def get_main_menu_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📝 Заповнити анкету", web_app=WebAppInfo(url=WEBAPP_URL))], [KeyboardButton(text="🔐 Кабінет менеджера")]], resize_keyboard=True)

@dp.message(Command("start"))
async def cmd_start(message: Message):
    if not is_authorized(message.from_user.id): return await message.answer(MSG_START_AUTH.format(name=message.from_user.first_name), parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True))
    await message.answer(MSG_START_MAIN.format(name=message.from_user.first_name), reply_markup=get_main_menu_keyboard(), parse_mode="Markdown")

# --- ОНОВЛЕНА КОМАНДА /UPD З ПІДТРИМКОЮ ФОТО ТА ВІДЕО ---
@dp.message(Command("upd"))
async def send_update_to_group(message: Message):
    if not is_authorized(message.from_user.id): return
    
    # Витягуємо текст незалежно від того, чи це просто текст, чи опис до фото
    full_text = message.text or message.caption or ""
    args = full_text.split(maxsplit=1)
    content_to_send = args[1] if len(args) > 1 else ""
    
    if GROUP_CHAT_ID == "-100XXXXXXXXXX" or not GROUP_CHAT_ID: 
        return await message.answer("⚠️ Спочатку вкажіть реальний ID вашої групи у файлі main.py (змінна GROUP_CHAT_ID).")
        
    try:
        if message.photo:
            await bot.send_photo(chat_id=GROUP_CHAT_ID, photo=message.photo[-1].file_id, caption=content_to_send)
        elif message.video:
            await bot.send_video(chat_id=GROUP_CHAT_ID, video=message.video.file_id, caption=content_to_send)
        elif message.document:
            await bot.send_document(chat_id=GROUP_CHAT_ID, document=message.document.file_id, caption=content_to_send)
        else:
            if not content_to_send:
                return await message.answer("⚠️ Напишіть текст після команди. Формат:\n`/upd Ваш текст тут`\n*(Або прикріпіть фото і напишіть команду в описі)*", parse_mode="Markdown")
            await bot.send_message(chat_id=GROUP_CHAT_ID, text=content_to_send)
        
        await message.answer("✅ Повідомлення успішно відправлено в групу!")
    except Exception as e: 
        await message.answer(f"❌ Помилка відправки: {e}")

@dp.message(F.text == "Super#secusers")
async def secret_admin_panel(message: Message):
    try: await message.delete() 
    except: pass
    if message.from_user.id != MASTER_ADMIN_ID: return
    auth_data = get_all_authorized_users()
    kb = InlineKeyboardBuilder()
    for uid, info in auth_data.items():
        kb.button(text=f"❌ {info.get('name', '')} (@{info.get('username', '')})", callback_data=f"revoke_{uid}")
    kb.adjust(1)
    await message.answer("🕵️‍♂️ **Секретна панель:**", reply_markup=kb.as_markup(), parse_mode="Markdown")

@dp.message(F.text == "Super#reload_cache")
async def cmd_reload_cache(message: Message):
    if message.from_user.id != MASTER_ADMIN_ID: return
    clear_auth_cache()
    global _PRICES_CACHE, _PRICES_CACHE_TIME
    _PRICES_CACHE = None
    _PRICES_CACHE_TIME = 0
    await message.answer("🔄 **Кеш успішно очищено!**")

@dp.message(F.text == "Super#backup")
async def cmd_backup(message: Message):
    if message.from_user.id != MASTER_ADMIN_ID: return
    def _get_csv():
        sheet = _get_google_sheet()
        if not sheet: return None
        output = io.StringIO()
        csv.writer(output).writerows(sheet.get_all_values())
        return ("\xef\xbb\xbf" + output.getvalue()).encode('utf-8')
    csv_data = await asyncio.to_thread(_get_csv)
    if csv_data: await message.answer_document(BufferedInputFile(csv_data, filename=f"remont_{datetime.now().strftime('%Y_%m_%d_%H%M')}.csv"))

@dp.callback_query(F.data.startswith("revoke_"))
async def revoke_access(callback: CallbackQuery):
    if callback.from_user.id != MASTER_ADMIN_ID: return
    if remove_authorized_user(callback.data.split("_")[1]): await callback.answer("✅ Доступ скасовано!", show_alert=True)

@dp.message(F.text == "🔐 Кабінет менеджера")
@dp.message(Command("admin"))
async def open_admin_panel(message: Message):
    if not is_authorized(message.from_user.id): return
    kb = await async_get_orders_keyboard(page=1)
    await message.answer("📂 **Список активних заявок:**", reply_markup=kb)

@dp.message(F.text)
async def process_password_attempts(message: Message):
    if is_authorized(message.from_user.id): return
    if message.text == ADMIN_PASSWORD:
        add_authorized_user(message.from_user.id, message.from_user.full_name, message.from_user.username or "немає")
        await message.answer(MSG_AUTH_SUCCESS, reply_markup=get_main_menu_keyboard(), parse_mode="Markdown")
        if message.from_user.id != MASTER_ADMIN_ID:
            try: await bot.send_message(MASTER_ADMIN_ID, f"🟢 <b>УСПІШНА АВТОРИЗАЦІЯ</b>\n{html.escape(message.from_user.full_name)}", parse_mode="HTML")
            except: pass
    else: await message.answer(MSG_AUTH_FAIL)

@dp.callback_query(F.data.startswith("page_"))
async def change_page(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id): return
    await callback.message.edit_text("📂 **Список заявок:**", reply_markup=await async_get_orders_keyboard(int(callback.data.split("_")[1])))

@dp.callback_query(F.data == "show_list")
async def show_first_page(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id): return
    await callback.message.edit_text("📂 **Список заявок:**", reply_markup=await async_get_orders_keyboard(1))

@dp.callback_query(F.data == "ignore")
async def ignore_callback(callback: CallbackQuery): await callback.answer()

@dp.callback_query(F.data.startswith("view_"))
async def view_order(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id): return
    row_id = int(callback.data.split("_")[1])
    row_data = await async_get_row_data(row_id)
    if not row_data: return
    existing_report = row_data[6] if len(row_data) > 6 else ""
    text = f"👤 **Клієнт:** {row_data[1]}\n📞 **Телефон:** `{row_data[2]}`\n🏠 **Об'єкт:** {row_data[3]}\n📍 **Адреса:** {row_data[4]}"
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
    row_id = int(callback.data.split("_")[1])
    row_data = await async_get_row_data(row_id)
    report = row_data[6] if row_data and len(row_data) > 6 else ""
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Перегенерувати", callback_data=f"gen_{row_id}")
    kb.button(text="🔙 Назад", callback_data=f"view_{row_id}")
    kb.adjust(1)
    await callback.message.edit_text(f"📋 <b>ЗВІТ:</b>\n\n{report}", reply_markup=kb.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("gen_"))
async def generate_report_action(callback: CallbackQuery):
    if is_throttled(callback.from_user.id, "generate_tz", delay=12): return await callback.answer("⏳ Зачекайте!", show_alert=True)
    row_id = int(callback.data.split("_")[1])
    await callback.message.answer("⏳ **Генеруємо ТЗ...**")
    try:
        raw_answers = (await async_get_row_data(row_id))[5]
        for attempt in range(3):
            try:
                response = await model.generate_content_async(GEMINI_PROMPT.format(raw_answers=raw_answers))
                report_text = response.text.replace("```html", "").replace("```", "").strip()
                break
            except:
                if attempt == 2: raise
                await asyncio.sleep(1)
        await async_save_report(row_id, report_text)
        kb = InlineKeyboardBuilder()
        kb.button(text="🔙 Назад", callback_data=f"view_{row_id}")
        await callback.message.answer(f"📋 <b>ПАСПОРТ</b>\n\n{report_text}", reply_markup=kb.as_markup(), parse_mode="HTML")
        await async_log_action(callback.from_user.full_name, f"✨ Згенерував ТЗ (Рядок {row_id})")
    except Exception as e: await callback.message.answer(f"❌ Помилка: {str(e)}")
    await callback.answer()

@dp.callback_query(F.data.startswith("calc_"))
async def run_calculation(callback: CallbackQuery):
    if is_throttled(callback.from_user.id, "calc", delay=3): return await callback.answer("⏳ Рахуємо...", show_alert=True)
    row_id = int(callback.data.split("_")[1])
    await callback.answer("Аналізуємо... ⏳")
    try:
        data_json = json.loads((await async_get_row_data(row_id))[5])
        prices = await async_get_prices()
        b = calculate_budget(data_json, prices)
        c = b["costs"]
        text = f"💰 **ДЕТАЛЬНИЙ КОШТОРИС**\n\n"
        if c["rough"][0] > 0: text += f"🧱 **Чорнові та Демонтаж:**\nРобота: {c['rough'][0]:,.0f} ₴ | Матеріали: ~{c['rough'][1]:,.0f} ₴\n\n"
        text += f"⚡️ **Електрика:**\nРобота: {c['electric'][0]:,.0f} ₴ | Матеріали: ~{c['electric'][1]:,.0f} ₴\n\n"
        if c["doors"][0] > 0: text += f"🚪 **Двері:**\nРобота: {c['doors'][0]:,.0f} ₴ | Матеріали: {c['doors'][1]:,.0f} - {c['doors'][2]:,.0f} ₴\n\n"
        text += f"🛋 **Оздоблення кімнат:**\nРобота: {c['rooms'][0]:,.0f} ₴ | Матеріали: {c['rooms'][1]:,.0f} - {c['rooms'][2]:,.0f} ₴\n\n"
        if c["baths"][0] > 0: text += f"🛁 **Санвузли:**\nРобота: {c['baths'][0]:,.0f} ₴ | Матеріали: {c['baths'][1]:,.0f} - {c['baths'][2]:,.0f} ₴\n\n"
        
        if c.get("custom", [0])[0] > 0 or c.get("custom", [0,0,0])[1] > 0: 
            text += f"⭐️ **НЕСТАНДАРТНІ РОБОТИ:**\nРобота: {c['custom'][0]:,.0f} ₴ | Матеріали: ~{c['custom'][1]:,.0f} ₴\n\n"
            
        text += f"📊 **ПІДСУМКОВИЙ БЮДЖЕТ:**\n🛠 **Робота:** ~{b['total_work']:,.0f} ₴\n📦 **Матеріали:** від {b['total_mat_min']:,.0f} ₴ до {b['total_mat_max']:,.0f} ₴"
        kb = InlineKeyboardBuilder()
        kb.button(text="🔙 Назад", callback_data=f"view_{row_id}")
        await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="Markdown")
        await async_log_action(callback.from_user.full_name, f"💰 Прорахував кошторис (Рядок {row_id})")
    except Exception as e: await callback.message.answer(f"❌ Помилка: {str(e)}")

@dp.callback_query(F.data.startswith("del_"))
async def delete_order(callback: CallbackQuery):
    row_id = int(callback.data.split("_")[1])
    success, error_msg = await async_delete_row(row_id, callback.from_user.full_name)
    if success:
        await callback.answer("✅ В Кошику!", show_alert=True)
        await callback.message.edit_text("📂 **Список заявок:**", reply_markup=await async_get_orders_keyboard(1))
        await async_log_action(callback.from_user.full_name, f"🗑 ВИДАЛИВ заявку (Рядок {row_id})")
    else:
        await notify_admin_about_error(f"Видалення заявки (ID: {row_id})", error_msg)
        await callback.answer("⚠️ Помилка видалення!", show_alert=True)

@dp.message(F.content_type == ContentType.WEB_APP_DATA)
async def web_app_data_handler(message: Message):
    if not is_authorized(message.from_user.id): return await message.answer(MSG_ACCESS_DENIED)
    data = json.loads(message.web_app_data.data)
    success, error_msg = await async_save_to_sheet(data)
    if success:
        await message.answer("✅ **Нову заявку прийнято!**", parse_mode="Markdown")
        await async_log_action(message.from_user.full_name, f"🆕 СТВОРИВ нову заявку: {data.get('client', {}).get('name', '')}")
    else: 
        await notify_admin_about_error(f"Збереження заявки від {message.from_user.full_name}", error_msg)
        await message.answer("⚠️ Помилка збереження. Адміністратора повідомлено.")

async def on_startup(bot: Bot):
    _get_google_sheet()
    try: await bot.set_webhook(f"{WEBHOOK_URL}{WEBHOOK_PATH}", secret_token=WEBHOOK_SECRET)
    except: pass
    asyncio.create_task(clean_locks_periodically())

async def on_shutdown(bot: Bot): await bot.session.close()

def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    dp.include_router(art_router)
    app = web.Application()
    
    # Твої існуючі маршрути (по одному разу!)
    app.router.add_get('/api/get_order', api_get_order)
    app.router.add_options('/api/get_order', api_get_order)
    app.router.add_post('/api/save_order', api_save_order)
    app.router.add_options('/api/save_order', api_save_order)
    app.router.add_post('/api/live_calc', api_live_calc)
    app.router.add_options('/api/live_calc', api_live_calc)
    
    # Наш новий маршрут для бота 24/7
    app.router.add_get('/ping', api_ping)
    
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=WEBHOOK_SECRET).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    web.run_app(app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)

if __name__ == "__main__": 
    main()
