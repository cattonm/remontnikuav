"""HTTP-обв'язка: CORS та обмеження частоти запитів.

Виділено з main.py, бо цим користуються ВСІ групи ендпоінтів. Тримати
декоратор @cors в одному файлі з бізнес-логікою заявок означало б, що
кожен новий модуль тягне за собою імпорт заявок.
"""
import time

from aiohttp import web
from functools import wraps

from config import _ALLOWED_ORIGINS


def add_cors_headers(response, origin=None):
    # Відповідаємо конкретним origin лише якщо він у білому списку.
    # Для чужих origin заголовок не ставимо взагалі — браузер сам заблокує.
    if origin and origin in _ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Vary'] = 'Origin'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Telegram-Init-Data, X-Session-Token'
    return response


def cors(handler):
    @wraps(handler)
    async def wrapper(request):
        origin = request.headers.get('Origin')
        if request.method == 'OPTIONS':
            return add_cors_headers(web.Response(), origin)
        response = await handler(request)
        return add_cors_headers(response, origin)
    return wrapper


# ==========================================================
# ОБМЕЖЕННЯ ЧАСТОТИ ЗАПИТІВ
# ----------------------------------------------------------
# Лічильники в пам'яті процесу. Це не захист від цілеспрямованої атаки
# (для неї потрібен Cloudflare), а запобіжник від скрипта-переборника
# й від випадкового циклу у фронтенді, який здатен покласти єдиний інстанс.
#
# Ключ бакета — не завжди IP: для платних операцій (Gemini) рахуємо по
# user_id, бо в одного менеджера може бути мобільний інтернет зі змінним IP,
# а гроші витрачає саме акаунт.
# ==========================================================
_RATE_BUCKETS = {}       # {(bucket, key): [timestamps]}

LEAD_MAX_PER_HOUR = 5
CALC_MAX_PER_MINUTE = 60      # калькулятор рахує на кожну зміну — ліміт щедрий
LOGIN_MAX_PER_HOUR = 30
LOGIN_POLL_MAX_PER_MINUTE = 60    # сайт опитує раз на 2 с, 60/хв із запасом
REPORT_MAX_PER_HOUR = 20          # Gemini коштує грошей — обмежуємо на людину
PDF_MAX_PER_HOUR = 60


def client_ip(request):
    """IP за проксі Render. Беремо перший у X-Forwarded-For — саме він клієнтський."""
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else (request.remote or "")).strip()


def rate_limited(key, bucket="lead", limit=LEAD_MAX_PER_HOUR, window=3600):
    """True, якщо цей ключ вичерпав ліміт у цьому вікні.

    Лічильники окремі на кожен bucket: флуд калькулятора не має блокувати
    людині можливість надіслати заявку.
    """
    if not key:
        return False
    now = time.time()
    bucket_key = (bucket, key)
    hits = [t for t in _RATE_BUCKETS.get(bucket_key, []) if now - t < window]
    hits.append(now)
    _RATE_BUCKETS[bucket_key] = hits
    if len(_RATE_BUCKETS) > 5000:      # не даємо словнику рости нескінченно
        _RATE_BUCKETS.clear()
    return len(hits) > limit


def too_many():
    """Готова відповідь 429 — щоб не дублювати JSON у кожному ендпоінті."""
    return web.json_response({"error": "too_many_requests"}, status=429)
