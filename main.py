"""Точка входу: складання застосунку і запуск.

Файл свідомо тонкий. Уся логіка живе в модулях:
    core.py          — bot / dp / Gemini / блокування / перевірка схеми
    http_utils.py    — CORS і ліміти частоти
    webauth.py       — підписи Telegram і сесії кабінету
    sanitize.py      — очищення HTML від мовної моделі
    api_orders.py    — заявки, кошик, PDF, ТЗ
    api_admin.py     — доступи, інвайти, прайс, статистика
    api_public.py    — калькулятор, лід із сайту, /version, /ping
    api_drafts.py    — чернетки й нагадування
    api_login.py     — вхід у кабінет
    bot_handlers.py  — хендлери Telegram
    routes.py        — таблиця маршрутів

Якщо тут з'являється бізнес-логіка — вона потрапила не в той файл.
"""
import asyncio
import logging

from aiogram import Bot
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from config import (WEB_SERVER_HOST, WEB_SERVER_PORT, WEBHOOK_URL,
                    WEBHOOK_PATH, WEBHOOK_SECRET, _require_env)
from core import bot, dp, check_schema
from storage import STORAGE_BACKEND, async_ensure_header, async_get_prices, _get_google_sheet
from api_drafts import remind_about_drafts_periodically
from api_orders import clean_locks_periodically
from routes import setup_routes

# Хендлери бота реєструються самим фактом імпорту (декоратори @dp.message тощо),
# тому модуль імпортується заради побічного ефекту.
import bot_handlers

# art_curator — ОПЦІЙНИЙ модуль: файла немає в репозиторії (лише локально).
# Раніше жорсткий import валив старт на Render: ModuleNotFoundError →
# деплой failed → Render мовчки лишав живою СТАРУ версію бекенду.
try:
    from art_curator import art_router
except ImportError:
    art_router = None
    logging.warning("art_curator.py не знайдено — стартуємо без цього роутера.")


async def on_startup(bot: Bot):
    # ⚠️ ІМ'Я ПАРАМЕТРА ЗНАЧУЩЕ. aiogram підставляє залежності ПО ІМЕНІ:
    # у startup-хендлер він передає kwargs {bot, dispatcher, ...} і фільтрує
    # їх за сигнатурою. Параметр, названий інакше (напр. _bot), не отримає
    # нічого — і виклик впаде з "missing 1 required positional argument"
    # уже в проді, бо на імпорті це не видно. Перевіряє tests/test_routes.py.
    # Google чіпаємо, ЛИШЕ якщо заявки справді лежать в аркушах. У
    # postgres-режимі цей виклик просто ліз до мертвого ключа при кожному
    # старті й засмічував логи помилкою авторизації.
    if STORAGE_BACKEND == "sheets":
        _get_google_sheet()
        # Самолікування таблиці: якщо в рядку 1 опинилась заявка замість шапки —
        # повертаємо шапку на місце (заявка з'їжджає на рядок 2 і стає видимою).
        asyncio.create_task(async_ensure_header())
    try:
        await bot.set_webhook(f"{WEBHOOK_URL}{WEBHOOK_PATH}", secret_token=WEBHOOK_SECRET)
    except Exception:
        logging.exception("Не вдалося встановити вебхук")
    asyncio.create_task(clean_locks_periodically())
    asyncio.create_task(async_get_prices())                  # прогріваємо прайс
    asyncio.create_task(remind_about_drafts_periodically())  # нагадування раз на годину


async def on_shutdown(bot: Bot):
    await bot.session.close()


def create_app():
    """Збирає aiohttp-застосунок. Винесено окремо, щоб тести могли підняти
    сервер без web.run_app і без вебхука."""
    app = web.Application()
    n = setup_routes(app)
    logging.info("Зареєстровано маршрутів: %d", n)
    return app


def main():
    _require_env()   # падаємо голосно, якщо критичних env немає
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    if art_router is not None:
        dp.include_router(art_router)
    check_schema()
    logging.info("Хендлери бота завантажено (%s)", bot_handlers.__name__)

    app = create_app()
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=WEBHOOK_SECRET).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    web.run_app(app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)


if __name__ == "__main__":
    main()