import json
import os

# --- НАЛАШТУВАННЯ БЕЗПЕКИ ---
AUTH_FILE = "auth_db.json"
ADMIN_PASSWORD = "IlOvErEmOnTUA26#A"

# Твій особистий ID у Telegram (тільки він має доступ до супер-команд та отримує логи)
MASTER_ADMIN_ID = 845232133

def load_auth():
    """Завантажує список авторизованих користувачів із файлу."""
    if os.path.exists(AUTH_FILE):
        with open(AUTH_FILE, "r", encoding="utf-8") as f:
            try: 
                return json.load(f)
            except: 
                return {}
    return {}

def save_auth(data):
    """Зберігає список авторизованих користувачів у файл."""
    with open(AUTH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def is_authorized(user_id):
    """Перевіряє, чи має користувач доступ (чи є він у базі, або чи він Супер Адмін)."""
    if user_id == MASTER_ADMIN_ID:
        return True
    auth_data = load_auth()
    return str(user_id) in auth_data
