import os
import json
import logging
import sys
import re
import html
import time
import csv
import base64
import secrets
import io
from datetime import datetime, timedelta
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
from google.oauth2.service_account import Credentials

from storage import (STORAGE_BACKEND, PRICES_BACKEND, PRICES_EDITABLE,
                     async_list_prices, async_upsert_prices)
from security import AUTH_BACKEND
from security import (ADMIN_PASSWORD, MASTER_ADMIN_ID, is_authorized, get_all_authorized_users,
                      add_authorized_user, remove_authorized_user, clear_auth_cache,
                      ROLE_ADMIN, ROLE_MANAGER, get_role, create_invite, redeem_invite)
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

# Налаштування і дефолтний прайс винесено в config.py (Етап 2).
from config import (
    BOT_TOKEN, GEMINI_API_KEY, GOOGLE_CREDS_JSON, SPREADSHEET_NAME,
    GROUP_CHAT_ID, WEB_SERVER_HOST, WEB_SERVER_PORT, WEBHOOK_URL,
    WEBHOOK_PATH, WEBAPP_URL, WEBHOOK_SECRET, SESSION_SECRET,
    _ALLOWED_ORIGINS, _require_env, DEFAULT_PRICES,
    SHEET_HEADER, PRICES_SHEET_NAME, _PRICES_HEADER,
)
# Доступ до сховища через фасад storage (Етап 3): sheets|postgres за env.
from storage import (
    _log_action_sync, async_log_action, _get_google_sheet, _ensure_header_sync,
    async_ensure_header, _save_to_sheet_sync, _update_row_sync, _get_row_data_sync,
    _row_meta, invalidate_orders_cache, _fetch_orders_rows_sync,
    _list_orders_sync, _soft_delete_sync, _purge_rows_sync,
    _list_trash_sync, async_soft_delete, async_purge_rows, async_list_trash,
    async_list_orders, _save_report_sync,
    async_save_to_sheet, async_update_row, async_get_row_data, async_save_report,
    _prices_bootstrap_sheet, _get_prices_sync, get_price_labels,
    async_get_prices, _drafts_ws, _save_draft_sync, _get_draft_sync, _delete_draft_sync,
    _scan_drafts_for_reminders_sync, _mark_reminded_sync,
    DRAFTS_SHEET_NAME, DRAFT_REMIND_AFTER_H, DRAFT_TTL_DAYS,
    _PRICES_META,
)

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
_STARTED_AT = time.time()

def add_cors_headers(response, origin=None):
    # Відповідаємо конкретним origin лише якщо він у білому списку.
    # Для чужих origin заголовок не ставимо взагалі — браузер сам заблокує.
    if origin and origin in _ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Vary'] = 'Origin'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Telegram-Init-Data, X-Session-Token'
    return response

def cors(handler):
    @wraps(handler)
    async def wrapper(request):
        origin = request.headers.get('Origin')
        if request.method == 'OPTIONS':
            return add_cors_headers(web.Response(), origin)
        response = await handler(request)
        return add_cors_headers(response, origin)
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
    sig = hmac.new(SESSION_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"

def read_session(token):
    """Повертає {'uid', 'role'} або None. Роль ПЕРЕПЕРЕВІРЯЄМО в таблиці —
    якщо доступ відкликано, старий токен перестає діяти негайно."""
    try:
        body, sig = str(token).split(".", 1)
        expect = hmac.new(SESSION_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
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




# --- РОЗШИРЕНА СХЕМА ЗАЯВКИ ---
# A-H були раніше. Додаємо:
#   I: manager_id — хто створив (порожньо = гість із сайту)
#   J: source     — "manager" | "web"
#   K: deal       — статус угоди: new | sent | won | lost
# Старі рядки без I-K читаються як (невідомий менеджер, manager, new) —
# зворотна сумісність повна, мігрувати таблицю руками не треба.
SOURCE_LABELS = {"manager": "👔 Менеджер", "web": "🌐 Сайт (самостійно)"}









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







# ==========================================================
# ВИДАЛЕННЯ ЗАЯВОК: два рівні
# ----------------------------------------------------------
# 1) КОШИК (soft delete) — колонка H = "видалена". Заявка зникає зі списку,
#    але фізично лишається: її можна відновити. Так працює будь-яка адекватна
#    CRM, бо випадковий клік не має знищувати контакт клієнта назавжди.
# 2) ОСТАТОЧНА ЧИСТКА (hard delete) — рядок фізично видаляється з таблиці.
#    Доступна ЛИШЕ адміну і лише для того, що вже лежить у кошику.
# Рядки видаляємо пачками, згрупувавши в суцільні діапазони: 50 окремих
# викликів delete_rows — це 50 звернень до API і майже гарантований 429.
# ==========================================================











# ЄДИНЕ ДЖЕРЕЛО ПРАВДИ (fallback): ціни Стандарт (мін) і Преміум (макс).
# Ці значення використовуються, якщо аркуш "Ціни" в Google-таблиці ще не
# створено або він недоступний. При першому успішному підключенні аркуш
# створюється автоматично і заповнюється цими ж значеннями.









# ==========================================================
# СЕРВЕРНІ ЧЕРНЕТКИ + НАГАДУВАННЯ ЧЕРЕЗ 24 ГОД
# Аркуш "Drafts": user_id | updated_at (ISO) | payload (JSON) | reminded (0/1)
# Чернетка живе не лише в localStorage телефона: менеджер може продовжити
# з іншого пристрою, а недороблена заявка не губиться — бот нагадає.
# ==========================================================





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
    # Було: приймали лише initData, тож із браузера (сесійний вхід) кнопка
    # «Анкета» падала. Тепер — та сама auth_request, що й решта кабінету.
    uid, role = auth_request(request)
    if not uid:
        return web.json_response({"error": "unauthorized"}, status=401)

    row_id = request.rel_url.query.get('edit_id')
    if not row_id: return web.json_response({"error": "No ID"}, status=400)

    now = time.time()
    if str(row_id) in _LOCKS:
        lock = _LOCKS[str(row_id)]
        if lock["expires"] > now and lock["user_id"] != uid:
            return web.json_response({"error": f"🔒 Цю заявку зараз редагує {lock['user_name']}!"}, status=423)

    auth_users = get_all_authorized_users()
    _LOCKS[str(row_id)] = { "user_id": uid, "user_name": auth_users.get(str(uid), {}).get("name", "Колега"), "expires": now + 600 }

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
async def api_order_delete(request):
    """У КОШИК (м'яке видалення). Менеджер може прибрати лише свою заявку
    або вільний лід; адмін — будь-яку. Дані не знищуються."""
    uid, role = auth_request(request)
    if not uid:
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        row_id = int((await request.json()).get("row"))
    except (TypeError, ValueError):
        return web.json_response({"error": "bad_row"}, status=400)

    raw = await async_get_row_data(row_id)
    if not raw:
        return web.json_response({"error": "not_found"}, status=404)
    m = _row_meta(raw)
    if role != ROLE_ADMIN:
        mine = m["manager_id"] == str(uid)
        free_lead = (m["source"] == "web" and not m["manager_id"])
        if not (mine or free_lead):
            return web.json_response({"error": "forbidden"}, status=403)

    await async_soft_delete(row_id, True)
    await async_log_action(f"web:{uid}", f"🗑 У кошик: заявка {row_id} ({m['name']})")
    return web.json_response({"success": True})

@cors
async def api_order_restore(request):
    """Повернути заявку з кошика."""
    uid, role = auth_request(request)
    if not uid:
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        row_id = int((await request.json()).get("row"))
    except (TypeError, ValueError):
        return web.json_response({"error": "bad_row"}, status=400)
    raw = await async_get_row_data(row_id)
    if not raw:
        return web.json_response({"error": "not_found"}, status=404)
    m = _row_meta(raw)
    if role != ROLE_ADMIN and m["manager_id"] != str(uid):
        return web.json_response({"error": "forbidden"}, status=403)
    await async_soft_delete(row_id, False)
    await async_log_action(f"web:{uid}", f"♻️ Відновлено заявку {row_id}")
    return web.json_response({"success": True})

@cors
async def api_trash(request):
    """Вміст кошика."""
    uid, role = auth_request(request)
    if not uid:
        return web.json_response({"error": "unauthorized"}, status=401)
    items = await async_list_trash(role, uid)
    users = get_all_authorized_users()
    for m in items:
        m["manager_name"] = users.get(m["manager_id"], {}).get("name", "") if m["manager_id"] else ""
    return web.json_response({"orders": items, "role": role})

@cors
async def api_purge(request):
    """ОСТАТОЧНЕ видалення — тільки адмін і тільки з кошика.
    Приймає або список рядків, або older_than_days (авточистка).
    Захист: усе, що не позначене «видалена», ігнорується — тобто активну
    заявку неможливо знищити цим ендпоінтом навіть навмисно."""
    uid, role = auth_request(request)
    if role != ROLE_ADMIN:
        return web.json_response({"error": "forbidden"}, status=403)
    data = await request.json()

    trash = await async_list_trash(ROLE_ADMIN, uid)
    trash_rows = {m["row"]: m for m in trash}

    if data.get("older_than_days"):
        try:
            days = int(data["older_than_days"])
        except (TypeError, ValueError):
            return web.json_response({"error": "bad_days"}, status=400)
        limit_dt = datetime.now() - timedelta(days=days)
        targets = []
        for r, m in trash_rows.items():
            try:
                # У колонці A формат "YYYY-MM-DD HH:MM" (+ можливий суфікс)
                dt = datetime.strptime(m["date"][:16], "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            if dt < limit_dt:
                targets.append(r)
    else:
        requested = data.get("rows") or []
        targets = [int(r) for r in requested if int(r) in trash_rows]

    if not targets:
        return web.json_response({"success": True, "deleted": 0})

    deleted = await async_purge_rows(targets)
    await async_log_action(f"web:{uid}", f"🔥 ОСТАТОЧНО видалено заявок: {deleted}")
    return web.json_response({"success": True, "deleted": deleted})

@cors
async def api_orders(request):
    """Список заявок для кабінету (з фільтром, пошуком і пагінацією).
    Свідомо НЕ віддаємо тут повний JSON анкети й не рахуємо кошториси —
    інакше кожне відкриття списку тягнуло б мегабайти й тисячі множень.
    Сума конкретної заявки приїжджає окремо, коли її розгортають."""
    uid, role = auth_request(request)
    if not uid:
        return web.json_response({"error": "unauthorized"}, status=401)
    query = request.query.get("q") or None
    try:
        limit = max(1, min(int(request.query.get("limit", 20)), 100))
        offset = max(0, int(request.query.get("offset", 0)))
    except ValueError:
        limit, offset = 20, 0

    orders = await async_list_orders(uid, role, None, query)
    users = get_all_authorized_users()
    page = orders[offset:offset + limit]
    for o in page:
        o["manager_name"] = users.get(o["manager_id"], {}).get("name", "") if o["manager_id"] else ""

    return web.json_response({
        "orders": page,
        "total": len(orders),
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
    m["report"] = row[6] if len(row) > 6 else ""   # збережений ТЗ (для показу/перегенерації на сайті)
    return web.json_response({"order": m, "budget": budget, "rooms": rooms})

@cors
async def api_generate_report(request):
    """Генерація ТЗ через Gemini для заявки (перенесено з бота на сайт).
    Ті самі права, що й на перегляд деталей; результат зберігаємо в заявці."""
    uid, role = auth_request(request)
    if not uid:
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await request.json()
        row_id = int(body.get("row"))
    except Exception:
        return web.json_response({"error": "bad_row"}, status=400)

    row = await async_get_row_data(row_id)
    if not row:
        return web.json_response({"error": "not_found"}, status=404)
    m = _row_meta(row)
    if role != ROLE_ADMIN:
        mine = m["manager_id"] == str(uid)
        free_lead = (m["source"] == "web" and not m["manager_id"])
        if not (mine or free_lead):
            return web.json_response({"error": "forbidden"}, status=403)

    if model is None:
        return web.json_response({"error": "gemini_unavailable"}, status=503)

    raw_answers = row[5]
    report_text = ""
    for attempt in range(3):
        try:
            resp = await model.generate_content_async(GEMINI_PROMPT.format(raw_answers=raw_answers))
            report_text = resp.text.replace("```html", "").replace("```", "").strip()
            break
        except Exception:
            if attempt == 2:
                logging.exception("api_generate_report: Gemini не відповів для рядка %s", row_id)
                return web.json_response({"error": "generation_failed"}, status=502)
            await asyncio.sleep(1)

    await async_save_report(row_id, report_text)
    info = get_all_authorized_users().get(str(uid), {})
    await async_log_action(info.get("name", f"web:{uid}"), f"✨ Згенерував ТЗ (Рядок {row_id})")
    return web.json_response({"report": report_text})

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
async def api_admin_prices(request):
    """Прайс для редактора. Тільки адмін.

    Віддає ВСІ позиції калькулятора, а не лише збережені в БД: якщо позиції
    ще немає в таблиці prices, повертається значення з коду з прапорцем
    saved=false. Інакше редактор показував би порожній список на свіжій базі.
    """
    uid, role = auth_request(request)
    if role != ROLE_ADMIN:
        return web.json_response({"error": "forbidden"}, status=403)
    if not PRICES_EDITABLE:
        return web.json_response(
            {"error": "read_only",
             "message": "Ціни зараз беруться з Google-таблиці. Редагувати їх "
                        "можна там, або перемкнути PRICES_BACKEND=postgres."},
            status=409)
    try:
        items = await async_list_prices()
    except Exception as e:
        logging.error("Не вдалося віддати прайс редактору: %s", e)
        return web.json_response({"error": "read_failed"}, status=500)
    return web.json_response({"prices": items, "editable": True})


@cors
async def api_admin_prices_save(request):
    """Збереження змінених позицій. Тільки адмін.

    Фронт надсилає ЛИШЕ змінені рядки — так у журналі видно, що саме правили,
    і випадковий «зберегти все» не переписує 83 позиції з тими самими числами.
    """
    uid, role = auth_request(request)
    if role != ROLE_ADMIN:
        return web.json_response({"error": "forbidden"}, status=403)
    if not PRICES_EDITABLE:
        return web.json_response({"error": "read_only"}, status=409)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "bad_json"}, status=400)

    items = data.get("items") or []
    if not isinstance(items, list) or not items:
        return web.json_response({"error": "empty"}, status=400)
    if len(items) > 500:
        return web.json_response({"error": "too_many"}, status=400)

    try:
        n = await async_upsert_prices(items, updated_by=str(uid))
    except ValueError as e:
        # Осмислена помилка валідації — показуємо людині як є.
        return web.json_response({"error": "invalid", "message": str(e)}, status=400)
    except Exception as e:
        logging.error("Не вдалося зберегти прайс: %s", e)
        return web.json_response({"error": "save_failed"}, status=500)

    names = ", ".join(str(i.get("key", "")) for i in items[:5])
    if len(items) > 5:
        names += f" +{len(items) - 5}"
    info = get_all_authorized_users().get(str(uid), {})
    await async_log_action(info.get("name", f"web:{uid}"),
                           f"💰 Змінив прайс ({n} поз.): {names}")
    return web.json_response({"success": True, "saved": n})


@cors
async def api_admin_stats(request):
    """Воронка: статуси, джерела, зріз по менеджерах."""
    uid, role = auth_request(request)
    if role != ROLE_ADMIN:
        return web.json_response({"error": "forbidden"}, status=403)
    orders = await async_list_orders(uid, ROLE_ADMIN)
    users = get_all_authorized_users()
    by_mgr = {}
    for o in orders:
        if not o["manager_id"]:
            continue
        nm = users.get(o["manager_id"], {}).get("name") or f"Менеджер #{o['manager_id']}"
        by_mgr[nm] = by_mgr.get(nm, 0) + 1
    return web.json_response({
        "total": len(orders),
        "web_leads": sum(1 for o in orders if o["source"] == "web"),
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
                   "manager_id": str(uid), "source": "manager",
                   "submission_id": data.get("submission_id")}   # захист від дублю
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
    # Скільки заявок сервер РЕАЛЬНО бачить у таблиці. Це головна діагностика:
    # якщо тут число > 0, а кабінет порожній — проблема в доступі/фронтенді,
    # а не в збереженні. Якщо 0 або error — дивимось саме на таблицю.
    orders_info = {}
    try:
        rows = await asyncio.to_thread(_fetch_orders_rows_sync)
        orders_info = {"rows_visible": len(rows)}
    except Exception as e:
        orders_info = {"error": str(e)[:200]}

    return web.json_response({
        "commit": commit,
        "commit_short": commit[:7],
        "branch": os.getenv('RENDER_GIT_BRANCH', '-'),
        "started_at": datetime.fromtimestamp(_STARTED_AT).strftime("%Y-%m-%d %H:%M:%S"),
        "uptime_min": round((time.time() - _STARTED_AT) / 60, 1),
        "prices": _PRICES_META,          # source: sheet|default, loaded_at, count
        "orders": orders_info,
        # Які саме бекенди активні просто зараз. Без цього після кожного
        # перемикання доводиться гадати, чи підхопилась змінна оточення.
        "backends": {
            "storage": STORAGE_BACKEND,   # заявки та чернетки
            "prices": PRICES_BACKEND,     # прайс
            "auth": AUTH_BACKEND,         # користувачі та інвайти
        },
        "features": ["room_costs", "room_lines", "drafts", "prices_sheet", "trash", "web_cabinet"],
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
    # Бот став лаунчером: увесь функціонал (анкета, перегляд заявок, стата,
    # коди доступу) живе на сайті / у Web App. У меню — одна кнопка, що його
    # відкриває. Дублювання цих екранів у боті прибрано.
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🚀 Відкрити застосунок", web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True,
    )

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

@dp.message(F.text)
async def process_password_attempts(message: Message):
    if is_authorized(message.from_user.id): return

    # Неавторизований: пробуємо як ОДНОРАЗОВИЙ ІНВАЙТ-КОД.
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
    # Самолікування таблиці: якщо в рядку 1 опинилась заявка замість шапки —
    # повертаємо шапку на місце (заявка з'їжджає на рядок 2 і стає видимою).
    asyncio.create_task(async_ensure_header())
    # Прогріваємо прайс одразу (заодно створює аркуш «Ціни», якщо його ще нема)
    asyncio.create_task(async_get_prices())
    # Нагадування про незавершені чернетки (раз на годину)
    asyncio.create_task(remind_about_drafts_periodically())

async def on_shutdown(bot: Bot): await bot.session.close()

def main():
    _require_env()   # падаємо голосно, якщо критичних env немає
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
    app.router.add_post('/api/generate_report', api_generate_report)
    app.router.add_options('/api/generate_report', api_generate_report)
    # Видалення: кошик → відновлення → остаточна чистка
    app.router.add_post('/api/order_delete', api_order_delete)
    app.router.add_options('/api/order_delete', api_order_delete)
    app.router.add_post('/api/order_restore', api_order_restore)
    app.router.add_options('/api/order_restore', api_order_restore)
    app.router.add_get('/api/trash', api_trash)
    app.router.add_options('/api/trash', api_trash)
    app.router.add_post('/api/purge', api_purge)
    app.router.add_options('/api/purge', api_purge)
    app.router.add_post('/api/create_order', api_create_order)
    app.router.add_options('/api/create_order', api_create_order)
    # Адмінка на сайті
    app.router.add_get('/api/admin/users', api_admin_users)
    app.router.add_options('/api/admin/users', api_admin_users)
    app.router.add_post('/api/admin/invite', api_admin_invite)
    app.router.add_options('/api/admin/invite', api_admin_invite)
    app.router.add_post('/api/admin/revoke', api_admin_revoke)
    app.router.add_options('/api/admin/revoke', api_admin_revoke)
    app.router.add_get('/api/admin/prices', api_admin_prices)
    app.router.add_options('/api/admin/prices', api_admin_prices)
    app.router.add_post('/api/admin/prices/save', api_admin_prices_save)
    app.router.add_options('/api/admin/prices/save', api_admin_prices_save)
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