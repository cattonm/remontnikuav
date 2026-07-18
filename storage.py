"""Єдина точка доступу до сховища (Етап 3).

main.py імпортує все звідси, не знаючи, яке сховище під капотом. Бекенд
обирається змінною оточення STORAGE_BACKEND:

    STORAGE_BACKEND=sheets    (за замовчуванням) — Google Sheets, як було;
    STORAGE_BACKEND=postgres                     — Postgres (Supabase).

Дефолт — sheets, тому коміт цих файлів НІЧОГО не змінює в проді, доки ти сам
не виставиш STORAGE_BACKEND=postgres. Це ж дає миттєвий відкіт: якщо з базою
щось не так — прибираєш змінну, і все повертається на Sheets без деплою коду.

Ціни мають ОКРЕМИЙ перемикач, бо це окремий ризик:

    PRICES_BACKEND=sheets     (за замовчуванням) — прайс із Google-таблиці;
    PRICES_BACKEND=postgres                      — прайс із таблиці prices.

Розділено навмисно: заявки вже давно в Postgres, а прайс переносимо зараз, і
якщо з ним щось піде не так — прибираєш одну змінну, і ціни знову з аркуша,
без відкату заявок і без деплою.
"""
import os
import logging

# Базовий (типовий) бекенд — Sheets. Імпортуємо ПОВНИЙ набір імен, які потрібні
# main.py; частину з них нижче перекриє Postgres, якщо його ввімкнено.
from storage_sheets import (
    _log_action_sync, async_log_action, _get_google_sheet, _ensure_header_sync,
    async_ensure_header, _save_to_sheet_sync, _update_row_sync, _get_row_data_sync,
    _row_meta, invalidate_orders_cache, _fetch_orders_rows_sync, _meta_from_parts,
    _list_orders_sync, _set_deal_status_sync, _soft_delete_sync, _purge_rows_sync,
    _list_trash_sync, async_soft_delete, async_purge_rows, async_list_trash,
    async_list_orders, async_set_deal_status, _delete_row_sync, _save_report_sync,
    async_save_to_sheet, async_update_row, async_get_row_data, async_save_report,
    async_delete_row, _prices_bootstrap_sheet, _get_prices_sync, get_price_labels,
    async_get_prices, _drafts_ws, _save_draft_sync, _get_draft_sync, _delete_draft_sync,
    _scan_drafts_for_reminders_sync, _mark_reminded_sync,
    DRAFTS_SHEET_NAME, DRAFT_REMIND_AFTER_H, DRAFT_TTL_DAYS, _PRICES_META,
)

STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "sheets").lower()

if STORAGE_BACKEND == "postgres":
    logging.info("STORAGE_BACKEND=postgres — заявки й чернетки з Postgres")
    # Перекриваємо ЛИШЕ роботу із заявками/чернетками/журналом.
    # Ціни, _row_meta/_meta_from_parts (чисті парсери) і _get_google_sheet
    # лишаються зі storage_sheets (див. docstring).
    from storage_postgres import (
        _log_action_sync, async_log_action, _ensure_header_sync, async_ensure_header,
        _save_to_sheet_sync, _update_row_sync, _get_row_data_sync,
        _fetch_orders_rows_sync, _list_orders_sync, _set_deal_status_sync,
        _soft_delete_sync, _purge_rows_sync, _list_trash_sync, async_soft_delete,
        async_purge_rows, async_list_trash, async_list_orders, async_set_deal_status,
        _delete_row_sync, _save_report_sync, async_save_to_sheet, async_update_row,
        async_get_row_data, async_save_report, async_delete_row,
        _save_draft_sync, _get_draft_sync, _delete_draft_sync,
        _scan_drafts_for_reminders_sync, _mark_reminded_sync,
    )


# ── Ціни: незалежний перемикач ────────────────────────────
PRICES_BACKEND = os.getenv("PRICES_BACKEND", "sheets").lower()

if PRICES_BACKEND == "postgres":
    logging.info("PRICES_BACKEND=postgres — прайс із таблиці prices")
    from storage_postgres import (
        _get_prices_sync, get_price_labels, async_get_prices, _PRICES_META,
        _list_prices_sync, _upsert_prices_sync, async_list_prices,
        async_upsert_prices, invalidate_prices_cache,
    )
    PRICES_EDITABLE = True          # кабінет може редагувати прайс
else:
    # Прайс у Google-таблиці: редагується там же, у кабінеті — лише перегляд.
    PRICES_EDITABLE = False

    def invalidate_prices_cache():
        """У режимі sheets кеш скидається сам за TTL — робити нічого."""
        return None

    async def async_list_prices():
        raise RuntimeError(
            "Редактор прайсу працює лише з PRICES_BACKEND=postgres. "
            "Зараз ціни живуть у Google-таблиці — редагуй їх там."
        )

    async def async_upsert_prices(items, updated_by=""):
        raise RuntimeError(
            "Редактор прайсу працює лише з PRICES_BACKEND=postgres. "
            "Зараз ціни живуть у Google-таблиці — редагуй їх там."
        )
