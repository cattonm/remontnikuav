import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- НАЛАШТУВАННЯ БЕЗПЕКИ ---
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'SECURE_FALLBACK_ERR_999')
MASTER_ADMIN_ID = 845232133

SPREADSHEET_NAME = "remonts sheets"
GOOGLE_CREDS_JSON = os.getenv('GOOGLE_CREDS_JSON')

# Кеш у пам'яті, щоб не смикати Google Sheets при кожному повідомленні
_auth_cache = None

def get_auth_sheet():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        doc = client.open(SPREADSHEET_NAME)
        
        # Шукаємо аркуш Admins. Якщо немає - створюємо.
        try:
            sheet = doc.worksheet("Admins")
        except gspread.exceptions.WorksheetNotFound:
            sheet = doc.add_worksheet(title="Admins", rows="100", cols="4")
            sheet.append_row(["user_id", "name", "username", "added_date"])
        return sheet
    except Exception as e:
        print(f"Помилка Google Sheets (Auth): {e}")
        return None

def get_all_authorized_users():
    """Завантажує список авторизованих користувачів із Google Sheets."""
    global _auth_cache
    if _auth_cache is not None:
        return _auth_cache

    sheet = get_auth_sheet()
    if not sheet: return {}

    records = sheet.get_all_records()
    auth_data = {}
    for row in records:
        auth_data[str(row.get("user_id"))] = {
            "name": row.get("name"),
            "username": row.get("username")
        }
    _auth_cache = auth_data
    return auth_data

def add_authorized_user(user_id, name, username):
    """Додає нового користувача в Google Sheets."""
    global _auth_cache
    sheet = get_auth_sheet()
    if not sheet: return False

    import datetime
    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    sheet.append_row([str(user_id), name, username, date_str])

    # Оновлюємо кеш
    if _auth_cache is None:
        _auth_cache = {}
    _auth_cache[str(user_id)] = {"name": name, "username": username}
    return True

def remove_authorized_user(user_id):
    """Видаляє користувача з Google Sheets."""
    global _auth_cache
    sheet = get_auth_sheet()
    if not sheet: return False

    try:
        cell = sheet.find(str(user_id))
        sheet.delete_rows(cell.row)
    except gspread.exceptions.CellNotFound:
        pass

    # Оновлюємо кеш
    if _auth_cache and str(user_id) in _auth_cache:
        del _auth_cache[str(user_id)]
    return True

def is_authorized(user_id):
    """Перевіряє доступ."""
    data = get_all_authorized_users()
    return str(user_id) in data
