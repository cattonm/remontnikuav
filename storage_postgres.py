"""Реалізація storage-інтерфейсу поверх Postgres (Етап 3).

Ті самі сигнатури й ФОРМИ даних, що й storage_sheets, тому бот-, API- і
UI-логіка в main.py лишається незмінною:
  • заявка з БД повертається списком з 11 полів у порядку SHEET_HEADER;
  • список заявок — через _fetch_orders_rows_sync -> _meta_from_parts
    (парсер спільний зі storage_sheets, тож словники ідентичні).

Синхронні функції виконуються в пулі потоків через наявні async-обгортки
(asyncio.to_thread), тому лишаємось на sync-драйвері psycopg2 без переписування
викликів. На нього ж покладено захист від подвійного збереження — унікальний
частковий індекс по submission_id (див. schema.sql).

Ціни теж живуть тут (Етап A): таблиця prices, ключ (tenant_id, key). Джерело
цін перемикається окремою змінною PRICES_BACKEND, незалежно від заявок, —
щоб переносити прайс без ризику для решти.
"""
import os
import json
import time
import logging
import asyncio
from contextlib import contextmanager
from datetime import datetime

import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from psycopg2.extras import Json

# Спільне зі storage_sheets: чисті парсери (не звертаються в Google) і ціни.
from storage_sheets import (
    _row_meta, _meta_from_parts,
    DRAFT_REMIND_AFTER_H, DRAFT_TTL_DAYS,
)
from config import DEFAULT_PRICES
from security import ROLE_ADMIN

# ── Підключення ───────────────────────────────────────────
_POOL = None


def _check_dsn(dsn):
    """Перевіряє форму рядка підключення і пояснює помилку людською мовою.

    ЧОМУ ЦЕ ПОТРІБНО. Найчастіша описка при ротації пароля — вставити
    ПОВНИЙ новий URL у місце, де має бути лише пароль. Виходить
    postgresql://user:postgresql://user:pass@host:5432/db@host:5432/db
    Психопг у відповідь каже `invalid integer value "postgresql:" for
    connection option "port"` — і на пошук причини йде півгодини.
    """
    if dsn.count("://") > 1:
        raise RuntimeError(
            "DATABASE_URL зіпсований: рядок підключення вставлено всередину "
            "самого себе (схема '://' трапляється двічі). Схоже, при ротації "
            "пароля туди поклали ПОВНИЙ URL замість самого пароля. "
            "Правильний вигляд: postgresql://КОРИСТУВАЧ:ПАРОЛЬ@ХОСТ:5432/postgres")
    if not dsn.startswith(("postgres://", "postgresql://")):
        raise RuntimeError(
            "DATABASE_URL має починатись з postgresql:// — зараз там щось інше.")


def _get_pool():
    global _POOL
    if _POOL is None:
        dsn = os.getenv("DATABASE_URL")
        if not dsn:
            raise RuntimeError("STORAGE_BACKEND=postgres, але DATABASE_URL не заданий")
        _check_dsn(dsn)
        _POOL = ThreadedConnectionPool(1, 8, dsn)
    return _POOL


@contextmanager
def _conn():
    pool = _get_pool()
    con = pool.getconn()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        pool.putconn(con)


_SELECT_COLS = ("date_text, name, phone, object_type, address, answers, "
                "report, status, manager_id, source, deal")


def _row_to_list(r):
    """Кортеж із БД -> список з 11 полів у порядку SHEET_HEADER.
    answers віддаємо РЯДКОМ JSON (як робила таблиця), бо main робить json.loads."""
    date_text, name, phone, otype, address, answers, report, status, mid, source, deal = r
    return [date_text, name, phone, otype, address,
            json.dumps(answers, ensure_ascii=False) if answers is not None else "{}",
            report or "", status or "активна", mid or "", source or "web", deal or "new"]


def _address_full(c):
    return (f"{c.get('address')} ({c.get('area', '0')} м² | "
            f"Пов: {c.get('floor', '1')} | Ліфт: {c.get('elevator', 'Немає')})")


# ── Заявки: запис ─────────────────────────────────────────
def _save_to_sheet_sync(data):
    try:
        c = data.get('client', {})
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        manager_id = str(data.get("manager_id") or "")
        source = data.get("source") or ("manager" if manager_id else "web")
        submission_id = data.get("submission_id") or None
        with _conn() as con, con.cursor() as cur:
            # ON CONFLICT DO NOTHING по частковому індексу submission_id:
            # повторна відправка тієї самої заявки не створює дубль.
            cur.execute(
                """INSERT INTO orders
                     (date_text, name, phone, object_type, address, answers,
                      manager_id, source, deal, submission_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'new',%s)
                   ON CONFLICT (submission_id) WHERE submission_id IS NOT NULL
                   DO NOTHING
                   RETURNING id""",
                (timestamp, c.get('name'), c.get('phone'), c.get('object_type'),
                 _address_full(c), Json(data), manager_id, source, submission_id),
            )
            inserted = cur.fetchone()
        if submission_id and inserted is None:
            logging.info("Дубль заявки submission_id=%s відхилено (ідемпотентність)", submission_id)
        return True, ""
    except Exception as e:
        logging.exception("PG save error")
        return False, str(e)


def _update_row_sync(row_id, data):
    try:
        c = data.get('client', {})
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M") + " (Оновлено)"
        with _conn() as con, con.cursor() as cur:
            cur.execute(
                """UPDATE orders SET date_text=%s, name=%s, phone=%s,
                        object_type=%s, address=%s, answers=%s
                   WHERE id=%s""",
                (timestamp, c.get('name'), c.get('phone'), c.get('object_type'),
                 _address_full(c), Json(data), int(row_id)),
            )
        return True, ""
    except Exception as e:
        return False, str(e)


def _save_report_sync(row_id, text):
    with _conn() as con, con.cursor() as cur:
        cur.execute("UPDATE orders SET report=%s WHERE id=%s", (text, int(row_id)))
    return True


# ── Заявки: читання ───────────────────────────────────────
def _get_row_data_sync(row_id):
    try:
        with _conn() as con, con.cursor() as cur:
            cur.execute(f"SELECT {_SELECT_COLS} FROM orders WHERE id=%s", (int(row_id),))
            r = cur.fetchone()
        return _row_to_list(r) if r else None
    except Exception:
        logging.exception("PG get_row error")
        return None


def _fetch_orders_rows_sync(include_deleted=False):
    """Легке читання: метадані у форматі {'row','a'(5),'b'(4)} — той самий, що
    очікують _meta_from_parts і _get_orders_keyboard_sync у main."""
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            "SELECT id, date_text, name, phone, object_type, address, "
            "status, manager_id, source, deal FROM orders ORDER BY id")
        rows = cur.fetchall()
    out = []
    for r in rows:
        rid, date_text, name, phone, otype, address, status, mid, source, deal = r
        out.append({
            "row": rid,
            "a": [date_text or "", name or "", phone or "", otype or "", address or ""],
            "b": [status or "активна", mid or "", source or "web", deal or "new"],
        })
    return out


def _list_orders_sync(user_id, role, deal_filter=None, query=None):
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
    out.reverse()
    return out


def _list_trash_sync(role, user_id):
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


# ── Заявки: статус / видалення ────────────────────────────
def _set_deal_status_sync(row_id, deal, claim_by=None):
    with _conn() as con, con.cursor() as cur:
        if claim_by:
            cur.execute("UPDATE orders SET deal=%s, manager_id=%s WHERE id=%s",
                        (deal, str(claim_by), int(row_id)))
        else:
            cur.execute("UPDATE orders SET deal=%s WHERE id=%s", (deal, int(row_id)))
    return True


def _soft_delete_sync(row_id, deleted=True):
    with _conn() as con, con.cursor() as cur:
        if deleted:
            cur.execute("UPDATE orders SET status='видалена', deleted_at=now() WHERE id=%s",
                        (int(row_id),))
        else:
            cur.execute("UPDATE orders SET status='активна', deleted_at=NULL, deleted_by=NULL WHERE id=%s",
                        (int(row_id),))
    return True


def _delete_row_sync(row_id, user_name):
    try:
        with _conn() as con, con.cursor() as cur:
            cur.execute(
                "UPDATE orders SET status='видалена', deleted_by=%s, deleted_at=now() WHERE id=%s",
                (user_name, int(row_id)))
        return True, ""
    except Exception as e:
        return False, str(e)


def _purge_rows_sync(rows):
    ids = [int(r) for r in rows if int(r) > 0]
    if not ids:
        return 0
    with _conn() as con, con.cursor() as cur:
        cur.execute("DELETE FROM orders WHERE id = ANY(%s)", (ids,))
        return cur.rowcount


# ── Журнал ────────────────────────────────────────────────
def _log_action_sync(user_name, action):
    try:
        with _conn() as con, con.cursor() as cur:
            cur.execute("INSERT INTO action_log (user_name, action) VALUES (%s,%s)",
                        (user_name, action))
    except Exception:
        logging.exception("PG log_action error")


def _ensure_header_sync():
    # У Postgres структуру задає схема — робити нічого не треба.
    return True


# ── Чернетки ──────────────────────────────────────────────
def _save_draft_sync(user_id, payload):
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """INSERT INTO drafts (user_id, payload, updated_at, reminded)
               VALUES (%s, %s, now(), FALSE)
               ON CONFLICT (user_id)
               DO UPDATE SET payload=EXCLUDED.payload, updated_at=now(), reminded=FALSE""",
            (str(user_id), Json(payload)))
    return True


def _get_draft_sync(user_id):
    with _conn() as con, con.cursor() as cur:
        cur.execute("SELECT updated_at, payload FROM drafts WHERE user_id=%s", (str(user_id),))
        r = cur.fetchone()
    if not r:
        return None
    return {"updated_at": r[0].isoformat(timespec="seconds"), "payload": r[1]}


def _delete_draft_sync(user_id):
    with _conn() as con, con.cursor() as cur:
        cur.execute("DELETE FROM drafts WHERE user_id=%s", (str(user_id),))
    return True


def _scan_drafts_for_reminders_sync():
    """[(user_id, user_id, payload)] для чернеток, старших за DRAFT_REMIND_AFTER_H
    і ще не нагаданих; протухлі (>DRAFT_TTL_DAYS) прибираємо. 'row' = user_id."""
    with _conn() as con, con.cursor() as cur:
        cur.execute("DELETE FROM drafts WHERE updated_at < now() - (%s || ' days')::interval",
                    (str(DRAFT_TTL_DAYS),))
        cur.execute(
            "SELECT user_id, payload FROM drafts "
            "WHERE reminded = FALSE AND updated_at <= now() - (%s || ' hours')::interval",
            (str(DRAFT_REMIND_AFTER_H),))
        rows = cur.fetchall()
    return [(uid, uid, payload) for uid, payload in rows]


def _mark_reminded_sync(row):
    with _conn() as con, con.cursor() as cur:
        cur.execute("UPDATE drafts SET reminded=TRUE WHERE user_id=%s", (str(row),))


# ── async-обгортки (сигнатури 1-в-1 зі storage_sheets) ────
async def async_log_action(user_name, action):
    await asyncio.to_thread(_log_action_sync, user_name, action)


async def async_save_to_sheet(data):
    return await asyncio.to_thread(_save_to_sheet_sync, data)


async def async_update_row(row_id, data):
    return await asyncio.to_thread(_update_row_sync, row_id, data)


async def async_get_row_data(row_id):
    return await asyncio.to_thread(_get_row_data_sync, row_id)


async def async_save_report(row_id, text):
    await asyncio.to_thread(_save_report_sync, row_id, text)


async def async_delete_row(row_id, user_name):
    return await asyncio.to_thread(_delete_row_sync, row_id, user_name)


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


async def async_ensure_header():
    return await asyncio.to_thread(_ensure_header_sync)


# ══════════════════════════════════════════════════════════
# ЦІНИ (Етап A: прайс переїхав із Google-таблиці в БД)
# ----------------------------------------------------------
# Контракт навмисно ТОЙ САМИЙ, що й у storage_sheets, щоб calculator.py і
# main.py не помітили підміни:
#     _get_prices_sync() -> {price_key: [робота, матеріал_мін, матеріал_макс]}
#     get_price_labels() -> {price_key: "Назва"}
#
# Логіка теж лишається «незламною»: база — DEFAULT_PRICES, поверх кладемо
# рядки з БД. Впала база / порожня таблиця / кривий рядок — калькулятор
# однаково рахує, просто на дефолтах. Прайс НЕ може обнулити кошторис.
# ══════════════════════════════════════════════════════════
TENANT_ID = int(os.getenv("TENANT_ID", "1"))   # Етап B: візьметься з сесії

_PRICES_CACHE = None
_PRICES_CACHE_TIME = 0
_PRICES_CACHE_TTL = 300          # 5 хв, як було в Sheets
_PRICE_LABELS = {}
_PRICES_META = {"source": "default", "loaded_at": None, "count": 0}   # для /version


def invalidate_prices_cache():
    """Скинути кеш після редагування прайсу — щоб зміна була видна одразу,
    а не через 5 хвилин."""
    global _PRICES_CACHE, _PRICES_CACHE_TIME
    _PRICES_CACHE = None
    _PRICES_CACHE_TIME = 0


def _get_prices_sync():
    global _PRICES_CACHE, _PRICES_CACHE_TIME, _PRICE_LABELS
    now = time.time()
    if _PRICES_CACHE and (now - _PRICES_CACHE_TIME) < _PRICES_CACHE_TTL:
        return _PRICES_CACHE

    prices = dict(DEFAULT_PRICES)
    labels = {}
    source = "default"
    try:
        with _conn() as con, con.cursor() as cur:
            cur.execute(
                "SELECT key, label, work, mat_min, mat_max, mat_mid FROM prices "
                "WHERE tenant_id = %s", (TENANT_ID,))
            rows = cur.fetchall()
        for key, label, work, mat_min, mat_max, mat_mid in rows:
            key = (key or "").strip()
            if not key:
                continue
            # Четвертий елемент — ціна рівня «Комфорт». None означає
            # «рахувати як середнє», і калькулятор це розуміє.
            prices[key] = [float(work), float(mat_min), float(mat_max),
                           None if mat_mid is None else float(mat_mid)]
            if label:
                labels[key] = label
        if rows:
            source = "postgres"
        else:
            # Таблиця порожня — прайс ще не імпортували. Це не помилка,
            # але про неї треба знати з /version, а не гадати.
            logging.warning("Таблиця prices порожня — працюю на DEFAULT_PRICES. "
                            "Запусти: python import_prices.py")
    except Exception as e:
        logging.error("Не вдалося прочитати ціни з БД (%s). Використовую %s.",
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
    return _PRICE_LABELS


async def async_get_prices():
    return await asyncio.to_thread(_get_prices_sync)


# ── Редактор прайсу (API кабінету) ────────────────────────
def _list_prices_sync():
    """Повний прайс тенанта для UI-редактора: список словників, відсортований
    за назвою. Ключі, яких ще немає в БД, підмішуються з DEFAULT_PRICES —
    щоб редактор показував ВСІ позиції калькулятора, а не тільки збережені."""
    from calculator import PRICE_META

    saved = {}
    try:
        with _conn() as con, con.cursor() as cur:
            cur.execute(
                "SELECT key, label, unit, work, mat_min, mat_max, mat_mid, updated_at, updated_by "
                "FROM prices WHERE tenant_id = %s", (TENANT_ID,))
            for k, label, unit, w, m1, m2, mid, upd, by in cur.fetchall():
                saved[k] = {
                    "key": k, "label": label, "unit": unit,
                    "work": float(w), "mat_min": float(m1), "mat_max": float(m2),
                    "mat_mid": None if mid is None else float(mid),
                    "updated_at": upd.strftime("%Y-%m-%d %H:%M") if upd else "",
                    "updated_by": by or "", "saved": True,
                }
    except Exception as e:
        logging.error("Не вдалося прочитати прайс для редактора: %s", e)
        raise

    out = []
    for key, (w, m1, m2) in DEFAULT_PRICES.items():
        if key in saved:
            out.append(saved.pop(key))
            continue
        meta_label, meta_unit = PRICE_META.get(key, (key, ""))
        out.append({"key": key, "label": meta_label, "unit": meta_unit,
                    "work": float(w), "mat_min": float(m1), "mat_max": float(m2),
                    "mat_mid": None,
                    "updated_at": "", "updated_by": "", "saved": False})
    out.extend(saved.values())   # позиції, доданих вручну і яких немає в коді
    out.sort(key=lambda r: (r["label"] or r["key"]).lower())
    return out


def _upsert_prices_sync(items, updated_by=""):
    """Зберегти зміни прайсу. items — список {key, work, mat_min, mat_max, label?, unit?}.

    Пише одним запитом на позицію в межах ОДНІЄЇ транзакції: або зберігається
    все, або нічого. Некоректні числа відкидаються тут, до бази, — а сама база
    ще й тримає CHECK-констрейнти як другий рубіж.
    """
    from calculator import PRICE_META

    clean = []
    for it in items or []:
        key = str(it.get("key", "")).strip()
        if not key:
            continue
        try:
            work = round(float(it.get("work", 0) or 0), 2)
            m1 = round(float(it.get("mat_min", 0) or 0), 2)
            m2 = round(float(it.get("mat_max", 0) or 0), 2)
        except (TypeError, ValueError):
            raise ValueError(f"Некоректне число в позиції «{key}»")
        if work < 0 or m1 < 0 or m2 < 0:
            raise ValueError(f"Відʼємна ціна в позиції «{key}»")
        if m2 < m1:
            m1, m2 = m2, m1          # описка «макс < мін» — мовчки міняємо місцями

        # Комфорт: порожнє поле = «рахувати як середнє». Значення поза
        # діапазоном підтягуємо до межі, інакше рівні переплутаються місцями.
        raw_mid = it.get("mat_mid", "")
        if raw_mid in (None, "", "auto"):
            mid = None
        else:
            try:
                mid = round(float(raw_mid), 2)
            except (TypeError, ValueError):
                raise ValueError(f"Некоректна ціна «Комфорт» у позиції «{key}»")
            if mid < 0:
                raise ValueError(f"Відʼємна ціна «Комфорт» у позиції «{key}»")
            mid = min(max(mid, m1), m2)

        meta_label, meta_unit = PRICE_META.get(key, (key, ""))
        clean.append((
            TENANT_ID, key,
            str(it.get("label") or meta_label).strip()[:200],
            str(it.get("unit") or meta_unit).strip()[:20],
            work, m1, m2, mid, str(updated_by)[:64],
        ))

    if not clean:
        return 0

    with _conn() as con, con.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO prices (tenant_id, key, label, unit, work, mat_min, mat_max, mat_mid, updated_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, key) DO UPDATE SET
                label = EXCLUDED.label,
                unit = EXCLUDED.unit,
                work = EXCLUDED.work,
                mat_min = EXCLUDED.mat_min,
                mat_max = EXCLUDED.mat_max,
                mat_mid = EXCLUDED.mat_mid,
                updated_by = EXCLUDED.updated_by,
                updated_at = now()
            """, clean)
    invalidate_prices_cache()
    return len(clean)


async def async_list_prices():
    return await asyncio.to_thread(_list_prices_sync)


async def async_upsert_prices(items, updated_by=""):
    return await asyncio.to_thread(_upsert_prices_sync, items, updated_by)


# ══════════════════════════════════════════════════════════
# НАЛАШТУВАННЯ ТЕНАНТА (брендинг для PDF, і далі — для віджета)
# ══════════════════════════════════════════════════════════
_TENANT_CACHE = None
_TENANT_CACHE_TIME = 0
_TENANT_CACHE_TTL = 300


def get_tenant_branding():
    """Назва компанії, контакти й акцентний колір із tenants.settings.

    Порожній результат — не помилка: PDF має свої значення за замовчуванням.
    На Етапі B тенант братиметься із сесії, поки що це TENANT_ID з оточення.
    """
    global _TENANT_CACHE, _TENANT_CACHE_TIME
    now = time.time()
    if _TENANT_CACHE is not None and (now - _TENANT_CACHE_TIME) < _TENANT_CACHE_TTL:
        return _TENANT_CACHE
    data = {}
    try:
        with _conn() as con, con.cursor() as cur:
            cur.execute("SELECT name, settings FROM tenants WHERE id = %s", (TENANT_ID,))
            row = cur.fetchone()
        if row:
            name, settings = row
            data = dict(settings or {})
            if name and not data.get("company"):
                data["company"] = name
    except Exception as e:
        logging.warning("Не вдалося прочитати налаштування тенанта: %s", e)
        return _TENANT_CACHE or {}
    _TENANT_CACHE = data
    _TENANT_CACHE_TIME = now
    return data