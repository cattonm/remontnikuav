"""Авторизація через Postgres (таблиці users та invites).

Контракт ТОЧНО такий самий, як у security_sheets — main.py не мусить
знати, звідки беруться користувачі:

    get_all_authorized_users(force_refresh=False) -> {"<tg_id>": {name, username, role}}
    add_authorized_user(user_id, name, username, role) -> bool
    remove_authorized_user(user_id) -> bool
    get_role(user_id) -> "admin" | "manager" | None
    is_admin(user_id) / is_authorized(user_id) -> bool
    create_invite(created_by, role) -> "КОД" | None
    redeem_invite(code, user_id, name, username) -> (успіх, роль_або_причина)
    clear_auth_cache() -> True

ЩО ЗМІНИЛОСЬ ПО СУТІ (не лише сховище):

1. Погашення інвайта стало атомарним. У Sheets-версії між «прочитали, що
   код вільний» і «записали, що він використаний» був проміжок: два
   переходи за кодом одночасно давали двох користувачів на один код. Тут
   це один UPDATE ... WHERE used_by IS NULL — база фізично не дозволить
   погасити код двічі.

2. Видалення користувача не стирає рядок, а ставить status='revoked'.
   Історія лишається (кому і коли давали доступ), а повторне додавання
   тієї ж людини просто повертає її в active.

3. Кеш живе 60 секунд, а не «до перезапуску». Postgres швидкий, тримати
   стару копію годинами немає причин: зміну ролі видно майже одразу.
"""
import os
import time
import logging
import secrets
import datetime

from security_sheets import (
    ADMIN_PASSWORD, MASTER_ADMIN_ID,
    ROLE_ADMIN, ROLE_MANAGER, INVITE_TTL_DAYS,
)

logger = logging.getLogger(__name__)

TENANT_ID = int(os.getenv("TENANT_ID", "1"))   # Етап B: візьметься з сесії

_AUTH_CACHE = None
_AUTH_CACHE_TIME = 0.0
_AUTH_CACHE_TTL = 60

# Без 0/O та 1/I — код диктують голосом і в чаті.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _conn():
    """Береться пул зі storage_postgres, щоб не плодити друге зʼєднання."""
    from storage_postgres import _conn as pg_conn
    return pg_conn()


# ── Користувачі ───────────────────────────────────────────
def get_all_authorized_users(force_refresh=False):
    """Активні користувачі тенанта. При збої БД — попередній кеш, інакше {}.

    Порожній словник означає «не знаю», а не «нікого немає»: майстер-адмін
    перевіряється окремо в get_role/is_authorized і заходить завжди.
    """
    global _AUTH_CACHE, _AUTH_CACHE_TIME
    now = time.time()
    if (_AUTH_CACHE is not None and not force_refresh
            and (now - _AUTH_CACHE_TIME) < _AUTH_CACHE_TTL):
        return _AUTH_CACHE

    try:
        with _conn() as con, con.cursor() as cur:
            cur.execute(
                "SELECT tg_id, name, username, role FROM users "
                "WHERE tenant_id = %s AND status = 'active' AND tg_id IS NOT NULL",
                (TENANT_ID,))
            data = {
                str(tg_id).strip(): {"name": name or "", "username": username or "",
                                     "role": role or ROLE_MANAGER}
                for tg_id, name, username, role in cur.fetchall()
            }
    except Exception as e:
        logger.error("Не вдалося прочитати користувачів із БД: %s", e)
        if _AUTH_CACHE is not None:
            logger.warning("Використовую кеш авторизації")
            return _AUTH_CACHE
        return {}

    _AUTH_CACHE = data
    _AUTH_CACHE_TIME = now
    return data


def add_authorized_user(user_id, name, username, role=ROLE_MANAGER):
    """Додає користувача або повертає раніше відкликаного в active."""
    clean_id = str(user_id).strip()
    if role not in (ROLE_ADMIN, ROLE_MANAGER):
        role = ROLE_MANAGER
    try:
        with _conn() as con, con.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (tenant_id, tg_id, name, username, role, status)
                VALUES (%s, %s, %s, %s, %s, 'active')
                ON CONFLICT (tenant_id, tg_id) WHERE tg_id IS NOT NULL
                DO UPDATE SET name = EXCLUDED.name,
                              username = EXCLUDED.username,
                              role = EXCLUDED.role,
                              status = 'active'
                """,
                (TENANT_ID, clean_id, name or "", username or "", role))
        clear_auth_cache()
        return True
    except Exception as e:
        logger.error("Не вдалося додати користувача %s: %s", clean_id, e)
        return False


def remove_authorized_user(user_id):
    """Відкликає доступ. Рядок лишається — щоб було видно, кому давали."""
    clean_id = str(user_id).strip()
    try:
        with _conn() as con, con.cursor() as cur:
            cur.execute(
                "UPDATE users SET status = 'revoked' "
                "WHERE tenant_id = %s AND tg_id = %s", (TENANT_ID, clean_id))
        clear_auth_cache()
        return True
    except Exception as e:
        logger.error("Не вдалося відкликати доступ %s: %s", clean_id, e)
        return False


def get_role(user_id):
    clean_id = str(user_id).strip()
    if clean_id == str(MASTER_ADMIN_ID):
        return ROLE_ADMIN          # аварійний вхід: працює навіть коли БД лягла
    info = get_all_authorized_users().get(clean_id)
    return info.get("role") if info else None


def is_admin(user_id):
    return get_role(user_id) == ROLE_ADMIN


def is_authorized(user_id):
    if str(user_id).strip() == str(MASTER_ADMIN_ID):
        return True
    return str(user_id).strip() in get_all_authorized_users()


def clear_auth_cache():
    global _AUTH_CACHE, _AUTH_CACHE_TIME
    _AUTH_CACHE = None
    _AUTH_CACHE_TIME = 0.0
    return True


# ── Інвайти ───────────────────────────────────────────────
def create_invite(created_by, role=ROLE_MANAGER):
    """Одноразовий код на 8 символів. Повертає код або None."""
    if role not in (ROLE_ADMIN, ROLE_MANAGER):
        role = ROLE_MANAGER
    code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    expires = datetime.datetime.now() + datetime.timedelta(days=INVITE_TTL_DAYS)
    try:
        with _conn() as con, con.cursor() as cur:
            cur.execute(
                "INSERT INTO invites (tenant_id, code, role, created_by, expires_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (TENANT_ID, code, role, str(created_by), expires))
        return code
    except Exception as e:
        logger.error("Не вдалося створити інвайт: %s", e)
        return None


def redeem_invite(code, user_id, name, username):
    """Гасить код і видає роль. Повертає (успіх, роль_або_причина).

    Гасіння і перевірка — один запит: WHERE used_by IS NULL гарантує, що
    навіть при двох одночасних спробах код спрацює рівно один раз.
    """
    code = str(code).strip().upper()
    if not code:
        return False, "Код не вказано"
    try:
        with _conn() as con, con.cursor() as cur:
            cur.execute(
                """
                UPDATE invites SET used_by = %s, used_at = now()
                WHERE code = %s AND used_by IS NULL AND expires_at > now()
                RETURNING role
                """, (str(user_id), code))
            row = cur.fetchone()

            if not row:
                # Не спрацювало — зʼясовуємо, чому саме, щоб сказати людині
                # зрозумілу причину замість загального «не вийшло».
                cur.execute(
                    "SELECT used_by, expires_at FROM invites WHERE code = %s", (code,))
                info = cur.fetchone()
    except Exception as e:
        logger.error("Помилка погашення інвайта: %s", e)
        return False, "Сервіс тимчасово недоступний"

    if not row:
        if not info:
            return False, "Код не знайдено"
        if info[0]:
            return False, "Код уже використано"
        return False, f"Код протермінований (діє {INVITE_TTL_DAYS} днів)"

    role = row[0] or ROLE_MANAGER
    if not add_authorized_user(user_id, name, username, role):
        # Код уже погашено, а користувач не додався — це треба бачити в логах.
        logger.error("Інвайт %s погашено, але користувача %s не додано!", code, user_id)
        return False, "Сервіс тимчасово недоступний"
    return True, role
