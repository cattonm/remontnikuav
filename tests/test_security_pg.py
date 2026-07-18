"""Інтеграційні тести авторизації на справжньому Postgres.

Запускаються лише коли заданий DATABASE_URL — у CI він є (сервіс postgres),
локально тести просто пропускаються. Так набір лишається швидким на
машині розробника, але критичні гарантії все одно перевіряються на кожному
пуші, а не «на віру».

УВАГА: тести пишуть у таблицю users, тому працюють з окремим tenant_id,
щоб не зачепити реальних користувачів, якщо DATABASE_URL раптом вкаже на
робочу базу.
"""
import os
import threading

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="потрібен DATABASE_URL (у CI задається сервісом postgres)")

TEST_TENANT = 9999


@pytest.fixture(scope="module")
def sec():
    import security_postgres as sp
    from storage_postgres import _conn

    # Пісочниця: окремий тенант, який гарантовано не збігається з робочим.
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            "INSERT INTO tenants (id, slug, name) VALUES (%s, %s, %s) "
            "ON CONFLICT (id) DO NOTHING",
            (TEST_TENANT, f"test-{TEST_TENANT}", "Тестовий тенант"))
    sp.TENANT_ID = TEST_TENANT

    yield sp

    with _conn() as con, con.cursor() as cur:
        cur.execute("DELETE FROM invites WHERE tenant_id = %s", (TEST_TENANT,))
        cur.execute("DELETE FROM users WHERE tenant_id = %s", (TEST_TENANT,))
        cur.execute("DELETE FROM tenants WHERE id = %s", (TEST_TENANT,))


def test_add_and_read_back(sec):
    assert sec.add_authorized_user(1001, "Тест", "@t", "manager")
    assert sec.get_role(1001) == "manager"
    assert sec.is_authorized(1001)
    assert not sec.is_admin(1001)


def test_repeat_add_updates_not_duplicates(sec):
    sec.add_authorized_user(1002, "Стара Назва", "@old", "manager")
    sec.add_authorized_user(1002, "Нова Назва", "@new", "admin")
    users = sec.get_all_authorized_users(force_refresh=True)
    assert users["1002"]["role"] == "admin"
    assert users["1002"]["name"] == "Нова Назва"
    assert sum(1 for k in users if k == "1002") == 1


def test_revoke_and_restore(sec):
    sec.add_authorized_user(1003, "Тимчасовий", "@tmp", "manager")
    sec.remove_authorized_user(1003)
    assert not sec.is_authorized(1003)
    assert sec.get_role(1003) is None
    sec.add_authorized_user(1003, "Повернувся", "@tmp", "manager")
    assert sec.is_authorized(1003)


def test_master_admin_works_without_any_row(sec):
    """Аварійний вхід: майстер заходить, навіть якщо його немає в таблиці."""
    assert sec.is_authorized(sec.MASTER_ADMIN_ID)
    assert sec.get_role(sec.MASTER_ADMIN_ID) == "admin"


def test_invite_is_single_use(sec):
    code = sec.create_invite(sec.MASTER_ADMIN_ID, "manager")
    assert code and len(code) == 8
    ok, role = sec.redeem_invite(code, 1004, "Перший", "@one")
    assert ok and role == "manager"
    ok2, reason = sec.redeem_invite(code, 1005, "Другий", "@two")
    assert not ok2 and "використано" in reason


def test_unknown_code_rejected(sec):
    ok, reason = sec.redeem_invite("ZZZZZZZZ", 1006, "X", "@x")
    assert not ok and "не знайдено" in reason


def test_expired_code_rejected(sec):
    from storage_postgres import _conn
    code = sec.create_invite(sec.MASTER_ADMIN_ID, "manager")
    with _conn() as con, con.cursor() as cur:
        cur.execute("UPDATE invites SET expires_at = now() - interval '1 day' "
                    "WHERE code = %s", (code,))
    ok, reason = sec.redeem_invite(code, 1007, "Пізно", "@late")
    assert not ok and "протермінован" in reason.lower()


def test_concurrent_redeem_gives_exactly_one_winner(sec):
    """Головна перевага переїзду: у Sheets-версії між читанням і записом
    був проміжок, і один код міг впустити двох людей."""
    code = sec.create_invite(sec.MASTER_ADMIN_ID, "manager")
    results = []
    lock = threading.Lock()

    def worker(uid):
        res = sec.redeem_invite(code, uid, f"U{uid}", f"@u{uid}")
        with lock:
            results.append(res)

    threads = [threading.Thread(target=worker, args=(2000 + i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(1 for ok, _ in results if ok) == 1, \
        f"код спрацював більше одного разу: {results}"
