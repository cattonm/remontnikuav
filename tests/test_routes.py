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


# ==========================================================
# ЖИТТЄВИЙ ЦИКЛ: startup / shutdown
# ----------------------------------------------------------
# Ці хендлери НЕ виконуються при імпорті — тому помилка в них не видно
# ні лінтеру, ні збірці застосунку. Вона вилазить лише в проді, при
# першому реальному запуску. Саме так деплой упав з
#   TypeError: on_startup() missing 1 required positional argument: '_bot'
# бо параметр назвали _bot, а aiogram підставляє залежності ПО ІМЕНІ.
# ==========================================================

# Імена, які aiogram кладе у workflow_data і може підставити в хендлер.
_INJECTABLE = {"bot", "dispatcher", "dp", "router", "event_router", "workflow_data"}


@pytest.mark.parametrize("fn_name", ["on_startup", "on_shutdown"])
def test_lifecycle_handler_params_are_injectable(fn_name):
    """Кожен параметр мусить або мати ім'я, яке aiogram уміє підставити,
    або мати значення за замовчуванням. Інакше старт впаде в проді."""
    import inspect
    import main

    fn = getattr(main, fn_name)
    for name, p in inspect.signature(fn).parameters.items():
        injectable = name in _INJECTABLE
        has_default = p.default is not inspect.Parameter.empty
        assert injectable or has_default, (
            f"{fn_name}({name}): aiogram не знає, що підставити в цей параметр. "
            f"Назви його одним із {sorted(_INJECTABLE)} або дай значення за замовчуванням."
        )


def test_on_startup_actually_runs():
    """НАЙВАЖЛИВІШИЙ тест цього файлу: реально проганяє startup через
    диспетчер aiogram — так само, як це робить web.run_app у проді.

    Побічні ефекти прибрані: мережевий виклик замінено заглушкою, а три
    фонові цикли — порожньою корутиною. Перевіряємо саме те, що падало:
    чи вміє aiogram викликати наш хендлер.
    """
    import asyncio
    from aiogram import Dispatcher
    import main

    called = []

    class FakeBot:
        async def set_webhook(self, *a, **k):
            called.append("set_webhook")

    async def noop():
        return None

    async def go():
        saved = (main.STORAGE_BACKEND, main.clean_locks_periodically,
                 main.async_get_prices, main.remind_about_drafts_periodically)
        # postgres-гілка не чіпає Google; фонові задачі — заглушки
        main.STORAGE_BACKEND = "postgres"
        main.clean_locks_periodically = noop
        main.async_get_prices = noop
        main.remind_about_drafts_periodically = noop
        try:
            d = Dispatcher()
            d.startup.register(main.on_startup)
            await d.emit_startup(bot=FakeBot())
            await asyncio.sleep(0)      # даємо створеним задачам завершитись
        finally:
            (main.STORAGE_BACKEND, main.clean_locks_periodically,
             main.async_get_prices, main.remind_about_drafts_periodically) = saved

    asyncio.run(go())
    assert called == ["set_webhook"], "startup не дійшов до встановлення вебхука"


def test_on_shutdown_actually_runs():
    """Те саме для shutdown — він теж викликається лише в проді."""
    import asyncio
    from aiogram import Dispatcher
    import main

    closed = []

    class FakeSession:
        async def close(self):
            closed.append("closed")

    class FakeBot:
        session = FakeSession()

    async def go():
        d = Dispatcher()
        d.shutdown.register(main.on_shutdown)
        await d.emit_shutdown(bot=FakeBot())

    asyncio.run(go())
    assert closed == ["closed"]