#!/usr/bin/env python3
"""Керування доступами до кабінету (таблиця users).

Щоденні дії з менеджерами без сирого SQL і без psql:

    python manage_users.py list                 # усі, включно з відкликаними
    python manage_users.py revoke 777001        # забрати доступ
    python manage_users.py restore 777001       # повернути доступ
    python manage_users.py role 845232133 admin # змінити роль

Відкликання не стирає рядок, а ставить status='revoked': видно, кому й
коли давали доступ, і будь-яку дію можна відкотити.

Працює лише з Postgres (AUTH_BACKEND=postgres). Поки користувачі живуть
у Google-таблиці, редагуй їх там.
"""
import os
import sys
import argparse

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROLES = ("admin", "manager")


def _rows():
    from storage_postgres import _conn
    tenant = int(os.getenv("TENANT_ID", "1"))
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            "SELECT tg_id, name, username, role, status, created_at "
            "FROM users WHERE tenant_id = %s "
            "ORDER BY status, role, lower(name)", (tenant,))
        return cur.fetchall()


def cmd_list():
    rows = _rows()
    if not rows:
        print("Користувачів немає. Спочатку: python import_users.py --from-csv admins.csv")
        return

    master = str(os.getenv("MASTER_ADMIN_ID") or 845232133)
    print(f"{'ID':<12} {'Імʼя':<22} {'Нік':<16} {'Роль':<9} {'Статус':<9} Доданий")
    print("-" * 86)
    for tg_id, name, username, role, status, created in rows:
        mark = " ★" if str(tg_id) == master else ""
        flag = "" if status == "active" else "  ← доступу немає"
        print(f"{tg_id:<12} {(name or '')[:21]:<22} {(username or '')[:15]:<16} "
              f"{role:<9} {status:<9} "
              f"{created.strftime('%Y-%m-%d') if created else ''}{mark}{flag}")

    active = sum(1 for r in rows if r[4] == "active")
    print(f"\nАктивних: {active}, відкликаних: {len(rows) - active}")
    print("★ — майстер-адмін: заходить завжди, навіть якщо база недоступна.")


def _set_status(tg_id, status):
    from storage_postgres import _conn
    import security_postgres as sp
    tenant = int(os.getenv("TENANT_ID", "1"))
    with _conn() as con, con.cursor() as cur:
        cur.execute("UPDATE users SET status = %s "
                    "WHERE tenant_id = %s AND tg_id = %s RETURNING name",
                    (status, tenant, str(tg_id)))
        row = cur.fetchone()
    sp.clear_auth_cache()
    return row


def cmd_revoke(tg_id):
    master = str(os.getenv("MASTER_ADMIN_ID") or 845232133)
    if str(tg_id) == master:
        sys.exit("Це майстер-адмін — відкликати його доступ немає сенсу: "
                 "він заходить в обхід таблиці. Змінюй MASTER_ADMIN_ID в оточенні.")
    row = _set_status(tg_id, "revoked")
    if not row:
        sys.exit(f"Користувача {tg_id} не знайдено.")
    print(f"Доступ відкликано: {row[0] or tg_id}")
    print("Повернути: python manage_users.py restore " + str(tg_id))


def cmd_restore(tg_id):
    row = _set_status(tg_id, "active")
    if not row:
        sys.exit(f"Користувача {tg_id} не знайдено.")
    print(f"Доступ повернуто: {row[0] or tg_id}")


def cmd_role(tg_id, role):
    from storage_postgres import _conn
    import security_postgres as sp
    if role not in ROLES:
        sys.exit(f"Роль має бути одна з: {', '.join(ROLES)}")
    tenant = int(os.getenv("TENANT_ID", "1"))
    with _conn() as con, con.cursor() as cur:
        cur.execute("UPDATE users SET role = %s "
                    "WHERE tenant_id = %s AND tg_id = %s RETURNING name",
                    (role, tenant, str(tg_id)))
        row = cur.fetchone()
    sp.clear_auth_cache()
    if not row:
        sys.exit(f"Користувача {tg_id} не знайдено.")
    print(f"Роль оновлено: {row[0] or tg_id} -> {role}")


def main():
    parser = argparse.ArgumentParser(description="Керування доступами до кабінету")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("list", help="показати всіх користувачів")
    p = sub.add_parser("revoke", help="забрати доступ"); p.add_argument("tg_id")
    p = sub.add_parser("restore", help="повернути доступ"); p.add_argument("tg_id")
    p = sub.add_parser("role", help="змінити роль")
    p.add_argument("tg_id"); p.add_argument("role", choices=ROLES)
    args = parser.parse_args()

    if not os.getenv("DATABASE_URL"):
        sys.exit("DATABASE_URL не заданий.")

    if args.cmd == "revoke":
        cmd_revoke(args.tg_id)
    elif args.cmd == "restore":
        cmd_restore(args.tg_id)
    elif args.cmd == "role":
        cmd_role(args.tg_id, args.role)
    else:
        cmd_list()


if __name__ == "__main__":
    main()