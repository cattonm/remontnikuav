#!/usr/bin/env python3
"""Міграції схеми БД.

НАВІЩО. Досі схема жила в schema.sql із інструкцією «запусти один раз
руками в Supabase». Поки таблиць три, це працює. Коли схема починає
змінюватись щотижня (а для SaaS так і буде), руками неможливо тримати в
голові, що вже накотили на прод, а що ні — і рано чи пізно код їде на
базу, яка ще не має потрібної колонки.

ЯК ЦЕ ПРАЦЮЄ. У папці migrations/ лежать .sql-файли з номерами:
0001_..., 0002_... Рушій дивиться в таблицю schema_migrations, бачить, що
вже накочено, і застосовує лише нове — по порядку, кожен файл в окремій
транзакції. Накотити двічі неможливо.

ЧОМУ НЕ ALEMBIC. Alembic сильний тим, що генерує міграції з ORM-моделей.
ORM у проєкті немає — ми пишемо чистий SQL через psycopg2. Тому Alembic
дав би лише зайвий шар boilerplate навколо тих самих .sql-файлів плюс
залежність від SQLAlchemy. Коли на Етапі B зʼявиться SQLAlchemy, перейти
на Alembic буде просто: `alembic stamp` фіксує поточний стан, далі новий
рушій.

КОМАНДИ:
    python migrate.py status    # що накочено, що чекає
    python migrate.py up        # накотити все, що чекає
    python migrate.py up --dry-run   # показати, але не виконувати

DATABASE_URL береться з оточення (той самий, що й у застосунку).
"""
import os
import sys
import glob
import hashlib
import argparse

import psycopg2

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")

# Довільний, але сталий ключ. Гарантує, що два одночасні деплої не
# почнуть накочувати ту саму міграцію паралельно: другий чекає першого.
_LOCK_ID = 482025071

_CREATE_LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT        PRIMARY KEY,
    checksum   TEXT        NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _dsn():
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        sys.exit("DATABASE_URL не заданий. Приклад:\n"
                 "  DATABASE_URL='postgresql://...' python migrate.py status")
    return dsn


def discover():
    """Усі міграції з диска, відсортовані за іменем файлу.

    Повертає список кортежів (version, path, sql, checksum).
    version — імʼя файлу без .sql, воно ж ключ у schema_migrations.
    """
    found = []
    for path in sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.sql"))):
        with open(path, "r", encoding="utf-8") as fh:
            sql = fh.read()
        version = os.path.basename(path)[:-4]
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16]
        found.append((version, path, sql, checksum))

    versions = [v for v, _, _, _ in found]
    dupes = {v for v in versions if versions.count(v) > 1}
    if dupes:
        sys.exit(f"Дублікати версій міграцій: {', '.join(sorted(dupes))}")
    return found


def _applied(cur):
    cur.execute(_CREATE_LEDGER)
    cur.execute("SELECT version, checksum FROM schema_migrations")
    return dict(cur.fetchall())


def cmd_status(conn):
    with conn.cursor() as cur:
        done = _applied(cur)
    conn.commit()

    print(f"Міграцій на диску: {len(discover())}, накочено: {len(done)}\n")
    changed = []
    for version, _, _, checksum in discover():
        if version not in done:
            print(f"  ⏳ {version}  — чекає")
        elif done[version] != checksum:
            print(f"  ⚠️  {version}  — НАКОЧЕНО, але файл змінився після цього")
            changed.append(version)
        else:
            print(f"  ✅ {version}")

    orphans = set(done) - {v for v, _, _, _ in discover()}
    for version in sorted(orphans):
        print(f"  ❓ {version}  — є в базі, але файлу немає (гілка/відкат?)")
    return changed


def cmd_up(conn, dry_run=False):
    """Накочує все, що ще не накочено. Кожна міграція — окрема транзакція:
    якщо третя впаде, перші дві лишаться накоченими, і після виправлення
    достатньо запустити команду ще раз."""
    with conn.cursor() as cur:
        # Session-level лок: тримається до кінця процесу, переживає commit.
        cur.execute("SELECT pg_advisory_lock(%s)", (_LOCK_ID,))
        done = _applied(cur)
    conn.commit()

    # Зміна вже накоченого файлу — майже завжди помилка: у проді лишиться
    # стара версія, локально буде нова, і різницю ніхто не помітить.
    for version, _, _, checksum in discover():
        if version in done and done[version] != checksum:
            sys.exit(
                f"Міграцію {version} вже накочено, але файл після цього змінили.\n"
                f"Не редагуй накочені міграції — створи нову з виправленням."
            )

    pending = [(v, p, s, c) for v, p, s, c in discover() if v not in done]
    if not pending:
        print("Все актуально — накочувати нічого.")
        return 0

    for version, path, sql, checksum in pending:
        if dry_run:
            print(f"  [dry-run] накотив би {version}")
            continue
        print(f"  \u25b6 {version} ...", end=" ", flush=True)
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                    (version, checksum),
                )
            conn.commit()
            print("ок")
        except Exception as exc:
            conn.rollback()
            print("ПОМИЛКА")
            sys.exit(f"\nМіграція {version} впала і відкочена цілком:\n{exc}\n"
                     f"Файл: {path}")

    if dry_run:
        print(f"\n[dry-run] нічого не змінено; до накату — {len(pending)}.")
    else:
        print(f"\nГотово: накочено {len(pending)}.")
    return len(pending)


def main():
    parser = argparse.ArgumentParser(description="Міграції схеми БД")
    parser.add_argument("command", nargs="?", default="status",
                        choices=["status", "up"])
    parser.add_argument("--dry-run", action="store_true",
                        help="показати план, нічого не виконувати")
    args = parser.parse_args()

    conn = psycopg2.connect(_dsn())
    try:
        if args.command == "status":
            cmd_status(conn)
        else:
            cmd_up(conn, dry_run=args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
