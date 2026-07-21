"""Публічна частина: калькулятор без авторизації, заявка від гостя, діагностика.

Гість рахує кошторис сам на сайті й лишає контакт. Лід падає в ту саму
таблицю з source="web" і БЕЗ manager_id — тобто у «вільний пул»: його
бачать усі менеджери.

Захист: rate-limit по IP + honeypot-поле. CAPTCHA свідомо не ставимо —
вона вбиває конверсію, а ставки тут невисокі.
"""
import asyncio
import html
import logging
import os
from datetime import datetime
import time

from aiohttp import web

from calculator import calculate_budget, apply_virtual_measurements
from core import STARTED_AT, PENDING_MIGRATIONS, bot, notify_admin_about_error
from http_utils import (cors, client_ip, rate_limited, too_many,
                        LEAD_MAX_PER_HOUR, CALC_MAX_PER_MINUTE)
from security import AUTH_BACKEND, get_all_authorized_users
from storage import (STORAGE_BACKEND, PRICES_BACKEND, _PRICES_META,
                     async_save_to_sheet, async_get_prices, get_price_labels,
                     _fetch_orders_rows_sync)
from webauth import auth_request


@cors
async def api_me(request):
    """Хто я: роль визначає, що покаже фронтенд.
    Працює для обох входів (міні-апка або веб-сесія). Без доступу — гість:
    вільний калькулятор + форма контакту в кінці."""
    uid, role = auth_request(request)
    if not uid:
        return web.json_response({"role": "guest"})
    info = get_all_authorized_users().get(str(uid), {})
    return web.json_response({"role": role, "user_id": uid, "name": info.get("name", "")})


@cors
async def api_submit_lead(request):
    try:
        data = await request.json()
        # Honeypot: приховане поле, яке заповнюють лише боти
        if data.get("website"):
            return web.json_response({"success": True})   # вдаємо успіх, мовчки ігноруємо

        if rate_limited(client_ip(request), "lead", LEAD_MAX_PER_HOUR):
            return too_many()

        c = data.get("client") or {}
        phone = str(c.get("phone") or "").strip()
        digits = "".join(ch for ch in phone if ch.isdigit())
        if len(digits) < 9:
            return web.json_response({"error": "bad_phone"}, status=400)
        if not str(c.get("name") or "").strip():
            return web.json_response({"error": "no_name"}, status=400)

        answers = data.get("answers") or {}
        # НЕ ДОВІРЯЄМО ЦІНАМ ВІД ГОСТЯ. У фронтенді розділ «Нестандартні роботи»
        # гостю не показується, але POST можна надіслати й напряму — тож
        # вирізаємо його тут: інакше будь-хто міг би підкинути в кошторис
        # довільні суми (хоч мільйон, хоч нуль).
        if isinstance(answers, dict) and answers.get("custom_works"):
            answers = {k: v for k, v in answers.items() if k != "custom_works"}

        lead = {"client": c, "answers": answers, "source": "web", "manager_id": ""}
        success, err = await async_save_to_sheet(lead)
        if not success:
            await notify_admin_about_error("Заявка з сайту", err)
            return web.json_response({"error": "save_failed"}, status=500)

        # Рахуємо суму й розсилаємо менеджерам — лід гарячий, реагувати треба швидко
        try:
            prices = await async_get_prices()
            b = calculate_budget(apply_virtual_measurements(lead), prices, labels=get_price_labels())
            total = round(b["total_work"] + b["total_mat_min"])
            rooms_n = len((lead["answers"] or {}).get("rooms") or [])
            text = (f"🌐 *НОВА ЗАЯВКА З САЙТУ*\n\n"
                    f"👤 {html.escape(str(c.get('name')))}\n"
                    f"📞 `{html.escape(phone)}`\n"
                    f"🏠 {html.escape(str(c.get('object_type') or '—'))}, "
                    f"{html.escape(str(c.get('area') or '?'))} м²\n"
                    f"🚪 Приміщень: {rooms_n}\n"
                    f"💰 Орієнтовно: *{total:,} ₴*\n\n"
                    f"_Клієнт порахував сам. Лід вільний — беріть у роботу._").replace(",", " ")
            for uid, info in get_all_authorized_users().items():
                try:
                    await bot.send_message(chat_id=int(uid), text=text, parse_mode="Markdown")
                    await asyncio.sleep(0.1)
                except Exception:
                    pass
        except Exception:
            logging.exception("Не вдалося розіслати сповіщення про лід із сайту")

        return web.json_response({"success": True})
    except Exception:
        logging.exception("submit_lead failed")
        return web.json_response({"error": "server_error"}, status=500)


@cors
async def api_live_calc(request):
    """Єдиний важкий публічний ендпоінт: рахує повний кошторис на кожен запит."""
    if rate_limited(client_ip(request), "calc", CALC_MAX_PER_MINUTE, window=60):
        return too_many()
    try:
        data = await request.json()
        data_with_virtual_meas = apply_virtual_measurements(data)
        prices = await async_get_prices()
        b = calculate_budget(data_with_virtual_meas, prices, labels=get_price_labels())
        # Розбивка для міні-апки: по приміщеннях + «загальні роботи»
        # (демонтаж, стяжка, стеля, двері, електророзводка) як залишок.
        # Це НЕ додаткові гроші — той самий total, розкладений на частини.
        rc = b.get("room_costs") or {}
        rooms_break = {rid: {"work": round(v[0]), "mat_min": round(v[1])} for rid, v in rc.items()}
        rooms_w = sum(v[0] for v in rc.values())
        rooms_m = sum(v[1] for v in rc.values())
        return web.json_response({
            "work": b["total_work"],
            "mat_min": b["total_mat_min"],
            "rooms": rooms_break,
            "general": {
                "work": round(max(b["total_work"] - rooms_w, 0)),
                "mat_min": round(max(b["total_mat_min"] - rooms_m, 0)),
            },
            # Построчна деталізація: {room_id: [{label, qty, unit, rate, work, mat_min}]}
            "room_lines": b.get("room_lines") or {},
            "general_lines": b.get("general_lines") or [],
        })
    except Exception:
        # Повний traceback — у лог Render (там його і шукати при дебагу);
        # клієнту — генерик: текст винятку може розкривати внутрішню
        # структуру даних/шляхи, а публічному ендпоінту це ні до чого.
        logging.exception("live_calc failed")
        return web.json_response({"error": "calc_failed"}, status=500)


@cors
async def api_ping(request):
    return web.Response(text="Pong! Bot is alive 24/7")


@cors
async def api_version(request):
    """Що САМЕ зараз крутиться на проді. Render сам віддає SHA коміту в
    RENDER_GIT_COMMIT — тепер видно, чи доїхав деплой, без здогадок."""
    await async_get_prices()   # переконуємось, що прайс завантажено (і бачимо джерело)
    commit = os.getenv('RENDER_GIT_COMMIT', 'local')
    # Скільки заявок сервер РЕАЛЬНО бачить у таблиці. Це головна діагностика:
    # якщо тут число > 0, а кабінет порожній — проблема в доступі/фронтенді,
    # а не в збереженні.
    try:
        rows = await asyncio.to_thread(_fetch_orders_rows_sync)
        orders_info = {"rows_visible": len(rows)}
    except Exception as e:
        orders_info = {"error": str(e)[:200]}

    return web.json_response({
        "commit": commit,
        "commit_short": commit[:7],
        "branch": os.getenv('RENDER_GIT_BRANCH', '-'),
        "started_at": datetime.fromtimestamp(STARTED_AT).strftime("%Y-%m-%d %H:%M:%S"),
        "uptime_min": round((time.time() - STARTED_AT) / 60, 1),
        "prices": _PRICES_META,          # source: postgres|sheet|default, loaded_at, count
        "orders": orders_info,
        # Ненакочені міграції. Найпідступніший вид поломки: код уже вимагає
        # нової колонки, база її ще не має, і з'ясовується це помилкою 500
        # у клієнта. Тепер видно одразу тут і в логах старту.
        "pending_migrations": PENDING_MIGRATIONS,
        # Які саме бекенди активні просто зараз. Без цього після кожного
        # перемикання доводиться гадати, чи підхопилась змінна оточення.
        "backends": {
            "storage": STORAGE_BACKEND,   # заявки та чернетки
            "prices": PRICES_BACKEND,     # прайс
            "auth": AUTH_BACKEND,         # користувачі та інвайти
        },
        "features": ["room_costs", "room_lines", "drafts", "prices_db", "trash",
                     "web_cabinet", "pdf_estimate"],
    })
