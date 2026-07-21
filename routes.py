"""Таблиця маршрутів API.

Було: 60 рядків app.router.add_* у main(), де кожен ендпоінт займав два
рядки (метод + OPTIONS для CORS). Легко було додати GET і забути OPTIONS —
і фронтенд отримував загадкову помилку CORS замість відповіді.

Стало: список кортежів (метод, шлях, хендлер). OPTIONS реєструється
автоматично для кожного маршруту — забути неможливо.
"""
import api_admin
import api_drafts
import api_login
import api_orders
import api_public

# (метод, шлях, хендлер)
ROUTES = [
    # --- заявки ---
    ("GET",  "/api/get_order",        api_orders.api_get_order),
    ("POST", "/api/save_order",       api_orders.api_save_order),
    ("POST", "/api/create_order",     api_orders.api_create_order),
    ("GET",  "/api/orders",           api_orders.api_orders),
    ("GET",  "/api/order_detail",     api_orders.api_order_detail),
    ("GET",  "/api/order_pdf",        api_orders.api_order_pdf),
    ("POST", "/api/generate_report",  api_orders.api_generate_report),
    # --- кошик ---
    ("POST", "/api/order_delete",     api_orders.api_order_delete),
    ("POST", "/api/order_restore",    api_orders.api_order_restore),
    ("GET",  "/api/trash",            api_orders.api_trash),
    ("POST", "/api/purge",            api_orders.api_purge),
    # --- вхід у кабінет ---
    ("POST", "/api/login_start",      api_login.api_login_start),
    ("GET",  "/api/login_poll",       api_login.api_login_poll),
    ("POST", "/api/login",            api_login.api_login),
    # --- адмінка ---
    ("GET",  "/api/admin/users",      api_admin.api_admin_users),
    ("POST", "/api/admin/invite",     api_admin.api_admin_invite),
    ("POST", "/api/admin/revoke",     api_admin.api_admin_revoke),
    ("GET",  "/api/admin/prices",     api_admin.api_admin_prices),
    ("POST", "/api/admin/prices/save", api_admin.api_admin_prices_save),
    ("GET",  "/api/admin/stats",      api_admin.api_admin_stats),
    # --- серверні чернетки ---
    ("POST", "/api/save_draft",       api_drafts.api_save_draft),
    ("GET",  "/api/get_draft",        api_drafts.api_get_draft),
    ("POST", "/api/delete_draft",     api_drafts.api_delete_draft),
    # --- публічне ---
    ("GET",  "/api/me",               api_public.api_me),
    ("POST", "/api/submit_lead",      api_public.api_submit_lead),
    ("POST", "/api/live_calc",        api_public.api_live_calc),
    # --- діагностика ---
    ("GET",  "/version",              api_public.api_version),
]


def setup_routes(app):
    """Реєструє всі маршрути + OPTIONS до кожного (preflight CORS)."""
    seen = set()
    for method, path, handler in ROUTES:
        app.router.add_route(method, path, handler)
        if path not in seen:
            app.router.add_options(path, handler)
            seen.add(path)
    # /ping навмисно без CORS-preflight: його смикає зовнішній пінгер, не браузер.
    app.router.add_get("/ping", api_public.api_ping)
    return len(ROUTES)
