"""Серверні чернетки анкети + нагадування через 24 години.

Чернетка живе не лише в localStorage телефона: менеджер може продовжити
з іншого пристрою, а недороблена заявка не губиться — бот нагадає.
"""
import asyncio
import html
import logging

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiohttp import web

from calculator import calculate_budget, apply_virtual_measurements
from config import WEBAPP_URL
from core import bot
from http_utils import cors
from storage import (_save_draft_sync, _get_draft_sync, _delete_draft_sync,
                     _scan_drafts_for_reminders_sync, _mark_reminded_sync,
                     async_get_prices, get_price_labels)
from webauth import auth_request


@cors
async def api_save_draft(request):
    user_id, _role = auth_request(request)   # працює і в Telegram, і на сайті
    if not user_id:
        return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        data = await request.json()
        ok = await asyncio.to_thread(_save_draft_sync, user_id, data)
        return web.json_response({"success": ok})
    except Exception:
        logging.exception("save_draft failed")
        return web.json_response({"error": "save_failed"}, status=500)


@cors
async def api_get_draft(request):
    user_id, _role = auth_request(request)
    if not user_id:
        return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        draft = await asyncio.to_thread(_get_draft_sync, user_id)
        return web.json_response({"draft": draft})
    except Exception:
        logging.exception("get_draft failed")
        return web.json_response({"draft": None})


@cors
async def api_delete_draft(request):
    user_id, _role = auth_request(request)
    if not user_id:
        return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        await asyncio.to_thread(_delete_draft_sync, user_id)
        return web.json_response({"success": True})
    except Exception:
        logging.exception("delete_draft failed")
        return web.json_response({"error": "delete_failed"}, status=500)


async def remind_about_drafts_periodically():
    """Раз на годину: незавершені чернетки старші за 24 год → бот пише
    менеджеру з ЖИВОЮ сумою кошторису і кнопкою «Продовжити»."""
    await asyncio.sleep(120)   # даємо сервісу піднятись
    while True:
        try:
            due = await asyncio.to_thread(_scan_drafts_for_reminders_sync)
            if due:
                prices = await async_get_prices()
            for row, uid, payload in due:
                try:
                    b = calculate_budget(apply_virtual_measurements(payload), prices,
                                         labels=get_price_labels())
                    total = round(b["total_work"] + b["total_mat_min"])
                    name = (payload.get("client") or {}).get("name") or "без назви"
                    rooms_n = len((payload.get("answers") or {}).get("rooms") or [])
                    kb = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="✏️ Продовжити заявку",
                                             web_app=WebAppInfo(url=WEBAPP_URL))
                    ]]) if WEBAPP_URL else None
                    await bot.send_message(
                        chat_id=int(uid),
                        text=(f"⏰ *Незавершена заявка*\n\nКлієнт: *{html.escape(str(name))}*\n"
                              f"Приміщень: {rooms_n}\nОрієнтовний кошторис: *{total:,} ₴*\n\n"
                              f"Чернетка чекає — завершіть, поки клієнт не охолов.").replace(",", " "),
                        parse_mode="Markdown", reply_markup=kb)
                    await asyncio.to_thread(_mark_reminded_sync, row)
                    await asyncio.sleep(0.5)     # не впираємось у ліміти Telegram
                except Exception:
                    logging.exception("Не вдалося нагадати про чернетку (user %s)", uid)
        except Exception:
            logging.exception("Цикл нагадувань про чернетки впав — повторю за годину")
        await asyncio.sleep(3600)
