"""Шар доступу до сховища (Google Sheets).

Виділено з main.py (Етап 2). Містить ЛИШЕ роботу з таблицею: синхронні
CRUD-хелпери, їхні async-обгортки через asyncio.to_thread, кеші та Google-auth.
НЕ імпортує aiogram/aiohttp/bot — тож коли на Етапі 3 з'явиться Postgres,
достатньо буде підмінити реалізацію тут, не чіпаючи бот- і API-логіку.
"""
import json
import time
import re
import math
import logging
import asyncio
from datetime import datetime, timedelta

import gspread
from google.oauth2.service_account import Credentials

from config import (GOOGLE_CREDS_JSON, SPREADSHEET_NAME, DEFAULT_PRICES,
                    DEAL_STATUSES, SHEET_HEADER, PRICES_SHEET_NAME, _PRICES_HEADER)
from security import ROLE_ADMIN

# --- Кеші (стан рівня процесу; на Етапі 3 переїде в БД) ---
_PRICES_CACHE = None
_PRICES_CACHE_TIME = 0
_PRICES_CACHE_TTL = 300 
_PRICE_LABELS = {}          # {price_key: "Назва з таблиці"} — для деталізації
_PRICES_META = {"source": "default", "loaded_at": None, "count": 0}  # для /version
_ORDERS_CACHE = {"rows": None, "ts": 0}
_ORDERS_TTL = 30
DRAFTS_SHEET_NAME = "Drafts"
DRAFT_REMIND_AFTER_H = 24        # через скільки годин нагадувати
DRAFT_TTL_DAYS = 30              # старші чернетки прибираємо


def _log_action_sync(user_name, action):
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        doc = gspread.authorize(Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), scopes=scope)).open(SPREADSHEET_NAME)
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
        return gspread.authorize(Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), scopes=scope)).open(SPREADSHEET_NAME).sheet1
    except Exception as e:
        return None


def _ensure_header_sync():
    """Гарантує, що рядок 1 — це ШАПКА, а не заявка.

    Через баг із append_row у рядок 1 могла потрапити справжня заявка,
    знищивши заголовки. Оскільки весь код читає дані з рядка 2, така заявка
    ставала невидимою. Тут ми це виявляємо і лікуємо БЕЗ втрати даних:
    вставляємо новий рядок зверху — заявка просто з'їжджає на рядок 2
    і одразу стає видимою.
    Ознака «в першому рядку заявка»: колонка A виглядає як дата (2026-...).
    """
    sheet = _get_google_sheet()
    if not sheet:
        return False
    first = sheet.row_values(1)
    a1 = (first[0] if first else "").strip()
    looks_like_order = bool(re.match(r"^\d{4}-\d{2}-\d{2}", a1))

    if looks_like_order:
        sheet.insert_row(SHEET_HEADER, index=1, value_input_option="RAW")
        logging.warning("Відновлено шапку таблиці: заявка з рядка 1 з'їхала на рядок 2.")
        invalidate_orders_cache()
        return True

    if not a1:                       # шапки взагалі немає (порожній рядок 1)
        sheet.update([SHEET_HEADER], "A1:K1", value_input_option="RAW")
        invalidate_orders_cache()
        return True
    return False


async def async_ensure_header():
    try:
        return await asyncio.to_thread(_ensure_header_sync)
    except Exception:
        logging.exception("Не вдалося перевірити шапку таблиці")
        return False


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

        # ЗАПИС ЗА ЯВНИМ АДРЕСОМ — не довіряємо автовизначенню Google.
        # append_row сам «шукає кінець таблиці» і на дірках у даних помиляється:
        # він уже записав заявку в РЯДОК 1, знищивши шапку (а весь код читає
        # дані з рядка 2 — тож заявка стала невидимою). Тепер рахуємо вільний
        # рядок самі, скануючи колонку A, і ніколи не пишемо вище рядка 2.
        col_a = sheet.col_values(1)
        last_filled = 0
        for i, val in enumerate(col_a, start=1):
            if str(val).strip():
                last_filled = i
        next_row = max(last_filled + 1, 2)      # рядок 1 — ЗАВЖДИ шапка

        if next_row > sheet.row_count:
            sheet.add_rows(20)

        sheet.update(
            [[str(v) for v in row_data]],
            f"A{next_row}:K{next_row}",
            value_input_option="RAW",
        )
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


def invalidate_orders_cache():
    _ORDERS_CACHE["rows"] = None
    _ORDERS_CACHE["ts"] = 0


def _fetch_orders_rows_sync(include_deleted=False):
    """Легке читання аркуша: тільки метадані заявок, без JSON анкети.
    include_deleted нічого не змінює в читанні (кеш спільний) — фільтрація
    за статусом відбувається вище, в _list_orders_sync / _list_trash_sync."""
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
    """Змінює статус угоди. claim_by — якщо менеджер бере вільний лід собі.
    Пишемо ОДНИМ batch-запитом: раніше було два окремих update_cell,
    тобто два звернення до Google на одну дію."""
    sheet = _get_google_sheet()
    if not sheet:
        return False
    updates = [{"range": f"K{int(row_id)}", "values": [[deal]]}]
    if claim_by:
        updates.append({"range": f"I{int(row_id)}", "values": [[str(claim_by)]]})
    sheet.batch_update(updates)
    invalidate_orders_cache()   # інакше кабінет ще 30 с показував би старий статус
    return True


def _soft_delete_sync(row_id, deleted=True):
    sheet = _get_google_sheet()
    if not sheet:
        return False
    sheet.update_cell(int(row_id), 8, "видалена" if deleted else "активна")   # H
    invalidate_orders_cache()
    return True


def _purge_rows_sync(rows):
    """Фізичне видалення рядків. Повертає кількість видалених.
    Видаляємо ЗВЕРХУ ВНИЗ у зворотному порядку — інакше після кожного
    видалення індекси нижніх рядків з'їжджають і ми зносимо не те."""
    sheet = _get_google_sheet()
    if not sheet or not rows:
        return 0
    rows = sorted({int(r) for r in rows if int(r) > 1}, reverse=True)
    if not rows:
        return 0

    # Групуємо сусідні рядки в суцільні діапазони (менше викликів API)
    groups = []
    start = prev = rows[0]
    for r in rows[1:]:
        if r == prev - 1:
            prev = r
            continue
        groups.append((prev, start))     # (від меншого, до більшого)
        start = prev = r
    groups.append((prev, start))

    deleted = 0
    for lo, hi in groups:
        sheet.delete_rows(lo, hi)
        deleted += hi - lo + 1
    invalidate_orders_cache()
    return deleted


def _list_trash_sync(role, user_id):
    """Кошик: те, що позначене як «видалена». Менеджер бачить лише свої."""
    out = []
    for entry in _fetch_orders_rows_sync(include_deleted=True):
        m = _meta_from_parts(entry)
        if m["status"] != "видалена":
            continue
        if role != ROLE_ADMIN and m["manager_id"] != str(user_id):
            continue
        out.append(m)
    out.reverse()
    return out


async def async_soft_delete(row_id, deleted=True):
    return await asyncio.to_thread(_soft_delete_sync, row_id, deleted)


async def async_purge_rows(rows):
    return await asyncio.to_thread(_purge_rows_sync, rows)


async def async_list_trash(role, user_id):
    return await asyncio.to_thread(_list_trash_sync, role, user_id)


async def async_list_orders(user_id, role, deal_filter=None, query=None):
    return await asyncio.to_thread(_list_orders_sync, user_id, role, deal_filter, query)


async def async_set_deal_status(row_id, deal, claim_by=None):
    return await asyncio.to_thread(_set_deal_status_sync, row_id, deal, claim_by)


def _delete_row_sync(row_id, user_name):
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        doc = gspread.authorize(Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), scopes=scope)).open(SPREADSHEET_NAME)
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


async def async_save_to_sheet(data): return await asyncio.to_thread(_save_to_sheet_sync, data)


async def async_update_row(row_id, data): return await asyncio.to_thread(_update_row_sync, row_id, data)


async def async_get_row_data(row_id): return await asyncio.to_thread(_get_row_data_sync, row_id)


async def async_save_report(row_id, text): await asyncio.to_thread(_save_report_sync, row_id, text)


async def async_delete_row(row_id, user_name): return await asyncio.to_thread(_delete_row_sync, row_id, user_name)


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
        doc = gspread.authorize(Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), scopes=scope)).open(SPREADSHEET_NAME)
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


def _drafts_ws():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    doc = gspread.authorize(Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), scopes=scope)).open(SPREADSHEET_NAME)
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
