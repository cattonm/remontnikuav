"""Контракт авторизації: два бекенди мусять бути взаємозамінні.

БД не потрібна. Ці тести ловлять найпідступнішу помилку переїзду — коли
в одному бекенді функція є, а в іншому забули: код спокійно працює на
sheets і падає в проді через тиждень після перемикання на postgres.
"""
import importlib

import pytest

# Що саме main.py очікує від модуля авторизації.
CONTRACT = (
    "get_all_authorized_users",
    "add_authorized_user",
    "remove_authorized_user",
    "get_role",
    "is_admin",
    "is_authorized",
    "create_invite",
    "redeem_invite",
    "clear_auth_cache",
)

CONSTANTS = ("ROLE_ADMIN", "ROLE_MANAGER", "MASTER_ADMIN_ID",
             "ADMIN_PASSWORD", "INVITE_TTL_DAYS")


@pytest.mark.parametrize("module_name", ["security_sheets", "security_postgres"])
@pytest.mark.parametrize("func", CONTRACT)
def test_backend_implements_contract(module_name, func):
    mod = importlib.import_module(module_name)
    assert callable(getattr(mod, func, None)), \
        f"{module_name} не реалізує {func}()"


@pytest.mark.parametrize("name", CONTRACT + CONSTANTS)
def test_facade_exports_everything(name):
    """Фасад мусить віддавати і функції, і константи — main.py бере все звідти."""
    import security
    assert hasattr(security, name), f"security.py не експортує {name}"


def test_facade_defaults_to_sheets(monkeypatch):
    """Без явної змінної нічого не має мовчки перемкнутись."""
    monkeypatch.delenv("AUTH_BACKEND", raising=False)
    import security
    importlib.reload(security)
    assert security.AUTH_BACKEND == "sheets"
    import security_sheets
    assert security.get_role is security_sheets.get_role


def test_master_admin_is_int():
    """MASTER_ADMIN_ID читається з оточення — має лишитись числом,
    інакше порівняння з Telegram id почне мовчки давати False."""
    import security
    assert isinstance(security.MASTER_ADMIN_ID, int)


def test_invite_alphabet_has_no_confusing_chars():
    """Коди диктують голосом: 0/O та 1/I плутають і код «не працює»."""
    import security_postgres
    for ch in "01OI":
        assert ch not in security_postgres._CODE_ALPHABET
