"""Ендпоінти редактора прайсу: хто має доступ і що приймається.

Без плагінів для async-тестів: кожен тест — звичайна функція, яка
проганяє свій сценарій через asyncio.run(). Так набір лишається на
чистому pytest.

Пропускаються, якщо немає БД АБО прайс читається не з неї.

ЧОМУ ДВІ УМОВИ, А НЕ ОДНА. Раніше стояла тільки перевірка DATABASE_URL —
і в codespace, де база задана, а PRICES_BACKEND лишився дефолтним
(sheets), усі тести падали з 409 read_only. Це не поломка коду: редактор
прайсу свідомо вимкнений, поки ціни живуть у Google-таблиці. Тест має це
розуміти й пропускатись, а не червоніти.
"""
import os
import asyncio

import pytest

_HAS_DB = bool(os.getenv("DATABASE_URL"))
_PRICES_IN_DB = os.getenv("PRICES_BACKEND", "sheets").lower() == "postgres"

pytestmark = pytest.mark.skipif(
    not (_HAS_DB and _PRICES_IN_DB),
    reason="потрібні DATABASE_URL і PRICES_BACKEND=postgres "
           "(у CI задається; локально: PRICES_BACKEND=postgres pytest tests/)")


def _app_and_module():
    """Ендпоінти прайсу живуть в api_admin (після розділення main.py)."""
    from aiohttp import web
    import api_admin as mod
    app = web.Application()
    app.router.add_get('/api/admin/prices', mod.api_admin_prices)
    app.router.add_post('/api/admin/prices/save', mod.api_admin_prices_save)
    return app, mod


def _run(scenario):
    """Піднімає тестовий сервер і віддає сценарію (client, module)."""
    from aiohttp.test_utils import TestClient, TestServer

    async def go():
        app, mod = _app_and_module()
        async with TestClient(TestServer(app)) as cli:
            return await scenario(cli, mod)

    return asyncio.run(go())


def _as(mod, uid, role):
    """Підміняє перевірку сесії — токени тут не тестуємо, лише права."""
    mod.auth_request = lambda request: (uid, role)


def test_anonymous_forbidden():
    async def scenario(cli, mod):
        return (await cli.get('/api/admin/prices')).status
    assert _run(scenario) == 403


def test_manager_forbidden():
    async def scenario(cli, mod):
        original = mod.auth_request
        _as(mod, "555", "manager")
        try:
            get = (await cli.get('/api/admin/prices')).status
            post = (await cli.post('/api/admin/prices/save',
                                   json={"items": [{"key": "room_lam", "work": 1}]})).status
            return get, post
        finally:
            mod.auth_request = original
    assert _run(scenario) == (403, 403)


def test_admin_gets_full_price_list():
    """Редактор мусить бачити ВСІ позиції калькулятора, а не лише збережені —
    інакше на свіжій базі екран був би порожній."""
    async def scenario(cli, mod):
        original = mod.auth_request
        _as(mod, "1", "admin")
        try:
            res = await cli.get('/api/admin/prices')
            return res.status, await res.json()
        finally:
            mod.auth_request = original

    status, data = _run(scenario)
    from config import DEFAULT_PRICES
    assert status == 200
    assert data["editable"] is True
    assert len(data["prices"]) >= len(DEFAULT_PRICES)
    row = data["prices"][0]
    for field in ("key", "label", "unit", "work", "mat_min", "mat_max", "saved"):
        assert field in row, f"у відповіді немає поля {field}"


@pytest.mark.parametrize("payload,expected", [
    ({"items": []}, 400),                                   # порожньо
    ({"items": "не список"}, 400),                          # не той тип
    ({"items": [{"key": "room_lam", "work": "абв"}]}, 400),  # текст замість числа
    ({"items": [{"key": "room_lam", "work": -5}]}, 400),     # відʼємна ціна
    ({"items": [{"key": "x", "work": 1}] * 501}, 400),       # надто багато
])
def test_bad_payload_rejected(payload, expected):
    async def scenario(cli, mod):
        original = mod.auth_request
        _as(mod, "1", "admin")
        try:
            return (await cli.post('/api/admin/prices/save', json=payload)).status
        finally:
            mod.auth_request = original
    assert _run(scenario) == expected


def test_save_round_trip():
    """Збережене значення мусить читатись назад тим самим."""
    async def scenario(cli, mod):
        original = mod.auth_request
        _as(mod, "42", "admin")
        try:
            await cli.post('/api/admin/prices/save', json={"items": [
                {"key": "room_lam", "label": "Ламінат", "unit": "м²",
                 "work": 123.45, "mat_min": 10, "mat_max": 20}]})
            res = await cli.get('/api/admin/prices')
            data = await res.json()
            row = next(p for p in data["prices"] if p["key"] == "room_lam")
            return row
        finally:
            mod.auth_request = original

    row = _run(scenario)
    assert row["work"] == 123.45
    assert row["mat_min"] == 10 and row["mat_max"] == 20
    assert row["saved"] is True
    assert row["updated_by"] == "42"


def test_swapped_bounds_are_fixed_not_rejected():
    """«Матеріал від 900 до 600» — описка, а не привід губити введене."""
    async def scenario(cli, mod):
        original = mod.auth_request
        _as(mod, "1", "admin")
        try:
            status = (await cli.post('/api/admin/prices/save', json={"items": [
                {"key": "wall_paint", "work": 100, "mat_min": 900, "mat_max": 600}]})).status
            data = await (await cli.get('/api/admin/prices')).json()
            row = next(p for p in data["prices"] if p["key"] == "wall_paint")
            return status, row["mat_min"], row["mat_max"]
        finally:
            mod.auth_request = original

    status, mat_min, mat_max = _run(scenario)
    assert status == 200
    assert (mat_min, mat_max) == (600, 900)