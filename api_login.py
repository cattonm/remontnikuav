"""Вхід у веб-кабінет.

ВХІД НА САЙТ ЧЕРЕЗ БОТА (без Login Widget і без /setdomain):
  1. Сайт просить у бекенда одноразовий код → отримує deep link
     t.me/<bot>?start=web_ABC123.
  2. Людина тисне кнопку → відкривається бот → одне натискання «Запустити».
  3. Бот бачить свій же код, знає user_id (Telegram його гарантує) і
     прив'язує код до цієї людини.
  4. Сайт, який усе це врем'я опитує статус, отримує сесійний токен.

Чому це безпечно: код живе 5 хвилин, одноразовий, і прив'язати його може
лише той, хто реально написав боту зі свого акаунта. Паролів немає.
"""
import asyncio
import logging
import secrets
import time

from aiohttp import web

from config import BOT_TOKEN
from core import bot
from http_utils import (cors, client_ip, rate_limited, too_many,
                        LOGIN_MAX_PER_HOUR, LOGIN_POLL_MAX_PER_MINUTE)
from security import get_all_authorized_users, get_role, redeem_invite
from webauth import create_session, validate_login_widget

_WEB_LOGIN = {}                  # {code: {"ts": float, "uid": str|None}}
LOGIN_CODE_TTL = 300             # 5 хвилин
_BOT_USERNAME_CACHE = None


async def _get_bot_username():
    """Username бота питаємо в самого Telegram і кешуємо.

    ЗАПОБІЖНИК: якщо Telegram недоступний саме в момент першого виклику,
    раніше /api/login_start віддавав 500 і сайт показував порожній екран.
    Тепер помилка не кешується, а наступний запит спробує знову.
    """
    global _BOT_USERNAME_CACHE
    if not _BOT_USERNAME_CACHE:
        me = await bot.get_me()
        _BOT_USERNAME_CACHE = me.username
    return _BOT_USERNAME_CACHE


def _cleanup_login_codes():
    now = time.time()
    for c in [c for c, v in _WEB_LOGIN.items() if now - v["ts"] > LOGIN_CODE_TTL]:
        _WEB_LOGIN.pop(c, None)


@cors
async def api_login_start(request):
    """Видає одноразовий код і deep link на бота."""
    if rate_limited(client_ip(request), "login", LOGIN_MAX_PER_HOUR):
        return too_many()
    _cleanup_login_codes()
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    code = "".join(secrets.choice(alphabet) for _ in range(6))
    _WEB_LOGIN[code] = {"ts": time.time(), "uid": None}
    try:
        username = await _get_bot_username()
    except Exception:
        logging.exception("Telegram не відповів на get_me — вхід тимчасово недоступний")
        _WEB_LOGIN.pop(code, None)
        return web.json_response(
            {"error": "telegram_unavailable",
             "message": "Telegram зараз не відповідає. Спробуйте за хвилину."},
            status=503)
    return web.json_response({
        "code": code,
        "bot": username,
        "deep_link": f"https://t.me/{username}?start=web_{code}",
        "ttl": LOGIN_CODE_TTL,
    })


@cors
async def api_login_poll(request):
    """Сайт опитує: чи підтвердив уже хтось цей код у боті?

    БЕЗПЕКА: ендпоінт публічний, тож без ліміту його можна було б
    використати для перебору 6-символьних кодів, поки хтось саме входить.
    Ліміт робить перебір безглуздим (код живе лише 5 хвилин).
    """
    if rate_limited(client_ip(request), "login_poll", LOGIN_POLL_MAX_PER_MINUTE, window=60):
        return too_many()
    _cleanup_login_codes()
    code = (request.query.get("code") or "").strip().upper()
    entry = _WEB_LOGIN.get(code)
    if not entry:
        return web.json_response({"status": "expired"})
    if not entry["uid"]:
        return web.json_response({"status": "pending"})

    uid = entry["uid"]
    role = get_role(uid)
    _WEB_LOGIN.pop(code, None)              # код одноразовий
    if not role:
        return web.json_response({"status": "no_access"})
    info = get_all_authorized_users().get(str(uid), {})
    return web.json_response({
        "status": "ok",
        "token": create_session(uid, role),
        "role": role,
        "user_id": str(uid),
        "name": info.get("name", ""),
    })


def bind_login_code(code, user_id):
    """Викликається з бота: прив'язує код до людини. True, якщо код живий."""
    _cleanup_login_codes()
    entry = _WEB_LOGIN.get(str(code).strip().upper())
    if not entry:
        return False
    entry["uid"] = str(user_id)
    return True


@cors
async def api_login(request):
    """Обмін підписаних даних Telegram Login Widget на сесійний токен."""
    if rate_limited(client_ip(request), "login", LOGIN_MAX_PER_HOUR):
        return too_many()
    try:
        data = await request.json()
        user_id = validate_login_widget(data, BOT_TOKEN)
        if not user_id:
            return web.json_response({"error": "bad_signature"}, status=401)

        role = get_role(user_id)
        if not role:
            # Вхід є, але доступу немає. Даємо шанс одразу активувати інвайт-код,
            # щоб людину не викидало «в нікуди».
            code = str(data.get("invite") or "").strip().upper()
            if code:
                ok, res = await asyncio.to_thread(
                    redeem_invite, code, user_id,
                    f"{data.get('first_name','')} {data.get('last_name','')}".strip() or "Менеджер",
                    data.get("username") or "немає")
                if not ok:
                    return web.json_response({"error": "bad_invite", "message": res}, status=403)
                role = res
            else:
                return web.json_response({"error": "no_access"}, status=403)

        info = get_all_authorized_users().get(str(user_id), {})
        return web.json_response({
            "token": create_session(user_id, role),
            "role": role,
            "user_id": str(user_id),
            "name": info.get("name") or data.get("first_name") or "",
        })
    except Exception:
        logging.exception("login failed")
        return web.json_response({"error": "server_error"}, status=500)
