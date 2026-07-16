import os
import json
import logging
import secrets
import datetime
import gspread
from google.oauth2.service_account import Credentials

# --- НАЛАШТУВАННЯ БЕЗПЕКИ ---
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'SECURE_FALLBACK_ERR_999')
MASTER_ADMIN_ID = 845232133

SPREADSHEET_NAME = "remonts sheets"
GOOGLE_CREDS_JSON = os.getenv('GOOGLE_CREDS_JSON')

# --- РОЛІ ---
# admin   — власник: бачить УСІ заявки, керує менеджерами, роздає інвайти.
# manager — бачить свої заявки + вільні (гостьові) ліди.
# Гість (без запису в таблиці) — доступу до кабінету не має взагалі,
#          але може користуватись публічним калькулятором на сайті.
ROLE_ADMIN = "admin"
ROLE_MANAGER = "manager"

# Кеш у пам'яті, щоб не смикати Google Sheets при кожному повідомленні
_auth_cache = None

# Налаштування логування
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_auth_sheet():
    """
    Отримує доступ до аркуша "Admins" у Google Sheets.
    Повертає об'єкт worksheet або None у разі помилки.
    """
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        client = gspread.authorize(creds)
        doc = client.open(SPREADSHEET_NAME)
        
        try:
            sheet = doc.worksheet("Admins")
            # Міграція старої таблиці: додаємо колонку "role", якщо її ще нема.
            # Усі, хто вже мав доступ, лишаються менеджерами (крім майстер-адміна).
            header = sheet.row_values(1)
            if len(header) < 5 or (len(header) > 4 and header[4].strip().lower() != "role"):
                sheet.update_cell(1, 5, "role")
        except gspread.exceptions.WorksheetNotFound:
            sheet = doc.add_worksheet(title="Admins", rows="100", cols="5")
            sheet.append_row(["user_id", "name", "username", "added_date", "role"])
        return sheet
    except Exception as e:
        logger.error(f"Помилка Google Sheets (Auth): {e}")
        return None

def get_all_authorized_users(force_refresh=False):
    """
    Повертає словник авторизованих користувачів.
    Якщо не вдалося отримати дані з таблиці, повертає кеш (якщо він є),
    інакше порожній словник.
    """
    global _auth_cache
    if _auth_cache is not None and not force_refresh:
        return _auth_cache

    sheet = get_auth_sheet()
    if not sheet:
        # Якщо таблиця недоступна, повертаємо попередній кеш (якщо є)
        if _auth_cache is not None:
            logger.warning("Google Sheets недоступний, використовується кеш авторизації")
            return _auth_cache
        else:
            logger.error("Google Sheets недоступний, кеш порожній")
            return {}

    try:
        records = sheet.get_all_values()
        auth_data = {}
        for row in records[1:]:
            if len(row) > 0 and row[0]:
                clean_id = str(row[0]).strip()
                role = (row[4].strip().lower() if len(row) > 4 and row[4] else "") or ROLE_MANAGER
                if role not in (ROLE_ADMIN, ROLE_MANAGER):
                    role = ROLE_MANAGER
                auth_data[clean_id] = {
                    "name": row[1] if len(row) > 1 else "",
                    "username": row[2] if len(row) > 2 else "",
                    "role": role,
                }
        _auth_cache = auth_data
        return auth_data
    except Exception as e:
        logger.error(f"Помилка читання даних авторизації: {e}")
        # Повертаємо кеш, якщо він є
        if _auth_cache is not None:
            return _auth_cache
        return {}

def add_authorized_user(user_id, name, username, role=ROLE_MANAGER):
    """
    Додає користувача до списку авторизованих із вказаною роллю.
    Оновлює кеш і намагається записати в Google Sheets.
    """
    global _auth_cache
    clean_id = str(user_id).strip()
    if role not in (ROLE_ADMIN, ROLE_MANAGER):
        role = ROLE_MANAGER
    if _auth_cache is None:
        _auth_cache = {}
    _auth_cache[clean_id] = {"name": name, "username": username, "role": role}

    # Тепер намагаємося записати в таблицю
    sheet = get_auth_sheet()
    if not sheet:
        logger.warning("Не вдалося записати в Google Sheets, але кеш оновлено")
        return True

    try:
        date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        sheet.append_row([clean_id, name, username, date_str, role])
        return True
    except Exception as e:
        logger.error(f"Помилка додавання користувача в Google Sheets: {e}")
        # Кеш вже оновлено, тому повертаємо True
        return True

def remove_authorized_user(user_id):
    """
    Видаляє користувача зі списку авторизованих.
    Оновлює кеш і намагається видалити з Google Sheets.
    """
    global _auth_cache
    clean_id = str(user_id).strip()

    # Видаляємо з кешу
    if _auth_cache and clean_id in _auth_cache:
        del _auth_cache[clean_id]

    # Намагаємося видалити з таблиці
    sheet = get_auth_sheet()
    if not sheet:
        logger.warning("Не вдалося видалити з Google Sheets, але кеш оновлено")
        return True

    try:
        cell = sheet.find(clean_id)
        sheet.delete_rows(cell.row)
        return True
    except gspread.exceptions.CellNotFound:
        # Користувача немає в таблиці – це нормально
        return True
    except Exception as e:
        logger.error(f"Помилка видалення користувача з Google Sheets: {e}")
        # Кеш вже оновлено
        return True

def get_role(user_id):
    """Роль користувача: 'admin' | 'manager' | None (немає доступу)."""
    clean_id = str(user_id).strip()
    if clean_id == str(MASTER_ADMIN_ID):
        return ROLE_ADMIN          # майстер-адмін — завжди адмін, навіть якщо таблиця лягла
    info = get_all_authorized_users().get(clean_id)
    return info.get("role") if info else None

def is_admin(user_id):
    return get_role(user_id) == ROLE_ADMIN

def is_authorized(user_id):
    """
    Перевіряє, чи є користувач авторизованим.
    """
    if str(user_id).strip() == str(MASTER_ADMIN_ID):
        return True
    data = get_all_authorized_users()
    return str(user_id).strip() in data


# ==========================================================
# ІНВАЙТ-КОДИ (заміна спільного ADMIN_PASSWORD)
# ----------------------------------------------------------
# Було: один пароль на всіх. Хто його дізнався — отримував повний доступ
# назавжди, і відкликати можна було лише вручну. Тепер адмін генерує
# ОДНОРАЗОВИЙ код на конкретну людину: код гаситься при першому
# використанні і протухає за 7 днів. Паролі ніде не зберігаються.
# Аркуш "Invites": code | role | created_by | created_at | used_by | used_at
# ==========================================================
INVITE_TTL_DAYS = 7

def _invites_sheet():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), scopes=scope)
        doc = gspread.authorize(creds).open(SPREADSHEET_NAME)
        try:
            return doc.worksheet("Invites")
        except gspread.exceptions.WorksheetNotFound:
            ws = doc.add_worksheet(title="Invites", rows="200", cols="6")
            ws.append_row(["code", "role", "created_by", "created_at", "used_by", "used_at"])
            return ws
    except Exception as e:
        logger.error(f"Помилка Google Sheets (Invites): {e}")
        return None

def create_invite(created_by, role=ROLE_MANAGER):
    """Створює одноразовий код. Повертає рядок коду або None."""
    ws = _invites_sheet()
    if not ws:
        return None
    # 8 символів, без плутанини 0/O та 1/I — код диктують голосом і в чаті
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    code = "".join(secrets.choice(alphabet) for _ in range(8))
    try:
        ws.append_row([code, role, str(created_by),
                       datetime.datetime.now().isoformat(timespec="seconds"), "", ""])
        return code
    except Exception as e:
        logger.error(f"Не вдалося створити інвайт: {e}")
        return None

def redeem_invite(code, user_id, name, username):
    """Гасить код і видає роль. Повертає (успіх, роль_або_причина)."""
    ws = _invites_sheet()
    if not ws:
        return False, "Сервіс тимчасово недоступний"
    code = str(code).strip().upper()
    try:
        cell = ws.find(code, in_column=1)
    except Exception:
        cell = None
    if not cell:
        return False, "Код не знайдено"

    row = ws.row_values(cell.row)
    role = (row[1].strip().lower() if len(row) > 1 and row[1] else ROLE_MANAGER)
    used_by = row[4] if len(row) > 4 else ""
    if used_by:
        return False, "Код уже використано"

    try:
        created = datetime.datetime.fromisoformat(row[3])
        if (datetime.datetime.now() - created).days > INVITE_TTL_DAYS:
            return False, f"Код протермінований (діє {INVITE_TTL_DAYS} днів)"
    except (IndexError, ValueError):
        pass  # немає дати — не блокуємо

    add_authorized_user(user_id, name, username, role)
    try:
        ws.update([[str(user_id), datetime.datetime.now().isoformat(timespec="seconds")]],
                  f"E{cell.row}:F{cell.row}")
    except Exception as e:
        logger.error(f"Не вдалося позначити інвайт використаним: {e}")
    return True, role

def clear_auth_cache():
    """
    Скидає кеш, змушуючи бота перечитати Google Таблицю.
    """
    global _auth_cache
    _auth_cache = None
    logger.info("Кеш авторизації очищено")
    return True
