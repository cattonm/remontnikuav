"""Ядро застосунку: єдині екземпляри бота, диспетчера та моделі Gemini.

НАВІЩО ЦЕЙ МОДУЛЬ. Хендлери роз'їхались по файлах (api_*.py, bot_handlers.py),
але всім їм потрібен один і той самий `bot`, щоб слати повідомлення. Якщо кожен
модуль створить свій Bot(token=...) — це різні HTTP-сесії, зайві з'єднання і
плаваючі помилки. Тому екземпляри живуть ТУТ, а всі інші модулі їх імпортують.

Правило залежностей, щоб не було циклічних імпортів:
    config / lexicon      → нічого не імпортують із застосунку
    core                  → лише config + security
    webauth, http_utils   → лише config + security + core
    api_*.py              → core, webauth, http_utils, storage, calculator
    bot_handlers          → core, api_login (для прив'язки коду входу)
    main                  → усе перелічене, і більше нічого не робить сам
Модуль ніколи не імпортує той, що стоїть нижче в цьому списку.
"""
import html
import logging
import sys
import time

import google.generativeai as genai
from aiogram import Bot, Dispatcher

from config import BOT_TOKEN, GEMINI_API_KEY
from security import MASTER_ADMIN_ID
from storage import STORAGE_BACKEND, PRICES_BACKEND

logging.basicConfig(level=logging.INFO, stream=sys.stdout)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Gemini може бути не налаштований (немає ключа) — це не привід не стартувати.
# Ендпоінт генерації ТЗ у такому разі віддає зрозумілий 503.
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash-lite')
else:
    model = None
    logging.warning("GEMINI_API_KEY не заданий — генерація ТЗ недоступна.")

STARTED_AT = time.time()

# М'які блокування заявок: поки один менеджер редагує, інший бачить попередження.
# {row_id: {"user_id", "user_name", "expires"}}
LOCKS = {}

# Заповнюється на старті (check_schema). Порожній список = схема актуальна.
# Список, а не рядок, бо його читає /version уже після зміни.
PENDING_MIGRATIONS = []


def check_schema():
    """Гучно попереджає, якщо база відстає від коду.

    Не блокує запуск: сервіс має піднятись і віддавати те, що вміє, навіть
    коли одна фіча зламана. Але мовчати про це не можна — саме так
    втрачається година на пошук причини 500-ї помилки.
    """
    if STORAGE_BACKEND != "postgres" and PRICES_BACKEND != "postgres":
        return
    try:
        from migrate import pending_versions
        PENDING_MIGRATIONS[:] = pending_versions()
    except Exception as e:
        logging.warning("Не вдалося перевірити стан схеми БД: %s", e)
        return
    if PENDING_MIGRATIONS:
        logging.error(
            "БАЗА ВІДСТАЄ ВІД КОДУ: не накочено %d міграцій (%s). "
            "Частина функцій віддаватиме помилки, поки не виконаєш: python migrate.py up",
            len(PENDING_MIGRATIONS), ", ".join(PENDING_MIGRATIONS))
    else:
        logging.info("Схема БД актуальна")


async def notify_admin_about_error(context_msg, error_details):
    """Системна помилка → у Telegram майстер-адміну. Мовчазних падінь бути не повинно."""
    try:
        text = (f"🚨 <b>СИСТЕМНА ПОМИЛКА БОТА</b>\n\n"
                f"<b>Процес:</b> {html.escape(str(context_msg))}\n"
                f"<b>Деталі:</b> <code>{html.escape(str(error_details))}</code>")
        await bot.send_message(chat_id=MASTER_ADMIN_ID, text=text, parse_mode="HTML")
    except Exception as e:
        logging.error("Failed to notify admin: %s", e)
