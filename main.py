import os
import json
import logging
import sys
import math
import html
import time
import csv
import base64
import secrets
import io
from datetime import datetime
import asyncio
import hashlib
import hmac
from urllib.parse import parse_qsl
from functools import wraps

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, ContentType, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials

from security import (ADMIN_PASSWORD, MASTER_ADMIN_ID, is_authorized, get_all_authorized_users,
                      add_authorized_user, remove_authorized_user, clear_auth_cache,
                      ROLE_ADMIN, ROLE_MANAGER, get_role, is_admin, create_invite, redeem_invite)
from lexicon import GEMINI_PROMPT, MSG_START_AUTH, MSG_START_MAIN, MSG_AUTH_SUCCESS, MSG_AUTH_FAIL, MSG_ACCESS_DENIED, MSG_ACCESS_DENIED_ALERT
from calculator import calculate_budget, apply_virtual_measurements

# art_curator — ОПЦІЙНИЙ модуль: файла немає в репозиторії (лише локально).
# Раніше жорсткий import валив старт на Render: ModuleNotFoundError →
# деплой failed → Render мовчки лишав живою СТАРУ версію бекенду
# (саме тому rooms не тарифікувались, хоча код у репо був правильний).
# Якщо модуль потрібен у проді — закоміть art_curator.py, роутер підхопиться сам.
try:
    from art_curator import art_router
except ImportError:
    art_router = None
    logging.warning("art_curator.py не знайдено — стартуємо без цього роутера.")

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
_PRICE_LABELS = {}          # {price_key: "Назва з таблиці"} — для деталізації
_PRICES_META = {"source": "default", "loaded_at": None, "count": 0}  # для /version
_STARTED_AT = time.time()

def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Telegram-Init-Data, X-Session-Token'
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

def validate_login_widget(data: dict, bot_token: str):
    """Перевірка даних з Telegram Login Widget (вхід на ЗВИЧАЙНОМУ сайті).

    Відрізняється від initData: секрет — це SHA256(bot_token) без солі
    "WebAppData", а рядок перевірки збирається з плоского словника.
    Підпис гарантує, що дані прийшли саме від Telegram і не підроблені —
    тому паролі не потрібні взагалі.
    """
    try:
        data = dict(data)
        hash_val = data.pop("hash", None)
        if not hash_val:
            return None
        # Захист від replay: дані старші за добу не приймаємо
        if abs(time.time() - int(data.get("auth_date", 0))) > 86400:
            return None
        check = "\n".join(f"{k}={data[k]}" for k in sorted(data) if data[k] is not None)
        secret = hashlib.sha256(bot_token.encode()).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        return int(data["id"]) if hmac.compare_digest(calc, hash_val) else None
    except Exception:
        return None


# ==========================================================
# СЕСІЇ ДЛЯ САЙТУ
# ----------------------------------------------------------
# Після входу через Telegram видаємо підписаний токен: base64(payload).signature.
# Підпис — HMAC на BOT_TOKEN, тож підробити або продовжити термін неможливо.
# Токен зберігається у localStorage і їде в заголовку X-Session-Token.
# Нічого не тримаємо в пам'яті сервера — Render перезапускається, а сесії живуть.
# ==========================================================
SESSION_TTL = 30 * 24 * 3600      # 30 днів

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")

def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

def create_session(user_id, role):
    payload = json.dumps({"uid": str(user_id), "role": role,
                          "exp": int(time.time()) + SESSION_TTL}, separators=(",", ":"))
    body = _b64e(payload.encode())
    sig = hmac.new(BOT_TOKEN.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"

def read_session(token):
    """Повертає {'uid', 'role'} або None. Роль ПЕРЕПЕРЕВІРЯЄМО в таблиці —
    якщо доступ відкликано, старий токен перестає діяти негайно."""
    try:
        body, sig = str(token).split(".", 1)
        expect = hmac.new(BOT_TOKEN.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expect):
            return None
        data = json.loads(_b64d(body))
        if data.get("exp", 0) < time.time():
            return None
        role_now = get_role(data["uid"])       # актуальна роль, а не та, що в токені
        if not role_now:
            return None
        return {"uid": data["uid"], "role": role_now}
    except Exception:
        return None

def auth_request(request):
    """ЄДИНА точка автентифікації. Розуміє обидва входи:
       • X-Telegram-Init-Data — міні-апка всередині Telegram;
       • X-Session-Token      — сайт у звичайному браузері.
    Повертає (user_id, role) або (None, None)."""
    token = request.headers.get('X-Session-Token')
    if token:
        s = read_session(token)
        if s:
            return s["uid"], s["role"]
    init_data = request.headers.get('X-Telegram-Init-Data')
    if init_data:
        uid = validate_telegram_data(init_data, BOT_TOKEN)
        if uid:
            role = get_role(uid)
            if role:
                return str(uid), role
    return None, None


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

# --- РОЗШИРЕНА СХЕМА ЗАЯВКИ ---
# A-H були раніше. Додаємо:
#   I: manager_id — хто створив (порожньо = гість із сайту)
#   J: source     — "manager" | "web"
#   K: deal       — статус угоди: new | sent | won | lost
# Старі рядки без I-K читаються як (невідомий менеджер, manager, new) —
# зворотна сумісність повна, мігрувати таблицю руками не треба.
DEAL_STATUSES = {
    "new":  "🆕 Нова",
    "sent": "📤 КП відправлено",
    "won":  "✅ Виграна",
    "lost": "❌ Програна",
}
SOURCE_LABELS = {"manager": "👔 Менеджер", "web": "🌐 Сайт (самостійно)"}

def _save_to_sheet_sync(data):
    sheet = _get_google_sheet()
    if not sheet: return False, "Неможливо підключитися до Google Таблиці (Можливо, злетіли права або ліміти API)."
    try:
        c = data.get('client', {})
        answers = json.dumps(data, ensure_ascii=False)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        address_full = f"{c.get('address')} ({c.get('area', '0')} м² | Пов: {c.get('floor', '1')} | Ліфт: {c.get('elevator', 'Немає')})"
        manager_id = str(data.get("manager_id") or "")
        source = data.get("source") or ("manager" if manager_id else "web")
        row_data = [timestamp, c.get('name'), c.get('phone'), c.get('object_type'), address_full,
                    answers, "", "активна", manager_id, source, "new"]
        
        col_a = sheet.col_values(1)
        last_real_row = 0
        for i, val in enumerate(col_a):
            if val.strip() != "":
                last_real_row = i + 1
        
        next_row = last_real_row + 1
        if next_row > sheet.row_count:
            sheet.add_rows(10)
            
        cell_list = sheet.range(f'A{next_row}:K{next_row}')
        for i, val in enumerate(row_data):
            cell_list[i].value = str(val)
        sheet.update_cells(cell_list)
        invalidate_orders_cache()   # нова заявка має з'явитись у кабінеті одразу
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
        invalidate_orders_cache()   # оновлені ім'я/телефон/адреса — одразу в кабінеті
        return True, ""
    except Exception as e:
        return False, str(e)

def _get_row_data_sync(row_id):
    sheet = _get_google_sheet()
    if sheet:
        try: return sheet.row_values(row_id)
        except: return None
    return None

def _row_meta(row):
    """Розбирає рядок заявки у зручний словник (із зворотною сумісністю
    для старих рядків, де колонок I-K ще не було)."""
    def at(i, default=""):
        return row[i].strip() if len(row) > i and row[i] else default
    deal = at(10, "new").lower()
    if deal not in DEAL_STATUSES:
        deal = "new"
    return {
        "date": at(0), "name": at(1), "phone": at(2), "type": at(3), "address": at(4),
        "status": at(7, "активна"),
        "manager_id": at(8),
        "source": at(9, "manager").lower(),
        "deal": deal,
    }

# ==========================================================
# КЕШ СПИСКУ ЗАЯВОК
# ----------------------------------------------------------
# Було: кожен запит кабінету робив sheet.get_all_values() — тобто тягнув
# УСІ колонки, включно з F, де лежить повний JSON анкети (десятки КБ на
# заявку). Сто заявок = мегабайти з Google на кожне натискання фільтра,
# а квота Google Sheets — 60 читань за хвилину. Тепер:
#   • читаємо ЛИШЕ потрібні колонки (A-E, H-K), важкий JSON не чіпаємо;
#   • тримаємо результат у пам'яті 30 секунд;
#   • скидаємо кеш одразу після будь-якої зміни (нова заявка, статус).
# ==========================================================
_ORDERS_CACHE = {"rows": None, "ts": 0}
_ORDERS_TTL = 30

def invalidate_orders_cache():
    _ORDERS_CACHE["rows"] = None
    _ORDERS_CACHE["ts"] = 0

def _fetch_orders_rows_sync():
    """Легке читання аркуша: тільки метадані заявок, без JSON анкети."""
    now = time.time()
    if _ORDERS_CACHE["rows"] is not None and (now - _ORDERS_CACHE["ts"]) < _ORDERS_TTL:
        return _ORDERS_CACHE["rows"]

    sheet = _get_google_sheet()
    if not sheet:
        return []
    # A-E: дата, ім'я, телефон, тип, адреса | H-K: статус, менеджер, джерело, угода.
    # Колонку F (повний JSON) свідомо НЕ читаємо — вона потрібна лише при
    # відкритті конкретної заявки.
    left, right = sheet.batch_get(["A2:E", "H2:K"])
    rows = []
    for i in range(max(len(left), len(right))):
        a = left[i] if i < len(left) else []
        b = right[i] if i < len(right) else []
        if not a or not (a[0] or "").strip():
            continue
        a = list(a) + [""] * (5 - len(a))
        b = list(b) + [""] * (4 - len(b))
        rows.append({"row": i + 2, "a": a, "b": b})

    _ORDERS_CACHE["rows"] = rows
    _ORDERS_CACHE["ts"] = now
    return rows

def _meta_from_parts(entry):
    a, b = entry["a"], entry["b"]
    deal = (b[3] or "new").strip().lower()
    if deal not in DEAL_STATUSES:
        deal = "new"
    return {
        "row": entry["row"],
        "date": a[0].strip(), "name": a[1].strip(), "phone": a[2].strip(),
        "type": a[3].strip(), "address": a[4].strip(),
        "status": (b[0] or "активна").strip(),
        "manager_id": (b[1] or "").strip(),
        "source": ((b[2] or "manager").strip().lower()),
        "deal": deal,
    }

def _list_orders_sync(user_id, role, deal_filter=None, query=None):
    """Заявки, які має бачити цей користувач.
      • admin   — усі;
      • manager — свої + вільні гостьові ліди (нічиї);
    Плюс фільтр за статусом угоди і пошук за іменем/телефоном/адресою."""
    out = []
    q = (query or "").strip().lower()
    for entry in _fetch_orders_rows_sync():
        m = _meta_from_parts(entry)
        if m["status"] == "видалена":
            continue
        if role != ROLE_ADMIN:
            mine = m["manager_id"] == str(user_id)
            free_lead = (m["source"] == "web" and not m["manager_id"])
            if not (mine or free_lead):
                continue
        if deal_filter and m["deal"] != deal_filter:
            continue
        if q and q not in f"{m['name']} {m['phone']} {m['address']}".lower():
            continue
        out.append(m)
    out.reverse()      # найновіші зверху
    return out

def _set_deal_status_sync(row_id, deal, claim_by=None):
    """Змінює статус угоди. claim_by — якщо менеджер бере вільний лід собі."""
    sheet = _get_google_sheet()
    if not sheet:
        return False
    sheet.update_cell(int(row_id), 11, deal)        # K = deal
    if claim_by:
        sheet.update_cell(int(row_id), 9, str(claim_by))   # I = manager_id
    invalidate_orders_cache()   # інакше кабінет ще 30 с показував би старий статус
    return True

async def async_list_orders(user_id, role, deal_filter=None, query=None):
    return await asyncio.to_thread(_list_orders_sync, user_id, role, deal_filter, query)

async def async_set_deal_status(row_id, deal, claim_by=None):
    return await asyncio.to_thread(_set_deal_status_sync, row_id, deal, claim_by)

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

# ЄДИНЕ ДЖЕРЕЛО ПРАВДИ (fallback): ціни Стандарт (мін) і Преміум (макс).
# Ці значення використовуються, якщо аркуш "Ціни" в Google-таблиці ще не
# створено або він недоступний. При першому успішному підключенні аркуш
# створюється автоматично і заповнюється цими ж значеннями.
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
PRICES_SHEET_NAME = "Ціни"
_PRICES_HEADER = ["key", "Назва", "Робота (грн)", "Матеріал мін (грн)", "Матеріал макс (грн)"]


def _prices_bootstrap_sheet(doc):
    """Створює аркуш «Ціни» і заливає в нього поточні DEFAULT_PRICES.
    Викликається один раз — далі власник редагує ціни прямо в таблиці."""
    from calculator import PRICE_META
    ws = doc.add_worksheet(title=PRICES_SHEET_NAME, rows=str(len(DEFAULT_PRICES) + 20), cols="5")
    rows = [_PRICES_HEADER]
    for key, (w, m1, m2) in sorted(DEFAULT_PRICES.items()):
        label = PRICE_META.get(key, (key, ""))[0]
        rows.append([key, label, w, m1, m2])
    ws.update(rows, "A1")
    ws.format("A1:E1", {"textFormat": {"bold": True}})
    ws.freeze(rows=1)
    logging.info("Створено аркуш «Ціни» і заповнено дефолтними значеннями.")
    return ws


def _get_prices_sync():
    """Ціни з Google-таблиці (аркуш «Ціни») з кешем на 5 хв.

    Логіка навмисно «незламна»:
      • аркуша немає  → створюємо і заповнюємо дефолтами;
      • таблиця лягла → віддаємо останній кеш, а якщо його нема — DEFAULT_PRICES;
      • у таблиці кривий рядок → пропускаємо саме його, решта цін працює;
      • ключ є в коді, але його немає в таблиці → береться дефолт (merge).
    Тобто редагування таблиці НІКОЛИ не може повалити калькулятор у нуль.
    """
    global _PRICES_CACHE, _PRICES_CACHE_TIME, _PRICE_LABELS
    now = time.time()
    if _PRICES_CACHE and (now - _PRICES_CACHE_TIME) < _PRICES_CACHE_TTL:
        return _PRICES_CACHE

    prices = dict(DEFAULT_PRICES)   # база: дефолти, поверх — значення з таблиці
    labels = {}
    source = "default"
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        doc = gspread.authorize(ServiceAccountCredentials.from_json_keyfile_dict(json.loads(GOOGLE_CREDS_JSON), scope)).open(SPREADSHEET_NAME)
        try:
            ws = doc.worksheet(PRICES_SHEET_NAME)
        except gspread.exceptions.WorksheetNotFound:
            ws = _prices_bootstrap_sheet(doc)

        bad = 0
        for row in ws.get_all_records():      # перший рядок = заголовки
            key = str(row.get("key", "")).strip()
            if not key:
                continue
            try:
                w = float(str(row.get("Робота (грн)", 0)).replace(",", ".").replace(" ", "") or 0)
                m1 = float(str(row.get("Матеріал мін (грн)", 0)).replace(",", ".").replace(" ", "") or 0)
                m2 = float(str(row.get("Матеріал макс (грн)", 0)).replace(",", ".").replace(" ", "") or 0)
            except (TypeError, ValueError):
                bad += 1
                continue
            if m2 < m1:                        # захист від описки «макс < мін»
                m1, m2 = m2, m1
            prices[key] = [w, m1, m2]
            label = str(row.get("Назва", "")).strip()
            if label:
                labels[key] = label
        source = "sheet"
        if bad:
            logging.warning("Аркуш «Ціни»: пропущено %d некоректних рядків.", bad)
    except Exception as e:
        logging.error("Не вдалося прочитати ціни з таблиці (%s). Використовую %s.",
                      e, "кеш" if _PRICES_CACHE else "DEFAULT_PRICES")
        if _PRICES_CACHE:
            return _PRICES_CACHE

    _PRICES_CACHE = prices
    _PRICES_CACHE_TIME = now
    _PRICE_LABELS = labels
    _PRICES_META["source"] = source
    _PRICES_META["loaded_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _PRICES_META["count"] = len(prices)
    return prices


def get_price_labels():
    """Назви позицій з колонки «Назва» — для построчної деталізації."""
    return _PRICE_LABELS


async def async_get_prices(): return await asyncio.to_thread(_get_prices_sync)

# ==========================================================
# СЕРВЕРНІ ЧЕРНЕТКИ + НАГАДУВАННЯ ЧЕРЕЗ 24 ГОД
# Аркуш "Drafts": user_id | updated_at (ISO) | payload (JSON) | reminded (0/1)
# Чернетка живе не лише в localStorage телефона: менеджер може продовжити
# з іншого пристрою, а недороблена заявка не губиться — бот нагадає.
# ==========================================================
DRAFTS_SHEET_NAME = "Drafts"
DRAFT_REMIND_AFTER_H = 24        # через скільки годин нагадувати
DRAFT_TTL_DAYS = 30              # старші чернетки прибираємо

def _drafts_ws():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    doc = gspread.authorize(ServiceAccountCredentials.from_json_keyfile_dict(json.loads(GOOGLE_CREDS_JSON), scope)).open(SPREADSHEET_NAME)
    try:
        return doc.worksheet(DRAFTS_SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        ws = doc.add_worksheet(title=DRAFTS_SHEET_NAME, rows="200", cols="4")
        ws.append_row(["user_id", "updated_at", "payload", "reminded"])
        return ws

def _save_draft_sync(user_id, payload):
    ws = _drafts_ws()
    blob = json.dumps(payload, ensure_ascii=False)
    if len(blob) > 45000:        # ліміт клітинки Google Sheets — 50 000 символів
        logging.warning("Чернетка %s завелика (%d символів) — не зберігаю.", user_id, len(blob))
        return False
    row = [str(user_id), datetime.now().isoformat(timespec="seconds"), blob, "0"]
    try:
        cell = ws.find(str(user_id), in_column=1)
    except Exception:
        cell = None
    if cell:
        ws.update([row], f"A{cell.row}:D{cell.row}")   # оновлюємо існуючу
    else:
        ws.append_row(row)                             # або створюємо нову
    return True

def _get_draft_sync(user_id):
    ws = _drafts_ws()
    try:
        cell = ws.find(str(user_id), in_column=1)
    except Exception:
        cell = None
    if not cell:
        return None
    vals = ws.row_values(cell.row)
    if len(vals) < 3 or not vals[2]:
        return None
    try:
        return {"updated_at": vals[1], "payload": json.loads(vals[2])}
    except json.JSONDecodeError:
        return None

def _delete_draft_sync(user_id):
    ws = _drafts_ws()
    try:
        cell = ws.find(str(user_id), in_column=1)
    except Exception:
        cell = None
    if cell:
        ws.delete_rows(cell.row)
    return True

@cors
async def api_save_draft(request):
    user_id, _role = auth_request(request)   # працює і в Telegram, і на сайті
    if not user_id: return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        data = await request.json()
        ok = await asyncio.to_thread(_save_draft_sync, user_id, data)
        return web.json_response({"success": ok})
    except Exception:
        logging.exception("save_draft failed")
        return web.json_response({"error": "save_failed"}, status=500)

@cors
async def api_get_draft(request):
    user_id, _role = auth_request(request)   # працює і в Telegram, і на сайті
    if not user_id: return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        draft = await asyncio.to_thread(_get_draft_sync, user_id)
        return web.json_response({"draft": draft})
    except Exception:
        logging.exception("get_draft failed")
        return web.json_response({"draft": None})

@cors
async def api_delete_draft(request):
    user_id, _role = auth_request(request)   # працює і в Telegram, і на сайті
    if not user_id: return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        await asyncio.to_thread(_delete_draft_sync, user_id)
        return web.json_response({"success": True})
    except Exception:
        logging.exception("delete_draft failed")
        return web.json_response({"error": "delete_failed"}, status=500)

def _scan_drafts_for_reminders_sync():
    """Повертає [(row, user_id, payload)] для чернеток, старших за 24 год і
    ще не нагаданих; попутно видаляє протухлі (>30 днів)."""
    ws = _drafts_ws()
    rows = ws.get_all_values()[1:]     # без заголовка
    now = datetime.now()
    due, stale_rows = [], []
    for idx, r in enumerate(rows, start=2):    # 1 = заголовок
        if len(r) < 4:
            continue
        uid, updated, blob, reminded = r[0], r[1], r[2], r[3]
        try:
            ts = datetime.fromisoformat(updated)
        except (TypeError, ValueError):
            continue
        age_h = (now - ts).total_seconds() / 3600
        if age_h > DRAFT_TTL_DAYS * 24:
            stale_rows.append(idx)
            continue
        if reminded == "0" and age_h >= DRAFT_REMIND_AFTER_H:
            try:
                due.append((idx, uid, json.loads(blob)))
            except json.JSONDecodeError:
                continue
    for idx in reversed(stale_rows):   # знизу вгору, щоб не зсувати індекси
        ws.delete_rows(idx)
    return due

def _mark_reminded_sync(row):
    _drafts_ws().update([["1"]], f"D{row}")

async def remind_about_drafts_periodically():
    """Раз на годину: незавершені чернетки старші за 24 год → бот пише
    менеджеру з ЖИВОЮ сумою кошторису і кнопкою «Продовжити»."""
    await asyncio.sleep(120)   # даємо сервісу піднятись
    while True:
        try:
            due = await asyncio.to_thread(_scan_drafts_for_reminders_sync)
            if due:
                prices = await async_get_prices()
            for row, uid, payload in due:
                try:
                    b = calculate_budget(apply_virtual_measurements(payload), prices, labels=get_price_labels())
                    total = round(b["total_work"] + b["total_mat_min"])
                    name = (payload.get("client") or {}).get("name") or "без назви"
                    rooms_n = len((payload.get("answers") or {}).get("rooms") or [])
                    kb = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="✏️ Продовжити заявку", web_app=WebAppInfo(url=WEBAPP_URL))
                    ]]) if WEBAPP_URL else None
                    await bot.send_message(
                        chat_id=int(uid),
                        text=(f"⏰ *Незавершена заявка*\n\nКлієнт: *{html.escape(str(name))}*\n"
                              f"Приміщень: {rooms_n}\nОрієнтовний кошторис: *{total:,} ₴*\n\n"
                              f"Чернетка чекає — завершіть, поки клієнт не охолов.").replace(",", " "),
                        parse_mode="Markdown", reply_markup=kb)
                    await asyncio.to_thread(_mark_reminded_sync, row)
                    await asyncio.sleep(0.5)     # не впираємось у ліміти Telegram
                except Exception:
                    logging.exception("Не вдалося нагадати про чернетку (user %s)", uid)
        except Exception:
            logging.exception("Цикл нагадувань про чернетки впав — повторю за годину")
        await asyncio.sleep(3600)

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
    user_id, _role = auth_request(request)   # працює і в Telegram, і на сайті
    if not user_id: return web.json_response({"error": "Unauthorized"}, status=401)
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
# ==========================================================
# ПУБЛІЧНИЙ КАЛЬКУЛЯТОР: заявка від гостя (БЕЗ авторизації)
# ----------------------------------------------------------
# Гість рахує кошторис сам на сайті й лишає контакт. Лід падає в ту саму
# таблицю з source="web" і БЕЗ manager_id — тобто у «вільний пул»: його
# бачать усі менеджери й може забрати собі будь-хто (кнопка «Взяти в роботу»).
# Захист: rate-limit по IP + honeypot-поле. CAPTCHA свідомо не ставлю —
# вона вбиває конверсію, а ставки тут невисокі.
# ==========================================================
_LEAD_RATE = {}          # {ip: [timestamps]}
LEAD_MAX_PER_HOUR = 5

def _rate_limited(ip):
    now = time.time()
    hits = [t for t in _LEAD_RATE.get(ip, []) if now - t < 3600]
    hits.append(now)
    _LEAD_RATE[ip] = hits
    if len(_LEAD_RATE) > 5000:      # не даємо словнику рости нескінченно
        _LEAD_RATE.clear()
    return len(hits) > LEAD_MAX_PER_HOUR

@cors
async def api_submit_lead(request):
    try:
        data = await request.json()
        # Honeypot: приховане поле, яке заповнюють лише боти
        if data.get("website"):
            return web.json_response({"success": True})   # вдаємо успіх, мовчки ігноруємо

        ip = request.headers.get("X-Forwarded-For", request.remote or "").split(",")[0].strip()
        if _rate_limited(ip):
            return web.json_response({"error": "too_many_requests"}, status=429)

        c = data.get("client") or {}
        phone = str(c.get("phone") or "").strip()
        digits = "".join(ch for ch in phone if ch.isdigit())
        if len(digits) < 9:
            return web.json_response({"error": "bad_phone"}, status=400)
        if not str(c.get("name") or "").strip():
            return web.json_response({"error": "no_name"}, status=400)

        answers = data.get("answers") or {}
        # НЕ ДОВІРЯЄМО ЦІНАМ ВІД ГОСТЯ. У фронтенді розділ «Нестандартні роботи»
        # гостю не показується, але POST можна надіслати й напряму — тож
        # вирізаємо його тут: інакше будь-хто міг би підкинути в кошторис
        # довільні суми (хоч мільйон, хоч нуль).
        if isinstance(answers, dict) and answers.get("custom_works"):
            answers = {k: v for k, v in answers.items() if k != "custom_works"}

        lead = {"client": c, "answers": answers, "source": "web", "manager_id": ""}
        success, err = await async_save_to_sheet(lead)
        if not success:
            await notify_admin_about_error("Заявка з сайту", err)
            return web.json_response({"error": "save_failed"}, status=500)

        # Рахуємо суму й розсилаємо менеджерам — лід гарячий, реагувати треба швидко
        try:
            prices = await async_get_prices()
            b = calculate_budget(apply_virtual_measurements(lead), prices, labels=get_price_labels())
            total = round(b["total_work"] + b["total_mat_min"])
            rooms_n = len((lead["answers"] or {}).get("rooms") or [])
            text = (f"🌐 *НОВА ЗАЯВКА З САЙТУ*\n\n"
                    f"👤 {html.escape(str(c.get('name')))}\n"
                    f"📞 `{html.escape(phone)}`\n"
                    f"🏠 {html.escape(str(c.get('object_type') or '—'))}, {html.escape(str(c.get('area') or '?'))} м²\n"
                    f"🚪 Приміщень: {rooms_n}\n"
                    f"💰 Орієнтовно: *{total:,} ₴*\n\n"
                    f"_Клієнт порахував сам. Лід вільний — беріть у роботу._").replace(",", " ")
            for uid, info in get_all_authorized_users().items():
                try:
                    await bot.send_message(chat_id=int(uid), text=text, parse_mode="Markdown")
                    await asyncio.sleep(0.1)
                except Exception:
                    pass
        except Exception:
            logging.exception("Не вдалося розіслати сповіщення про лід із сайту")

        return web.json_response({"success": True})
    except Exception:
        logging.exception("submit_lead failed")
        return web.json_response({"error": "server_error"}, status=500)

@cors
async def api_me(request):
    """Хто я: роль визначає, що покаже фронтенд.
    Працює для обох входів (міні-апка або веб-сесія). Без доступу — гість:
    вільний калькулятор + форма контакту в кінці."""
    uid, role = auth_request(request)
    if not uid:
        return web.json_response({"role": "guest"})
    info = get_all_authorized_users().get(str(uid), {})
    return web.json_response({"role": role, "user_id": uid, "name": info.get("name", "")})

# ==========================================================
# ВЕБ-КАБІНЕТ: вхід через Telegram Login Widget + API кабінету/адмінки
# ==========================================================
# ==========================================================
# ВХІД НА САЙТ ЧЕРЕЗ БОТА (без Login Widget і без /setdomain)
# ----------------------------------------------------------
# Як це працює:
#   1. Сайт просить у бекенда одноразовий код → отримує deep link
#      t.me/<bot>?start=web_ABC123.
#   2. Людина тисне кнопку → відкривається бот → одне натискання «Запустити».
#   3. Бот бачить свій же код, знає user_id (Telegram його гарантує) і
#      прив'язує код до цієї людини.
#   4. Сайт, який усе це врем'я опитує статус, отримує сесійний токен.
#
# Чому це безпечно: код живе 5 хвилин, одноразовий, і прив'язати його може
# лише той, хто реально написав боту зі свого акаунта. Паролів немає.
# Чому це простіше за Login Widget: не треба /setdomain, не треба знати
# username бота (питаємо його в самого Telegram), працює і в браузері,
# і всередині Telegram.
# ==========================================================
_WEB_LOGIN = {}                  # {code: {"ts": float, "uid": str|None}}
LOGIN_CODE_TTL = 300             # 5 хвилин
_BOT_USERNAME_CACHE = None

async def _get_bot_username():
    global _BOT_USERNAME_CACHE
    if not _BOT_USERNAME_CACHE:
        me = await bot.get_me()
        _BOT_USERNAME_CACHE = me.username
    return _BOT_USERNAME_CACHE

def _cleanup_login_codes():
    now = time.time()
    for c in [c for c, v in _WEB_LOGIN.items() if now - v["ts"] > LOGIN_CODE_TTL]:
        _WEB_LOGIN.pop(c, None)

@cors
async def api_login_start(request):
    """Видає одноразовий код і deep link на бота."""
    _cleanup_login_codes()
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    code = "".join(secrets.choice(alphabet) for _ in range(6))
    _WEB_LOGIN[code] = {"ts": time.time(), "uid": None}
    username = await _get_bot_username()
    return web.json_response({
        "code": code,
        "bot": username,
        "deep_link": f"https://t.me/{username}?start=web_{code}",
        "ttl": LOGIN_CODE_TTL,
    })

@cors
async def api_login_poll(request):
    """Сайт опитує: чи підтвердив уже хтось цей код у боті?"""
    _cleanup_login_codes()
    code = (request.query.get("code") or "").strip().upper()
    entry = _WEB_LOGIN.get(code)
    if not entry:
        return web.json_response({"status": "expired"})
    if not entry["uid"]:
        return web.json_response({"status": "pending"})

    uid = entry["uid"]
    role = get_role(uid)
    _WEB_LOGIN.pop(code, None)              # код одноразовий
    if not role:
        return web.json_response({"status": "no_access"})
    info = get_all_authorized_users().get(str(uid), {})
    return web.json_response({
        "status": "ok",
        "token": create_session(uid, role),
        "role": role,
        "user_id": str(uid),
        "name": info.get("name", ""),
    })

def bind_login_code(code, user_id):
    """Викликається з бота: прив'язує код до людини. True, якщо код живий."""
    _cleanup_login_codes()
    entry = _WEB_LOGIN.get(str(code).strip().upper())
    if not entry:
        return False
    entry["uid"] = str(user_id)
    return True

@cors
async def api_login(request):
    """Обмін підписаних даних Telegram Login Widget на сесійний токен."""
    try:
        data = await request.json()
        user_id = validate_login_widget(data, BOT_TOKEN)
        if not user_id:
            return web.json_response({"error": "bad_signature"}, status=401)

        role = get_role(user_id)
        if not role:
            # Вхід є, але доступу немає. Даємо шанс одразу активувати інвайт-код,
            # щоб людину не викидало «в нікуди».
            code = str(data.get("invite") or "").strip().upper()
            if code:
                ok, res = await asyncio.to_thread(
                    redeem_invite, code, user_id,
                    f"{data.get('first_name','')} {data.get('last_name','')}".strip() or "Менеджер",
                    data.get("username") or "немає")
                if not ok:
                    return web.json_response({"error": "bad_invite", "message": res}, status=403)
                role = res
            else:
                return web.json_response({"error": "no_access"}, status=403)

        info = get_all_authorized_users().get(str(user_id), {})
        return web.json_response({
            "token": create_session(user_id, role),
            "role": role,
            "user_id": str(user_id),
            "name": info.get("name") or data.get("first_name") or "",
        })
    except Exception:
        logging.exception("login failed")
        return web.json_response({"error": "server_error"}, status=500)

@cors
async def api_orders(request):
    """Список заявок для кабінету (з фільтром, пошуком і пагінацією).
    Свідомо НЕ віддаємо тут повний JSON анкети й не рахуємо кошториси —
    інакше кожне відкриття списку тягнуло б мегабайти й тисячі множень.
    Сума конкретної заявки приїжджає окремо, коли її розгортають."""
    uid, role = auth_request(request)
    if not uid:
        return web.json_response({"error": "unauthorized"}, status=401)
    deal = request.query.get("deal") or None
    query = request.query.get("q") or None
    try:
        limit = max(1, min(int(request.query.get("limit", 20)), 100))
        offset = max(0, int(request.query.get("offset", 0)))
    except ValueError:
        limit, offset = 20, 0

    orders = await async_list_orders(uid, role, deal, query)
    users = get_all_authorized_users()
    page = orders[offset:offset + limit]
    for o in page:
        o["manager_name"] = users.get(o["manager_id"], {}).get("name", "") if o["manager_id"] else ""

    # Лічильники по статусах рахуємо з ТОГО САМОГО кешу — без зайвих читань
    all_for_counts = await async_list_orders(uid, role, None, query)
    counts = {k: sum(1 for o in all_for_counts if o["deal"] == k) for k in DEAL_STATUSES}

    return web.json_response({
        "orders": page,
        "total": len(orders),
        "counts": counts,
        "all_total": len(all_for_counts),
        "role": role,
        "has_more": offset + limit < len(orders),
    })

@cors
async def api_order_detail(request):
    """Повна заявка + порахований кошторис. Викликається лише коли менеджер
    розгортає конкретну картку — тож важкий JSON читаємо точково."""
    uid, role = auth_request(request)
    if not uid:
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        row_id = int(request.query.get("row"))
    except (TypeError, ValueError):
        return web.json_response({"error": "bad_row"}, status=400)

    row = await async_get_row_data(row_id)
    if not row:
        return web.json_response({"error": "not_found"}, status=404)
    m = _row_meta(row)

    # Менеджер не має бачити чужі заявки навіть за прямим row_id
    if role != ROLE_ADMIN:
        mine = m["manager_id"] == str(uid)
        free_lead = (m["source"] == "web" and not m["manager_id"])
        if not (mine or free_lead):
            return web.json_response({"error": "forbidden"}, status=403)

    budget = None
    rooms = []
    try:
        payload = json.loads(row[5])
        prices = await async_get_prices()
        b = calculate_budget(apply_virtual_measurements(payload), prices, labels=get_price_labels())
        rc = b.get("room_costs") or {}
        for r in (payload.get("answers") or {}).get("rooms") or []:
            c = rc.get(r.get("id")) or [0, 0, 0]
            rooms.append({
                "name": r.get("name"),
                "area": (r.get("measurements") or {}).get("floor"),
                "work": round(c[0]), "mat_min": round(c[1]),
                "lines": (b.get("room_lines") or {}).get(r.get("id"), []),
            })
        budget = {
            "work": round(b["total_work"]),
            "mat_min": round(b["total_mat_min"]),
            "mat_max": round(b["total_mat_max"]),
            "total": round(b["total_work"] + b["total_mat_min"]),
            "general_lines": b.get("general_lines") or [],
        }
    except Exception:
        logging.exception("order_detail: не вдалося порахувати кошторис для рядка %s", row_id)

    m["manager_name"] = get_all_authorized_users().get(m["manager_id"], {}).get("name", "") if m["manager_id"] else ""
    return web.json_response({"order": m, "budget": budget, "rooms": rooms})

@cors
async def api_order_status(request):
    """Зміна статусу угоди / взяття вільного ліда в роботу."""
    uid, role = auth_request(request)
    if not uid:
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        data = await request.json()
        row = int(data.get("row"))
        deal = str(data.get("deal") or "new")
        if deal not in DEAL_STATUSES:
            return web.json_response({"error": "bad_status"}, status=400)
        claim = uid if data.get("claim") else None
        await async_set_deal_status(row, deal, claim_by=claim)
        await async_log_action(f"web:{uid}", f"🔄 Статус заявки {row} → {DEAL_STATUSES[deal]}")
        return web.json_response({"success": True})
    except Exception:
        logging.exception("order_status failed")
        return web.json_response({"error": "server_error"}, status=500)

@cors
async def api_admin_users(request):
    """Список доступів. Тільки для адміна."""
    uid, role = auth_request(request)
    if role != ROLE_ADMIN:
        return web.json_response({"error": "forbidden"}, status=403)
    users = get_all_authorized_users(force_refresh=True)
    out = [{"user_id": u, "name": i.get("name", ""), "username": i.get("username", ""),
            "role": i.get("role", ROLE_MANAGER), "is_master": str(u) == str(MASTER_ADMIN_ID)}
           for u, i in users.items()]
    return web.json_response({"users": out})

@cors
async def api_admin_invite(request):
    """Створення одноразового коду для нового менеджера. Тільки адмін."""
    uid, role = auth_request(request)
    if role != ROLE_ADMIN:
        return web.json_response({"error": "forbidden"}, status=403)
    code = await asyncio.to_thread(create_invite, uid, ROLE_MANAGER)
    if not code:
        return web.json_response({"error": "create_failed"}, status=500)
    return web.json_response({"code": code, "ttl_days": 7})

@cors
async def api_admin_revoke(request):
    """Відкликання доступу. Майстер-адміна забрати не можна."""
    uid, role = auth_request(request)
    if role != ROLE_ADMIN:
        return web.json_response({"error": "forbidden"}, status=403)
    data = await request.json()
    target = str(data.get("user_id") or "")
    if not target or target == str(MASTER_ADMIN_ID):
        return web.json_response({"error": "forbidden"}, status=403)
    await asyncio.to_thread(remove_authorized_user, target)
    await async_log_action(f"web:{uid}", f"⛔️ Відкликав доступ у {target}")
    return web.json_response({"success": True})

@cors
async def api_admin_stats(request):
    """Воронка: статуси, джерела, зріз по менеджерах."""
    uid, role = auth_request(request)
    if role != ROLE_ADMIN:
        return web.json_response({"error": "forbidden"}, status=403)
    orders = await async_list_orders(uid, ROLE_ADMIN)
    users = get_all_authorized_users()
    by_status = {k: sum(1 for o in orders if o["deal"] == k) for k in DEAL_STATUSES}
    by_mgr = {}
    for o in orders:
        if not o["manager_id"]:
            continue
        nm = users.get(o["manager_id"], {}).get("name", o["manager_id"])
        by_mgr[nm] = by_mgr.get(nm, 0) + 1
    won, lost = by_status["won"], by_status["lost"]
    return web.json_response({
        "total": len(orders),
        "by_status": by_status,
        "labels": DEAL_STATUSES,
        "web_leads": sum(1 for o in orders if o["source"] == "web"),
        "conversion": (won * 100 // (won + lost)) if (won + lost) else None,
        "by_manager": by_mgr,
    })

@cors
async def api_create_order(request):
    """Нова заявка з ВЕБ-кабінету. У міні-апці заявка йде через tg.sendData
    (бот ловить web_app_data), але в браузері такого каналу немає — тож
    менеджер з веб-сесією зберігає її сюди. Авторство підписуємо з сесії,
    а не з тіла запиту: підмінити чужий manager_id неможливо."""
    uid, role = auth_request(request)
    if not uid:
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        data = await request.json()
        payload = {"client": data.get("client") or {},
                   "answers": data.get("answers") or {},
                   "manager_id": str(uid), "source": "manager"}
        success, err = await async_save_to_sheet(payload)
        if not success:
            await notify_admin_about_error("Заявка з веб-кабінету", err)
            return web.json_response({"error": "save_failed"}, status=500)
        name = (payload["client"] or {}).get("name", "")
        await async_log_action(f"web:{uid}", f"🆕 СТВОРИВ нову заявку: {name}")
        try:
            await bot.send_message(chat_id=int(uid),
                                   text=f"✅ *Заявку прийнято* (з веб-кабінету)\n👤 {html.escape(str(name))}",
                                   parse_mode="Markdown")
        except Exception:
            pass
        return web.json_response({"success": True})
    except Exception:
        logging.exception("create_order failed")
        return web.json_response({"error": "server_error"}, status=500)

@cors
async def api_ping(request):
    return web.Response(text="Pong! Bot is alive 24/7")

@cors
async def api_version(request):
    """Що САМЕ зараз крутиться на проді. Render сам віддає SHA коміту в
    RENDER_GIT_COMMIT — тепер видно, чи доїхав деплой, без здогадок."""
    await async_get_prices()   # переконуємось, що прайс завантажено (і бачимо джерело)
    commit = os.getenv('RENDER_GIT_COMMIT', 'local')
    return web.json_response({
        "commit": commit,
        "commit_short": commit[:7],
        "branch": os.getenv('RENDER_GIT_BRANCH', '-'),
        "started_at": datetime.fromtimestamp(_STARTED_AT).strftime("%Y-%m-%d %H:%M:%S"),
        "uptime_min": round((time.time() - _STARTED_AT) / 60, 1),
        "prices": _PRICES_META,          # source: sheet|default, loaded_at, count
        "features": ["room_costs", "room_lines", "drafts", "prices_sheet"],
    })

@cors
async def api_live_calc(request):
    try:
        data = await request.json()
        data_with_virtual_meas = apply_virtual_measurements(data)
        prices = await async_get_prices()
        b = calculate_budget(data_with_virtual_meas, prices, labels=get_price_labels())
        # Розбивка для міні-апки: по приміщеннях + «загальні роботи»
        # (демонтаж, стяжка, стеля, двері, електророзводка) як залишок.
        # Це НЕ додаткові гроші — той самий total, розкладений на частини.
        rc = b.get("room_costs") or {}
        rooms_break = {rid: {"work": round(v[0]), "mat_min": round(v[1])} for rid, v in rc.items()}
        rooms_w = sum(v[0] for v in rc.values())
        rooms_m = sum(v[1] for v in rc.values())
        return web.json_response({
            "work": b["total_work"],
            "mat_min": b["total_mat_min"],
            "rooms": rooms_break,
            "general": {
                "work": round(max(b["total_work"] - rooms_w, 0)),
                "mat_min": round(max(b["total_mat_min"] - rooms_m, 0)),
            },
            # Построчна деталізація: {room_id: [{label, qty, unit, rate, work, mat_min}]}
            "room_lines": b.get("room_lines") or {},
            "general_lines": b.get("general_lines") or [],
        })
    except Exception:
        # Повний traceback — у лог Render (там його і шукати при дебагу);
        # клієнту — генерик: текст винятку може розкривати внутрішню
        # структуру даних/шляхи, а публічному ендпоінту це ні до чого.
        logging.exception("live_calc failed")
        return web.json_response({"error": "calc_failed"}, status=500)

def get_main_menu_keyboard(user_id=None):
    rows = [
        [KeyboardButton(text="📝 Заповнити анкету", web_app=WebAppInfo(url=WEBAPP_URL))],
        [KeyboardButton(text="📂 Мої заявки"), KeyboardButton(text="🔐 Кабінет менеджера")],
    ]
    if user_id and is_admin(user_id):
        rows.append([KeyboardButton(text="⚙️ Адмін-панель")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

@dp.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject = None):
    # DEEP LINK ВХОДУ НА САЙТ: t.me/<bot>?start=web_ABC123
    # Людина натиснула кнопку на сайті — тут ми підтверджуємо, що це справді
    # вона (Telegram гарантує user_id), і прив'язуємо код. Сайт впустить сам.
    payload = (command.args or "") if command else ""
    if payload.startswith("web_"):
        code = payload[4:]
        if not is_authorized(message.from_user.id):
            return await message.answer(
                "🔒 У вас ще немає доступу до кабінету.\n\n"
                "Надішліть мені *код доступу*, який видав адміністратор, "
                "і повторіть вхід на сайті.", parse_mode="Markdown")
        if bind_login_code(code, message.from_user.id):
            return await message.answer(
                "✅ *Вхід підтверджено!*\n\nПоверніться на сайт — кабінет уже відкрито.",
                parse_mode="Markdown", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return await message.answer("⌛️ Код входу застарів. Оновіть сторінку сайту й спробуйте ще раз.")

    if not is_authorized(message.from_user.id): return await message.answer(MSG_START_AUTH.format(name=message.from_user.first_name), parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True))
    await message.answer(MSG_START_MAIN.format(name=message.from_user.first_name), reply_markup=get_main_menu_keyboard(message.from_user.id), parse_mode="Markdown")

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
    # Підписуємо заявку автором — саме за цим полем менеджер потім
    # бачить її у «Мої заявки», а адмін знає, чия це робота.
    data["manager_id"] = str(message.from_user.id)
    data["source"] = "manager"
    success, error_msg = await async_save_to_sheet(data)
    if success:
        await message.answer("✅ **Нову заявку прийнято!**", parse_mode="Markdown")
        await async_log_action(message.from_user.full_name, f"🆕 СТВОРИВ нову заявку: {data.get('client', {}).get('name', '')}")
    else: 
        await notify_admin_about_error(f"Збереження заявки від {message.from_user.full_name}", error_msg)
        await message.answer("⚠️ Помилка збереження. Адміністратора повідомлено.")

# ==========================================================
# «МОЇ ЗАЯВКИ»: воронка менеджера
# ----------------------------------------------------------
# Раніше кабінет показував просто «активні заявки» — без авторства, без
# статусу угоди і без пошуку. Тепер це справжня воронка:
#   🆕 Нова → 📤 КП відправлено → ✅ Виграна / ❌ Програна
# Менеджер бачить свої заявки + вільні ліди з сайту; адмін — усі.
# ==========================================================
ORDERS_PER_PAGE = 8

def _fmt_order_line(m):
    src = "🌐" if m["source"] == "web" else "👔"
    free = " · 🔥вільний" if (m["source"] == "web" and not m["manager_id"]) else ""
    return f"{DEAL_STATUSES[m['deal']].split()[0]}{src} {m['name'] or 'Без імені'}{free}"

async def _orders_view(user_id, deal_filter=None, query=None, page=1):
    role = get_role(user_id)
    orders = await async_list_orders(user_id, role, deal_filter, query)
    kb = InlineKeyboardBuilder()

    # Рядок фільтрів: лічильники по кожному статусу
    all_orders = await async_list_orders(user_id, role, None, query)
    counts = {k: sum(1 for o in all_orders if o["deal"] == k) for k in DEAL_STATUSES}
    for key, label in DEAL_STATUSES.items():
        mark = "•" if deal_filter == key else ""
        kb.button(text=f"{mark}{label.split()[0]} {counts[key]}", callback_data=f"of_{key}")
    kb.button(text=("•📋 Усі" if not deal_filter else "📋 Усі"), callback_data="of_all")
    kb.adjust(4, 1)

    total_pages = max(1, (len(orders) + ORDERS_PER_PAGE - 1) // ORDERS_PER_PAGE)
    page = max(1, min(page, total_pages))
    chunk = orders[(page - 1) * ORDERS_PER_PAGE: page * ORDERS_PER_PAGE]
    for m in chunk:
        kb.row(InlineKeyboardButton(text=_fmt_order_line(m), callback_data=f"od_{m['row']}"))

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"op_{page-1}_{deal_filter or 'all'}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"op_{page+1}_{deal_filter or 'all'}"))
    if nav:
        kb.row(*nav)
    kb.row(InlineKeyboardButton(text="🔍 Пошук", callback_data="osearch"))

    who = "усі заявки компанії" if role == ROLE_ADMIN else "ваші заявки + вільні ліди"
    head = f"📂 *Заявки* ({who})\nЗнайдено: {len(orders)}"
    if query:
        head += f"\n🔍 Пошук: «{html.escape(query)}»"
    return head, kb.as_markup()

@dp.message(F.text == "📂 Мої заявки")
@dp.message(Command("orders"))
async def cmd_my_orders(message: Message):
    if not is_authorized(message.from_user.id): return
    text, kb = await _orders_view(message.from_user.id)
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("of_"))
async def orders_filter(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id): return await callback.answer()
    key = callback.data[3:]
    text, kb = await _orders_view(callback.from_user.id, None if key == "all" else key)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("op_"))
async def orders_page(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id): return await callback.answer()
    _, page, key = callback.data.split("_", 2)
    text, kb = await _orders_view(callback.from_user.id, None if key == "all" else key, page=int(page))
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("od_"))
async def order_detail(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id): return await callback.answer()
    row_id = int(callback.data[3:])
    row = await async_get_row_data(row_id)
    if not row:
        return await callback.answer("Заявку не знайдено", show_alert=True)
    m = _row_meta(row)

    # Рахуємо суму на льоту — щоб менеджер бачив вагу угоди одразу в списку
    total_txt = ""
    try:
        payload = json.loads(row[5])
        prices = await async_get_prices()
        b = calculate_budget(apply_virtual_measurements(payload), prices, labels=get_price_labels())
        total_txt = f"\n💰 Кошторис: *{round(b['total_work'] + b['total_mat_min']):,} ₴*".replace(",", " ")
    except Exception:
        pass

    owner = "—"
    if m["manager_id"]:
        owner = get_all_authorized_users().get(m["manager_id"], {}).get("name", m["manager_id"])
    elif m["source"] == "web":
        owner = "🔥 вільний лід"

    text = (f"{DEAL_STATUSES[m['deal']]}  ·  {SOURCE_LABELS.get(m['source'], m['source'])}\n\n"
            f"👤 *{html.escape(m['name'])}*\n📞 `{html.escape(m['phone'])}`\n"
            f"🏠 {html.escape(m['address'])}\n📅 {m['date']}\n👔 Менеджер: {html.escape(str(owner))}{total_txt}")

    kb = InlineKeyboardBuilder()
    if WEBAPP_URL:
        kb.row(InlineKeyboardButton(text="✏️ Редагувати анкету",
                                    web_app=WebAppInfo(url=f"{WEBAPP_URL}?edit_id={row_id}")))
    # Кнопки статусів — окрім поточного
    status_btns = [InlineKeyboardButton(text=label, callback_data=f"os_{row_id}_{key}")
                   for key, label in DEAL_STATUSES.items() if key != m["deal"]]
    kb.row(*status_btns[:2])
    if len(status_btns) > 2:
        kb.row(*status_btns[2:])
    if m["source"] == "web" and not m["manager_id"]:
        kb.row(InlineKeyboardButton(text="🙋 Взяти в роботу", callback_data=f"oc_{row_id}"))
    kb.row(InlineKeyboardButton(text="⬅️ До списку", callback_data="of_all"))
    await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("os_"))
async def order_set_status(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id): return await callback.answer()
    _, row_id, deal = callback.data.split("_", 2)
    if deal not in DEAL_STATUSES:
        return await callback.answer()
    await async_set_deal_status(row_id, deal)
    await async_log_action(callback.from_user.full_name, f"🔄 Статус заявки {row_id} → {DEAL_STATUSES[deal]}")
    await callback.answer(f"Статус: {DEAL_STATUSES[deal]}")
    callback.data = f"od_{row_id}"
    await order_detail(callback)

@dp.callback_query(F.data.startswith("oc_"))
async def order_claim(callback: CallbackQuery):
    """Менеджер забирає вільний лід із сайту собі."""
    if not is_authorized(callback.from_user.id): return await callback.answer()
    row_id = callback.data[3:]
    await async_set_deal_status(row_id, "new", claim_by=callback.from_user.id)
    await async_log_action(callback.from_user.full_name, f"🙋 Взяв у роботу лід із сайту (рядок {row_id})")
    await callback.answer("Лід ваш — успіхів!", show_alert=True)
    callback.data = f"od_{row_id}"
    await order_detail(callback)

@dp.callback_query(F.data == "osearch")
async def order_search_prompt(callback: CallbackQuery):
    _SEARCH_WAIT.add(callback.from_user.id)
    await callback.message.answer("🔍 Надішліть ім'я, телефон або адресу для пошуку:")
    await callback.answer()

_SEARCH_WAIT = set()      # хто зараз вводить пошуковий запит

# ==========================================================
# АДМІН-ПАНЕЛЬ: керування менеджерами через інвайт-коди
# ==========================================================
@dp.message(F.text == "⚙️ Адмін-панель")
@dp.message(Command("admin_panel"))
async def cmd_admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        return
    users = get_all_authorized_users()
    managers = {u: i for u, i in users.items() if i.get("role") != ROLE_ADMIN}
    admins = {u: i for u, i in users.items() if i.get("role") == ROLE_ADMIN}
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="➕ Створити код для менеджера", callback_data="inv_manager"))
    kb.row(InlineKeyboardButton(text="👥 Менеджери та доступи", callback_data="inv_list"))
    kb.row(InlineKeyboardButton(text="📊 Статистика воронки", callback_data="inv_stats"))
    await message.answer(
        f"⚙️ *Адмін-панель*\n\nАдміністраторів: {len(admins)}\nМенеджерів: {len(managers)}\n\n"
        f"_Доступ видається одноразовим кодом — паролі не використовуються._",
        reply_markup=kb.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data == "inv_manager")
async def admin_create_invite(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return await callback.answer()
    code = await asyncio.to_thread(create_invite, callback.from_user.id, ROLE_MANAGER)
    if not code:
        return await callback.answer("Не вдалося створити код", show_alert=True)
    await callback.message.answer(
        f"🎟 *Код доступу для менеджера*\n\n`{code}`\n\n"
        f"Надішліть його людині. Вона відкриває бота і просто надсилає цей код повідомленням.\n"
        f"Код одноразовий і діє 7 днів.", parse_mode="Markdown")
    await callback.answer("Код створено")

@dp.callback_query(F.data == "inv_list")
async def admin_list_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return await callback.answer()
    users = get_all_authorized_users(force_refresh=True)
    kb = InlineKeyboardBuilder()
    lines = []
    for uid, info in users.items():
        badge = "👑" if info.get("role") == ROLE_ADMIN else "👔"
        lines.append(f"{badge} {info.get('name', '—')} (@{info.get('username', '—')})")
        if str(uid) != str(MASTER_ADMIN_ID):
            kb.row(InlineKeyboardButton(text=f"❌ Забрати доступ: {info.get('name', uid)}",
                                        callback_data=f"revoke_{uid}"))
    await callback.message.answer("👥 *Доступи:*\n\n" + "\n".join(lines),
                                  reply_markup=kb.as_markup(), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "inv_stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return await callback.answer()
    orders = await async_list_orders(callback.from_user.id, ROLE_ADMIN)
    if not orders:
        await callback.message.answer("Заявок поки немає.")
        return await callback.answer()
    by_status = {k: sum(1 for o in orders if o["deal"] == k) for k in DEAL_STATUSES}
    web_n = sum(1 for o in orders if o["source"] == "web")
    by_mgr = {}
    users = get_all_authorized_users()
    for o in orders:
        if not o["manager_id"]:
            continue
        nm = users.get(o["manager_id"], {}).get("name", o["manager_id"])
        by_mgr[nm] = by_mgr.get(nm, 0) + 1
    won, lost = by_status["won"], by_status["lost"]
    conv = f"{won * 100 // max(1, won + lost)}%" if (won + lost) else "—"
    text = ("📊 *Статистика воронки*\n\n" +
            "\n".join(f"{DEAL_STATUSES[k]}: *{v}*" for k, v in by_status.items()) +
            f"\n\n🌐 З сайту: *{web_n}* із {len(orders)}\n🎯 Конверсія (виграні/закриті): *{conv}*\n\n" +
            "*По менеджерах:*\n" + ("\n".join(f"👔 {n}: {c}" for n, c in sorted(by_mgr.items(), key=lambda x: -x[1])) or "—"))
    await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()

@dp.message(F.text)
async def process_password_attempts(message: Message):
    # 1) Авторизований і зараз вводить пошуковий запит по заявках
    if message.from_user.id in _SEARCH_WAIT:
        _SEARCH_WAIT.discard(message.from_user.id)
        text, kb = await _orders_view(message.from_user.id, query=message.text)
        return await message.answer(text, reply_markup=kb, parse_mode="Markdown")

    if is_authorized(message.from_user.id): return

    # 2) Неавторизований: пробуємо як ОДНОРАЗОВИЙ ІНВАЙТ-КОД.
    # Старий спільний ADMIN_PASSWORD лишається робочим лише для майстер-адміна
    # (щоб ти не втратив доступ, якщо таблиця з ролями раптом ляже).
    code = (message.text or "").strip()
    if len(code) == 8 and code.isalnum():
        ok, res = await asyncio.to_thread(
            redeem_invite, code, message.from_user.id,
            message.from_user.full_name, message.from_user.username or "немає")
        if ok:
            try: await message.delete()     # прибираємо код із чату
            except Exception: pass
            await message.answer(MSG_AUTH_SUCCESS, reply_markup=get_main_menu_keyboard(message.from_user.id), parse_mode="Markdown")
            try:
                await bot.send_message(MASTER_ADMIN_ID,
                    f"🟢 <b>НОВИЙ МЕНЕДЖЕР</b>\n{html.escape(message.from_user.full_name)} (@{message.from_user.username or '—'})",
                    parse_mode="HTML")
            except Exception: pass
            return
        return await message.answer(f"❌ {res}")

    if message.text == ADMIN_PASSWORD and message.from_user.id == MASTER_ADMIN_ID:
        add_authorized_user(message.from_user.id, message.from_user.full_name,
                            message.from_user.username or "немає", ROLE_ADMIN)
        return await message.answer(MSG_AUTH_SUCCESS, reply_markup=get_main_menu_keyboard(message.from_user.id), parse_mode="Markdown")

    await message.answer(MSG_AUTH_FAIL)

async def on_startup(bot: Bot):
    _get_google_sheet()
    try: await bot.set_webhook(f"{WEBHOOK_URL}{WEBHOOK_PATH}", secret_token=WEBHOOK_SECRET)
    except: pass
    asyncio.create_task(clean_locks_periodically())
    # Прогріваємо прайс одразу (заодно створює аркуш «Ціни», якщо його ще нема)
    asyncio.create_task(async_get_prices())
    # Нагадування про незавершені чернетки (раз на годину)
    asyncio.create_task(remind_about_drafts_periodically())

async def on_shutdown(bot: Bot): await bot.session.close()

def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    if art_router is not None:
        dp.include_router(art_router)
    app = web.Application()
    
    # Твої існуючі маршрути (по одному разу!)
    app.router.add_get('/api/get_order', api_get_order)
    app.router.add_options('/api/get_order', api_get_order)
    app.router.add_post('/api/save_order', api_save_order)
    app.router.add_options('/api/save_order', api_save_order)
    app.router.add_post('/api/live_calc', api_live_calc)
    app.router.add_options('/api/live_calc', api_live_calc)
    # Веб-кабінет: вхід через Telegram Login Widget
    app.router.add_post('/api/login_start', api_login_start)
    app.router.add_options('/api/login_start', api_login_start)
    app.router.add_get('/api/login_poll', api_login_poll)
    app.router.add_options('/api/login_poll', api_login_poll)
    app.router.add_post('/api/login', api_login)
    app.router.add_options('/api/login', api_login)
    app.router.add_get('/api/orders', api_orders)
    app.router.add_options('/api/orders', api_orders)
    app.router.add_get('/api/order_detail', api_order_detail)
    app.router.add_options('/api/order_detail', api_order_detail)
    app.router.add_post('/api/order_status', api_order_status)
    app.router.add_options('/api/order_status', api_order_status)
    app.router.add_post('/api/create_order', api_create_order)
    app.router.add_options('/api/create_order', api_create_order)
    # Адмінка на сайті
    app.router.add_get('/api/admin/users', api_admin_users)
    app.router.add_options('/api/admin/users', api_admin_users)
    app.router.add_post('/api/admin/invite', api_admin_invite)
    app.router.add_options('/api/admin/invite', api_admin_invite)
    app.router.add_post('/api/admin/revoke', api_admin_revoke)
    app.router.add_options('/api/admin/revoke', api_admin_revoke)
    app.router.add_get('/api/admin/stats', api_admin_stats)
    app.router.add_options('/api/admin/stats', api_admin_stats)
    # Публічний калькулятор: хто я + заявка від гостя
    app.router.add_get('/api/me', api_me)
    app.router.add_options('/api/me', api_me)
    app.router.add_post('/api/submit_lead', api_submit_lead)
    app.router.add_options('/api/submit_lead', api_submit_lead)
    # Серверні чернетки
    app.router.add_post('/api/save_draft', api_save_draft)
    app.router.add_options('/api/save_draft', api_save_draft)
    app.router.add_get('/api/get_draft', api_get_draft)
    app.router.add_options('/api/get_draft', api_get_draft)
    app.router.add_post('/api/delete_draft', api_delete_draft)
    app.router.add_options('/api/delete_draft', api_delete_draft)
    # Діагностика: який коміт і звідки ціни
    app.router.add_get('/version', api_version)
    app.router.add_options('/version', api_version)
    
    # Наш новий маршрут для бота 24/7
    app.router.add_get('/ping', api_ping)
    
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=WEBHOOK_SECRET).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    web.run_app(app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)

if __name__ == "__main__": 
    main()
