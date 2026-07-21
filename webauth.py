"""Автентифікація: підписи Telegram + сесії веб-кабінету.

Два незалежні входи, які зводяться до однієї функції auth_request():
  • X-Telegram-Init-Data — міні-апка всередині Telegram (підпис від Telegram);
  • X-Session-Token      — сайт у звичайному браузері (наш власний підпис).

Нічого не тримаємо в пам'яті сервера: Render перезапускається, а сесії
мають жити. Тому токен самодостатній — base64(payload).signature.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from urllib.parse import parse_qsl

from config import BOT_TOKEN, SESSION_SECRET
from security import get_role

SESSION_TTL = 30 * 24 * 3600      # 30 днів

# Скільки живуть дані initData від Telegram. БЕЗПЕКА: без цієї перевірки
# перехоплений один раз рядок initData (наприклад, із логів проксі або
# з чужого пристрою) працював би вічно — Telegram його не відкликає.
# 24 години — рекомендація самого Telegram. Якщо менеджери скаржаться, що
# міні-апку доводиться перевідкривати, значення можна підняти через env.
INITDATA_MAX_AGE = int(os.getenv("INITDATA_MAX_AGE_H", "24")) * 3600


def validate_telegram_data(init_data: str, bot_token: str):
    """Перевірка initData з міні-апки. Повертає user_id або None."""
    try:
        parsed_data = dict(parse_qsl(init_data))
        if 'hash' not in parsed_data:
            return None
        hash_val = parsed_data.pop('hash')
        sorted_data = sorted(parsed_data.items(), key=lambda x: x[0])
        data_check_string = '\n'.join([f"{k}={v}" for k, v in sorted_data])
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc_hash, hash_val):
            return None
        # Захист від повторного використання старих даних (див. INITDATA_MAX_AGE).
        auth_date = int(parsed_data.get('auth_date', 0))
        if not auth_date or (time.time() - auth_date) > INITDATA_MAX_AGE:
            logging.info("initData відхилено: застарілий auth_date")
            return None
        return json.loads(parsed_data.get('user', '{}')).get('id')
    except Exception:
        return None


def validate_login_widget(data: dict, bot_token: str):
    """Перевірка даних з Telegram Login Widget (вхід на ЗВИЧАЙНОМУ сайті).

    Відрізняється від initData: секрет — це SHA256(bot_token) без солі
    "WebAppData", а рядок перевірки збирається з плоского словника.
    Підпис гарантує, що дані прийшли саме від Telegram і не підроблені —
    тому паролі не потрібні взагалі.
    """
    try:
        data = dict(data)
        hash_val = data.pop("hash", None)
        if not hash_val:
            return None
        # Захист від replay: дані старші за добу не приймаємо
        if abs(time.time() - int(data.get("auth_date", 0))) > 86400:
            return None
        check = "\n".join(f"{k}={data[k]}" for k in sorted(data) if data[k] is not None)
        secret = hashlib.sha256(bot_token.encode()).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        return int(data["id"]) if hmac.compare_digest(calc, hash_val) else None
    except Exception:
        return None


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def create_session(user_id, role):
    payload = json.dumps({"uid": str(user_id), "role": role,
                          "exp": int(time.time()) + SESSION_TTL}, separators=(",", ":"))
    body = _b64e(payload.encode())
    sig = hmac.new(SESSION_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def read_session(token):
    """Повертає {'uid', 'role'} або None. Роль ПЕРЕПЕРЕВІРЯЄМО в базі —
    якщо доступ відкликано, старий токен перестає діяти негайно."""
    try:
        body, sig = str(token).split(".", 1)
        expect = hmac.new(SESSION_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expect):
            return None
        data = json.loads(_b64d(body))
        if data.get("exp", 0) < time.time():
            return None
        role_now = get_role(data["uid"])       # актуальна роль, а не та, що в токені
        if not role_now:
            return None
        return {"uid": data["uid"], "role": role_now}
    except Exception:
        return None


def auth_request(request):
    """ЄДИНА точка автентифікації. Повертає (user_id, role) або (None, None)."""
    token = request.headers.get('X-Session-Token')
    if token:
        s = read_session(token)
        if s:
            return s["uid"], s["role"]
    init_data = request.headers.get('X-Telegram-Init-Data')
    if init_data:
        uid = validate_telegram_data(init_data, BOT_TOKEN)
        if uid:
            role = get_role(uid)
            if role:
                return str(uid), role
    return None, None
