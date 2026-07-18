#!/usr/bin/env python3
"""Одноразове перенесення користувачів: аркуш «Admins» -> Postgres.

    python migrate.py up            # створює таблиці users/invites
    python import_users.py          # заливає користувачів з Google-таблиці
    python import_users.py --from-csv admins.csv   # те саме без Google API
    python import_users.py --check  # звірка, нічого не пише

ПРО ІНВАЙТИ. Невикористані коди навмисно НЕ переносяться: вони живуть 7 днів
і потрібні рівно один раз. Простіше видати нові з кабінету, ніж тягнути
історію. Уже погашені коди — тим більше історія, а не дані.

ЯКЩО GOOGLE НЕ ВІДПОВІДАЄ (протухлий ключ сервіс-акаунта): відкрий таблицю,
вкладку «Admins», Файл -> Завантажити -> CSV, і запусти з --from-csv.
Очікувані колонки: user_id | name | username | added_date | role
"""
import os
import sys
import argparse

# .env підвантажуємо самі — див. коментар у migrate.py.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _norm_role(value):
    role = str(value or "").strip().lower()
    return role if role in ("admin", "manager") else "manager"


def _load_from_sheets():
    if not os.getenv("GOOGLE_CREDS_JSON"):
        sys.exit("GOOGLE_CREDS_JSON не заданий — до Google-таблиці не достукатись.\n"
                 "Або полагодь ключ, або: python import_users.py --from-csv admins.csv")

    from security_sheets import get_auth_sheet
    sheet = get_auth_sheet()
    if not sheet:
        sys.exit("Аркуш «Admins» прочитати не вдалося — причина вище.\n"
                 "Обхід без Google: python import_users.py --from-csv admins.csv")

    rows = sheet.get_all_values()
    users = []
    for row in rows[1:]:
        if not row or not str(row[0]).strip():
            continue
        users.append({
            "tg_id": str(row[0]).strip(),
            "name": row[1].strip() if len(row) > 1 else "",
            "username": row[2].strip() if len(row) > 2 else "",
            "role": _norm_role(row[4] if len(row) > 4 else ""),
        })
    return users


def _load_from_csv(path):
    import csv
    users = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        cols = reader.fieldnames or []
        # Аркуш могли експортувати з іншими заголовками — шукаємо колонку з id
        id_col = next((c for c in cols if c and c.strip().lower()
                       in ("user_id", "id", "tg_id", "telegram_id")), None)
        if not id_col:
            sys.exit(f"У {path} не знайдено колонку з id користувача.\n"
                     f"Знайдені колонки: {cols}")
        name_col = next((c for c in cols if c and c.strip().lower() in ("name", "імʼя", "ім'я")), None)
        user_col = next((c for c in cols if c and c.strip().lower() in ("username", "нік")), None)
        role_col = next((c for c in cols if c and c.strip().lower() in ("role", "роль")), None)

        for row in reader:
            tg_id = str(row.get(id_col, "")).strip()
            if not tg_id:
                continue
            users.append({
                "tg_id": tg_id,
                "name": str(row.get(name_col, "") or "").strip(),
                "username": str(row.get(user_col, "") or "").strip(),
                "role": _norm_role(row.get(role_col)),
            })
    return users


def main():
    parser = argparse.ArgumentParser(description="Перенесення користувачів у Postgres")
    parser.add_argument("--from-csv", metavar="ФАЙЛ",
                        help="взяти користувачів із CSV-експорту аркуша «Admins»")
    parser.add_argument("--check", action="store_true",
                        help="лише звірити БД із джерелом, нічого не змінювати")
    args = parser.parse_args()

    if not os.getenv("DATABASE_URL"):
        sys.exit("DATABASE_URL не заданий.")

    source = f"CSV {args.from_csv}" if args.from_csv else "Google-аркуш «Admins»"
    print(f"Джерело: {source}")
    users = _load_from_csv(args.from_csv) if args.from_csv else _load_from_sheets()
    if not users:
        sys.exit("Джерело порожнє — жодного користувача не знайдено.")

    admins = sum(1 for u in users if u["role"] == "admin")
    print(f"Знайдено користувачів: {len(users)} (адмінів: {admins}, менеджерів: {len(users) - admins})")

    # Імпортуємо реалізацію напряму, незалежно від того, як виставлений AUTH_BACKEND.
    import security_postgres as pg

    if args.check:
        in_db = pg.get_all_authorized_users(force_refresh=True)
        missing = [u["tg_id"] for u in users if u["tg_id"] not in in_db]
        diff = [u["tg_id"] for u in users
                if u["tg_id"] in in_db and in_db[u["tg_id"]]["role"] != u["role"]]
        print(f"У БД активних: {len(in_db)}")
        print(f"Немає в БД: {len(missing)}" + (f" -> {missing}" if missing else ""))
        print(f"Відрізняється роль: {len(diff)}" + (f" -> {diff}" if diff else ""))
        if not missing and not diff:
            print("БД повністю збігається з джерелом.")
        return

    added = 0
    for u in users:
        if pg.add_authorized_user(u["tg_id"], u["name"], u["username"], u["role"]):
            added += 1
        else:
            print(f"  не вдалося додати {u['tg_id']}")
    print(f"Записано/оновлено користувачів: {added}")
    print("Далі: виставити AUTH_BACKEND=postgres і перезапустити сервіс.")


if __name__ == "__main__":
    main()
