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
        
        try:
            sheet = doc.worksheet("Admins")
        except gspread.exceptions.WorksheetNotFound:
            sheet = doc.add_worksheet(title="Admins", rows="100", cols="4")
            sheet.append_row(["user_id", "name", "username", "added_date"])
        return sheet
    except Exception as e:
        print(f"Помилка Google Sheets (Auth): {e}")
        return None

def get_all_authorized_users(force_refresh=False):
    global _auth_cache
    if _auth_cache is not None and not force_refresh:
        return _auth_cache

    sheet = get_auth_sheet()
    if not sheet: return {}

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
        print(f"Помилка читання даних авторизації: {e}")
        return {}

def add_authorized_user(user_id, name, username):
    global _auth_cache
    get_all_authorized_users()
    
    sheet = get_auth_sheet()
    if not sheet: return False

    import datetime
    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    clean_id = str(user_id).strip()
    sheet.append_row([clean_id, name, username, date_str])

    if _auth_cache is None:
        _auth_cache = {}
    _auth_cache[clean_id] = {"name": name, "username": username}
    return True

def remove_authorized_user(user_id):
    global _auth_cache
    sheet = get_auth_sheet()
    if not sheet: return False

    clean_id = str(user_id).strip()
    try:
        cell = sheet.find(clean_id)
        sheet.delete_rows(cell.row)
    except gspread.exceptions.CellNotFound:
        pass

    if _auth_cache and clean_id in _auth_cache:
        del _auth_cache[clean_id]
    return True

def is_authorized(user_id):
    data = get_all_authorized_users()
    return str(user_id).strip() in data

# НОВА ФУНКЦІЯ: Очищення кешу
def clear_auth_cache():
    """Скидає кеш, змушуючи бота перечитати Google Таблицю."""
    global _auth_cache
    _auth_cache = None
    return True
