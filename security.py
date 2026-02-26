import os
import json
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- НАЛАШТУВАННЯ БЕЗПЕКИ ---
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'SECURE_FALLBACK_ERR_999')
MASTER_ADMIN_ID = 845232133

SPREADSHEET_NAME = "remonts sheets"
GOOGLE_CREDS_JSON = os.getenv('GOOGLE_CREDS_JSON')

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
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        doc = client.open(SPREADSHEET_NAME)
        
        try:
            sheet = doc.worksheet("Admins")
        except gspread.exceptions.WorksheetNotFound:
            sheet = doc.add_worksheet(title="Admins", rows="100", cols="4")
            sheet.append_row(["user_id", "name", "username", "added_date"])
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
                auth_data[clean_id] = {
                    "name": row[1] if len(row) > 1 else "",
                    "username": row[2] if len(row) > 2 else ""
                }
        _auth_cache = auth_data
        return auth_data
    except Exception as e:
        logger.error(f"Помилка читання даних авторизації: {e}")
        # Повертаємо кеш, якщо він є
        if _auth_cache is not None:
            return _auth_cache
        return {}

def add_authorized_user(user_id, name, username):
    """
    Додає користувача до списку авторизованих.
    Оновлює кеш і намагається записати в Google Sheets.
    """
    global _auth_cache
    # Спочатку оновлюємо кеш
    clean_id = str(user_id).strip()
    if _auth_cache is None:
        _auth_cache = {}
    _auth_cache[clean_id] = {"name": name, "username": username}

    # Тепер намагаємося записати в таблицю
    sheet = get_auth_sheet()
    if not sheet:
        logger.warning("Не вдалося записати в Google Sheets, але кеш оновлено")
        return True

    try:
        import datetime
        date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        sheet.append_row([clean_id, name, username, date_str])
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

def is_authorized(user_id):
    """
    Перевіряє, чи є користувач авторизованим.
    """
    data = get_all_authorized_users()
    return str(user_id).strip() in data

def clear_auth_cache():
    """
    Скидає кеш, змушуючи бота перечитати Google Таблицю.
    """
    global _auth_cache
    _auth_cache = None
    logger.info("Кеш авторизації очищено")
    return True
