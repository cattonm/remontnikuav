#!/usr/bin/env python3
"""Одноразове перенесення прайсу: Google-таблиця -> Postgres.

Запускається ПІСЛЯ міграцій і ПЕРЕД тим, як виставляти PRICES_BACKEND=postgres.

    python migrate.py up          # створює таблицю prices
    python import_prices.py       # заливає в неї поточні ціни
    python import_prices.py --check   # звіряє БД з таблицею, нічого не пише

Джерело за замовчуванням — аркуш «Ціни» (потрібен GOOGLE_CREDS_JSON).
Якщо доступу до Google немає, --from-defaults залиє DEFAULT_PRICES з config.py.

Скрипт безпечно запускати повторно: це upsert по (tenant_id, key), дублікатів
не буде. Уже наявні в БД позиції він перезапише значеннями з джерела.
"""
import os
import sys
import argparse


def _load_from_sheets():
    from storage_sheets import _get_prices_sync, get_price_labels
    prices = _get_prices_sync()
    labels = get_price_labels()
    if not labels:
        print("Увага: з аркуша не прочиталась жодна назва — беру назви з коду.")
    return prices, labels


def _load_from_defaults():
    from config import DEFAULT_PRICES
    return dict(DEFAULT_PRICES), {}


def _to_items(prices, labels):
    from calculator import PRICE_META
    items = []
    for key, triple in prices.items():
        try:
            work, mat_min, mat_max = (float(x) for x in triple)
        except (TypeError, ValueError):
            print(f"  пропускаю «{key}»: некоректні числа {triple!r}")
            continue
        meta_label, meta_unit = PRICE_META.get(key, (key, ""))
        items.append({
            "key": key,
            "label": labels.get(key) or meta_label,
            "unit": meta_unit,
            "work": work, "mat_min": mat_min, "mat_max": mat_max,
        })
    return items


def main():
    parser = argparse.ArgumentParser(description="Перенесення прайсу в Postgres")
    parser.add_argument("--from-defaults", action="store_true",
                        help="брати DEFAULT_PRICES з config.py замість Google-таблиці")
    parser.add_argument("--check", action="store_true",
                        help="лише звірити БД із джерелом, нічого не змінювати")
    args = parser.parse_args()

    if not os.getenv("DATABASE_URL"):
        sys.exit("DATABASE_URL не заданий.")

    source = "config.DEFAULT_PRICES" if args.from_defaults else "Google-аркуш «Ціни»"
    print(f"Джерело: {source}")
    try:
        prices, labels = (_load_from_defaults() if args.from_defaults
                          else _load_from_sheets())
    except Exception as e:
        sys.exit(f"Не вдалося прочитати джерело: {e}\n"
                 f"Спробуй: python import_prices.py --from-defaults")

    items = _to_items(prices, labels)
    print(f"Позицій до перенесення: {len(items)}")

    # Імпортуємо реалізацію напряму, а не через фасад: скрипт має працювати
    # незалежно від того, як зараз виставлений PRICES_BACKEND.
    import storage_postgres as pg

    if args.check:
        in_db = {r["key"]: r for r in pg._list_prices_sync() if r["saved"]}
        missing = [i["key"] for i in items if i["key"] not in in_db]
        diff = []
        for i in items:
            row = in_db.get(i["key"])
            if row and (row["work"], row["mat_min"], row["mat_max"]) != \
                       (i["work"], i["mat_min"], i["mat_max"]):
                diff.append(i["key"])
        print(f"У БД збережено: {len(in_db)}")
        print(f"Немає в БД: {len(missing)}" + (f" -> {missing[:10]}" if missing else ""))
        print(f"Відрізняються значення: {len(diff)}" + (f" -> {diff[:10]}" if diff else ""))
        if not missing and not diff:
            print("БД повністю збігається з джерелом.")
        return

    n = pg._upsert_prices_sync(items, updated_by="import")
    print(f"Записано/оновлено позицій: {n}")
    print("Далі: виставити PRICES_BACKEND=postgres і перезапустити сервіс.")


if __name__ == "__main__":
    main()
