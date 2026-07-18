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

# .env підвантажуємо самі: `export $(cat .env | xargs)` ламається на значеннях
# з лапками й пробілами — а GOOGLE_CREDS_JSON саме такий. python-dotenv уже є
# в requirements.txt. У проді (Render) .env немає — там змінні задані в
# оточенні, і load_dotenv() просто нічого не робить.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass



def _load_from_sheets():
    """Ціни з аркуша «Ціни».

    ВАЖЛИВО: storage_sheets навмисно «незламний» — якщо Google недоступний,
    він мовчки віддає DEFAULT_PRICES, щоб не повалити калькулятор у проді.
    Для ІМПОРТУ це рівно навпаки: тихо залити в базу дефолти замість реальних
    цін компанії — найгірший можливий результат, бо помітиш це вже по кривих
    кошторисах. Тому тут ми перевіряємо, звідки насправді приїхали дані, і
    падаємо, якщо це не аркуш.
    """
    if not os.getenv("GOOGLE_CREDS_JSON"):
        sys.exit("GOOGLE_CREDS_JSON не заданий — до Google-таблиці не достукатись.\n"
                 "Візьми його в Render -> Environment і додай у .env, або, якщо\n"
                 "свідомо хочеш залити ціни з коду: python import_prices.py --from-defaults")

    from storage_sheets import _get_prices_sync, get_price_labels, _PRICES_META
    prices = _get_prices_sync()
    if _PRICES_META.get("source") != "sheet":
        sys.exit("Прочитати аркуш «Ціни» НЕ вдалося — вище має бути причина.\n"
                 "Імпорт зупинено, щоб не залити в базу дефолтні ціни замість твоїх.\n"
                 "Полагодь доступ до Google або запусти явно: "
                 "python import_prices.py --from-defaults")
    labels = get_price_labels()
    if not labels:
        print("Увага: з аркуша не прочиталась жодна назва — беру назви з коду.")
    return prices, labels


def _load_from_defaults():
    from config import DEFAULT_PRICES
    return dict(DEFAULT_PRICES), {}


def _load_from_csv(path):
    """Ціни з CSV-експорту аркуша «Ціни».

    Запасний шлях, коли доступ до Google API не працює (протухлий ключ
    сервіс-акаунта тощо). У Google Таблицях: Файл -> Завантажити ->
    Значення, розділені комами (.csv) — саме на вкладці «Ціни».

    Очікувані колонки (як у аркуші):
        key | Назва | Робота (грн) | Матеріал мін (грн) | Матеріал макс (грн)
    """
    import csv
    from config import DEFAULT_PRICES

    def _num(x):
        return float(str(x or 0).replace(",", ".").replace("\u00a0", "").replace(" ", "") or 0)

    prices = dict(DEFAULT_PRICES)   # база — дефолти, поверх кладемо CSV
    labels = {}
    bad = read = 0
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or "key" not in reader.fieldnames:
            sys.exit(f"У {path} немає колонки «key». Схоже, це не аркуш «Ціни».\n"
                     f"Знайдені колонки: {reader.fieldnames}")
        for row in reader:
            key = str(row.get("key", "")).strip()
            if not key:
                continue
            try:
                work = _num(row.get("Робота (грн)"))
                m1 = _num(row.get("Матеріал мін (грн)"))
                m2 = _num(row.get("Матеріал макс (грн)"))
            except (TypeError, ValueError):
                bad += 1
                continue
            prices[key] = [work, m1, m2]
            read += 1
            label = str(row.get("Назва", "")).strip()
            if label:
                labels[key] = label
    if bad:
        print(f"Пропущено рядків із кривими числами: {bad}")
    if not read:
        sys.exit(f"З {path} не прочиталось жодної позиції — перевір файл.")
    print(f"Прочитано з CSV позицій: {read} (решта до {len(prices)} — дефолти з коду)")
    return prices, labels


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
    parser.add_argument("--from-csv", metavar="ФАЙЛ",
                        help="взяти ціни з CSV-експорту аркуша «Ціни» "
                             "(коли Google API недоступний)")
    parser.add_argument("--check", action="store_true",
                        help="лише звірити БД із джерелом, нічого не змінювати")
    args = parser.parse_args()

    if not os.getenv("DATABASE_URL"):
        sys.exit("DATABASE_URL не заданий.")

    if args.from_csv:
        source = f"CSV {args.from_csv}"
    elif args.from_defaults:
        source = "config.DEFAULT_PRICES"
    else:
        source = "Google-аркуш «Ціни»"
    print(f"Джерело: {source}")

    try:
        if args.from_csv:
            prices, labels = _load_from_csv(args.from_csv)
        elif args.from_defaults:
            prices, labels = _load_from_defaults()
        else:
            prices, labels = _load_from_sheets()
    except SystemExit:
        raise
    except Exception as e:
        sys.exit(f"Не вдалося прочитати джерело: {e}")

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