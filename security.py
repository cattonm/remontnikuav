import json
import os

# --- НАЛАШТУВАННЯ БЕЗПЕКИ ---

# Пароль більше не зберігається в коді! 
# Він надійно прихований у Environment Variables на хостингу.
# Якщо змінну не задано, ставиться неможливий рядок, щоб ніхто не зміг зайти.
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'SECURE_FALLBACK_ERR_999')

# Файл, де зберігаються видані доступи
AUTH_FILE = "auth_db.json"

# Твій особистий ID у Telegram (тільки ти маєш доступ до керування і логів)
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
    """Перевіряє, чи має користувач доступ."""
    # Супер-адмін має доступ завжди
    if user_id == MASTER_ADMIN_ID:
        return True
    
    # Інші перевіряються по базі
    auth_data = load_auth()
    return str(user_id) in auth_data
