"""Фасад авторизації: обирає бекенд і віддає єдиний набір функцій.

    AUTH_BACKEND=sheets     (за замовчуванням) — користувачі з Google-таблиці;
    AUTH_BACKEND=postgres                      — користувачі з таблиць users/invites.

Той самий підхід, що й у storage.py: main.py імпортує звідси і не знає,
де насправді живуть дані. Перемикач окремий від STORAGE_BACKEND і
PRICES_BACKEND — щоб переносити авторизацію окремо і відкочувати одним
прибиранням змінної, без деплою.

ЧОМУ ЦЕ ВАЖЛИВО САМЕ ДЛЯ АВТОРИЗАЦІЇ. У режимі sheets усе тримається на
живому ключі сервіс-акаунта Google. Ключ протухає — і після найближчого
рестарту в кабінет не заходить ніхто, крім майстер-адміна. У режимі
postgres Google для входу не потрібен узагалі.
"""
import os
import logging

# Базою завжди беремо Sheets-реалізацію: звідти ж приходять константи
# (ролі, TTL інвайта, майстер-адмін), спільні для обох бекендів.
from security_sheets import (            # noqa: F401
    ADMIN_PASSWORD, MASTER_ADMIN_ID, SPREADSHEET_NAME,
    ROLE_ADMIN, ROLE_MANAGER, INVITE_TTL_DAYS,
    get_auth_sheet,
    get_all_authorized_users, add_authorized_user, remove_authorized_user,
    get_role, is_admin, is_authorized,
    create_invite, redeem_invite, clear_auth_cache,
)

AUTH_BACKEND = os.getenv("AUTH_BACKEND", "sheets").lower()

if AUTH_BACKEND == "postgres":
    logging.info("AUTH_BACKEND=postgres — користувачі та інвайти з БД")
    from security_postgres import (      # noqa: F401
        get_all_authorized_users, add_authorized_user, remove_authorized_user,
        get_role, is_admin, is_authorized,
        create_invite, redeem_invite, clear_auth_cache,
    )
