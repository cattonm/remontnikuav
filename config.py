"""Конфігурація застосунку: змінні оточення, константи, дефолтний прайс.

Виділено з main.py (Етап 2 рефакторингу). Модуль НЕ імпортує aiogram/gspread —
його може безпечно підтягувати будь-який інший модуль без циклічних залежностей.
"""
import os
import logging

BOT_TOKEN = os.getenv('BOT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GOOGLE_CREDS_JSON = os.getenv('GOOGLE_CREDS_JSON') 
SPREADSHEET_NAME = "remonts sheets" 

GROUP_CHAT_ID = "-5265068775" # Замінити на свій ID групи

WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = 10000
WEBHOOK_URL = os.getenv('RENDER_EXTERNAL_URL')
WEBHOOK_PATH = "/webhook"
WEBAPP_URL = "https://siteremontt.vercel.app"

# Ціни рівня «Комфорт» для позицій, де середнє між стандартом і преміумом
# дає неправильне число. Ванна: стандарт 15 000, преміум 100 000, середнє
# 57 500 — а реальний комфорт 40 000.
#
# Це ДЕФОЛТИ для першого заповнення бази. Коли позиція має значення в
# таблиці prices (колонка mat_mid), береться воно — його редагують у
# кабінеті. Раніше ці числа жили просто в calculator.py, тож змінити їх
# можна було лише правкою коду і деплоєм.
DEFAULT_COMFORT_PRICES = {
    "radiator": 6000, "ac": 27000, "bath_tub": 40000,
    "toilet_okrem": 10000, "toilet_install": 22000,
    "sink_cabinet": 20000, "boiler_100": 13800, "boiler_300": 13800,
    "towel_dryer": 7500, "hygienic_shower": 6000, "mirror_led": 5500,
    "mixer_std": 6000, "mixer_hidden": 10000, "tech_washer": 25000,
    "tech_kitchen": 18000, "tech_osmos": 15000,
    "door_entrance_mdf": 30000, "door_entrance_armor": 30000,
}

# --- Секрети: жодних публічних дефолтів у проді ---
# Раніше WEBHOOK_SECRET мав дефолт 'DefaultSecretToken12345'. Якщо env не
# заданий, вебхук був підписаний загальновідомим рядком — будь-хто міг
# слати боту фейкові апдейти. Тепер відсутність секрету = падіння на старті.
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET')

# Сесії веб-кабінету підписуються ОКРЕМИМ секретом, а не BOT_TOKEN.
# Витік токена бота більше не дозволяє підробити чужу сесію.
# Фолбек на BOT_TOKEN лишений лише щоб уже видані сесії не протухли миттєво
# після деплою; після заведення SESSION_SECRET в env — прибрати фолбек.
SESSION_SECRET = os.getenv('SESSION_SECRET')
if not SESSION_SECRET:
    # Мовчазний фолбек — найгірший вид технічного боргу: він працює, тому про
    # нього забувають. Тепер він видно в логах при кожному старті.
    logging.warning(
        "SESSION_SECRET не заданий — сесії підписуються BOT_TOKEN. "
        "Витік токена бота в такому разі дозволяє підробити чужу сесію. "
        "Заведи SESSION_SECRET в оточенні: будь-який довгий випадковий рядок."
    )
    SESSION_SECRET = BOT_TOKEN

# Домени, яким дозволено ходити в API з браузера (CORS).
# '*' відкривав ендпоінти лідів/чернеток будь-якому сайту.
_ALLOWED_ORIGINS = {
    "https://siteremontt.vercel.app",
    "https://web.telegram.org",
}
# Локальна розробка: дозволяємо localhost лише коли не в проді.
if not WEBHOOK_URL:
    _ALLOWED_ORIGINS |= {"http://localhost:5173", "http://127.0.0.1:5173"}


def _require_env():
    """Валідація критичних env на старті. Краще впасти голосно тут,
    ніж тихо працювати з дірявою безпекою в проді."""
    missing = [name for name in ("BOT_TOKEN",) if not os.getenv(name)]
    # WEBHOOK_SECRET обов'язковий лише в проді (коли є RENDER_EXTERNAL_URL).
    if WEBHOOK_URL and not WEBHOOK_SECRET:
        missing.append("WEBHOOK_SECRET")
    if missing:
        raise RuntimeError(
            "Не задані обов'язкові змінні оточення: " + ", ".join(missing)
        )


# Дефолтний прайс — використовується, якщо Google-таблиця недоступна.
DEFAULT_PRICES = {
    "logistics_base": [150, 0, 0], "logistics_stair": [30, 0, 0], "logistics_elev": [10, 0, 0],
    "screed_wet": [1100, 700, 700], "screed_dry": [500, 500, 500], "plumbing": [1100, 300, 300],
    "rough_plaster": [805, 340, 400], "electric_wire": [2100, 1000, 2000], "electric_point": [180, 100, 200],
    "warm_floor_elec": [550, 400, 500], 
    "door_entrance_mdf": [4700, 15000, 50000], "door_entrance_armor": [5500, 15000, 50000], # Двері: 15к-50к
    "door_hidden": [30000, 15000, 27000], "door_std": [3650, 8000, 15000],
    "tile_floor_mosaic": [2600, 1500, 2500], "tile_floor_std": [1900, 1500, 2500], "tile_floor_large": [3100, 1500, 2500],
    "tile_wall_mosaic": [2800, 1500, 2500], "tile_wall_std": [2100, 1500, 2500], "tile_wall_large": [3300, 1500, 2500],
    "toilet_okrem": [2000, 5000, 20000], "toilet_install": [4900, 12000, 30000], # Унітази: 5к-20к / 12к-30к
    "bath_tub": [3800, 15000, 100000], # Ванна: 15к-100к
    "room_lam": [405, 600, 900], "room_quartz": [565, 1200, 1800], "room_parket": [850, 2500, 5000], "linoleum": [150, 300, 600],
    "wall_paper": [1000, 200, 400], "wall_paint": [1865, 250, 450], "wall_decor": [2210, 500, 1500], "whitewash": [100, 50, 100], "wood_rails": [800, 1500, 3500],
    "wall_primer": [55, 0, 0], "wall_vagonka": [1200, 1500, 1500], "wall_koroid": [600, 250, 250],
    "base_std": [215, 115, 200], "base_shadow": [1435, 400, 800], "base_hidden": [1600, 600, 600],
    "ceil_stretch": [400, 390, 390], "ceil_gips": [2500, 650, 650], 
    "ceil_shadow_add": [350, 150, 300], "wall_decor_panels": [5000, 8000, 15000], 
    "kitchen_workspace_led": [1000, 2000, 2000], "balcony_workspace": [1500, 3500, 3500],
    "radiator": [3400, 3000, 12000], "ac": [13000, 15000, 45000], # Радіатор: 3к-12к / Кондиціонер: 15к-45к
    "soundproof": [830, 1000, 2500], "curtains": [500, 3000, 10000],
    "boiler_100": [2800, 8000, 25000], "boiler_300": [5000, 8000, 25000], # Бойлер: 8к-25к
    "towel_dryer": [1200, 3500, 15000], "hygienic_shower": [1900, 3000, 12000], # Рушникосушка: 3.5к-15к / Гіг.душ: 3к-12к
    "mirror_led": [600, 1500, 12000], # Дзеркало: 1.5к-12к (робота змінюється в calculator.py)
    "tech_washer": [1050, 15000, 40000], "tech_kitchen": [1050, 10000, 30000], "tech_osmos": [2000, 8000, 25000], # Техніка
    "sink_cabinet": [1600, 10000, 40000], # Умивальник: 10к-40к
    "mixer_std": [1000, 2000, 15000], "mixer_hidden": [1900, 5000, 25000], # Змішувачі: 2к-15к / 5к-25к
    "sill_plastic": [800, 1500, 1500], "sill_wood": [1500, 3000, 3000], "sill_stone": [2000, 4000, 8000],
    "balcony_warm": [600, 600, 800], "kitchen_apron": [4000, 3000, 8000],
    "balcony_glazing_outer": [1000, 4800, 9000], "balcony_glazing_block": [1500, 4800, 9000],
    "light_point": [250, 300, 800], "light_chandelier": [750, 3500, 3500], "light_track": [780, 1450, 3600], "light_led": [390, 0, 0],
    "shower_tray": [3000, 8000, 20000], "shower_trap": [10000, 3000, 5000], "shower_glass": [3500, 8000, 15000], "shower_doors": [3500, 12000, 20000],
    "demo_door_ent": [1200, 0, 0], "demo_door_int": [500, 0, 0], "demo_walls": [400, 0, 0], 
    "build_gkl": [1100, 600, 600], "build_brick": [1100, 1000, 1000], "build_gazoblok": [850, 600, 600],
    "demo_floor_wood": [250, 0, 0], "demo_floor_lin": [120, 0, 0], "demo_screed": [320, 0, 0]
}


# --- Схема Google-таблиці (спільна для main і storage_sheets) ---
DEAL_STATUSES = {
    "new":  "🆕 Нова",
    "sent": "📤 КП відправлено",
    "won":  "✅ Виграна",
    "lost": "❌ Програна",
}

SHEET_HEADER = ["Дата", "Ім'я", "Телефон", "Тип об'єкта", "Адреса", "Анкета (JSON)",
                "Звіт", "Статус", "Менеджер", "Джерело", "Угода"]

PRICES_SHEET_NAME = "Ціни"

_PRICES_HEADER = ["key", "Назва", "Робота (грн)", "Матеріал мін (грн)", "Матеріал макс (грн)"]
