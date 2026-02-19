import json
import os

# --- НАЛАШТУВАННЯ БЕЗПЕКИ ---
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'SECURE_FALLBACK_ERR_999')
AUTH_FILE = "auth_db.json"

# Твій особистий ID у Telegram
MASTER_ADMIN_ID = 845232133

def load_auth():
    if os.path.exists(AUTH_FILE):
        with open(AUTH_FILE, "r", encoding="utf-8") as f:
            try: return json.load(f)
            except: return {}
    return {}

def save_auth(data):
    with open(AUTH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def is_authorized(user_id):
    """Перевіряє, чи є користувач у базі (без винятків)."""
    auth_data = load_auth()
    return str(user_id) in auth_data
