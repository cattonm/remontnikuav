"""Контракт маршрутів: жоден ендпоінт не мав загубитись при розділенні main.py.

Список нижче — це те, що ЗАРАЗ використовує фронтенд. Якщо тест червоний,
значить або маршрут прибрали (і кабінет зламається), або перейменували
й забули оновити фронт. Тест дешевий, а ловить найдорожчу помилку.

Заодно перевіряє, що до кожного маршруту зареєстровано OPTIONS: без нього
браузер не пройде preflight і фронт отримає загадкову помилку CORS.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


EXPECTED = {
    ("GET", "/api/get_order"),
    ("POST", "/api/save_order"),
    ("POST", "/api/create_order"),
    ("GET", "/api/orders"),
    ("GET", "/api/order_detail"),
    ("GET", "/api/order_pdf"),
    ("POST", "/api/generate_report"),
    ("POST", "/api/order_delete"),
    ("POST", "/api/order_restore"),
    ("GET", "/api/trash"),
    ("POST", "/api/purge"),
    ("POST", "/api/login_start"),
    ("GET", "/api/login_poll"),
    ("POST", "/api/login"),
    ("GET", "/api/admin/users"),
    ("POST", "/api/admin/invite"),
    ("POST", "/api/admin/revoke"),
    ("GET", "/api/admin/prices"),
    ("POST", "/api/admin/prices/save"),
    ("GET", "/api/admin/stats"),
    ("POST", "/api/save_draft"),
    ("GET", "/api/get_draft"),
    ("POST", "/api/delete_draft"),
    ("GET", "/api/me"),
    ("POST", "/api/submit_lead"),
    ("POST", "/api/live_calc"),
    ("GET", "/version"),
    ("GET", "/ping"),
}


def _registered():
    from aiohttp import web
    from routes import setup_routes
    app = web.Application()
    setup_routes(app)
    return {(r.method, r.resource.canonical) for r in app.router.routes()}


def test_all_expected_routes_present():
    missing = EXPECTED - _registered()
    assert not missing, f"загублені маршрути: {sorted(missing)}"


def test_every_api_route_has_options():
    """Без OPTIONS браузер не пройде preflight — фронт побачить помилку CORS."""
    routes = _registered()
    paths = {p for m, p in routes if p.startswith("/api/") or p == "/version"}
    missing = [p for p in paths if ("OPTIONS", p) not in routes]
    assert not missing, f"немає OPTIONS для: {sorted(missing)}"


def test_no_duplicate_registrations():
    from routes import ROUTES
    pairs = [(m, p) for m, p, _ in ROUTES]
    assert len(pairs) == len(set(pairs)), "маршрут зареєстровано двічі"


def test_handlers_are_callable():
    from routes import ROUTES
    for method, path, handler in ROUTES:
        assert callable(handler), f"{method} {path}: хендлер не викликається"


@pytest.mark.parametrize("module", [
    "core", "http_utils", "webauth", "sanitize",
    "api_orders", "api_admin", "api_public", "api_drafts", "api_login",
    "bot_handlers", "routes",
])
def test_module_imports(module):
    """Імітація старту на Render: кожен модуль мусить імпортуватись сам по собі.
    Саме тут ловиться «забув закомітити файл» — історичний режим відмови."""
    __import__(module)
