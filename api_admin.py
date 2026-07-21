"""Адмінська частина кабінету: доступи, інвайти, редактор прайсу, статистика.

Усе тут вимагає ROLE_ADMIN. Перевірка стоїть першим рядком кожного
хендлера — свідомо повторюється, а не ховається в декоратор: коли право
доступу видно прямо в тілі функції, його важче випадково загубити.
"""
import asyncio
import logging

from aiohttp import web

from http_utils import cors
from security import (ROLE_ADMIN, ROLE_MANAGER, MASTER_ADMIN_ID,
                      get_all_authorized_users, remove_authorized_user,
                      create_invite)
from storage import (PRICES_EDITABLE, async_list_prices, async_upsert_prices,
                     async_list_orders, async_log_action)
from webauth import auth_request


@cors
async def api_admin_users(request):
    """Список доступів. Тільки для адміна."""
    uid, role = auth_request(request)
    if role != ROLE_ADMIN:
        return web.json_response({"error": "forbidden"}, status=403)
    users = get_all_authorized_users(force_refresh=True)
    out = [{"user_id": u, "name": i.get("name", ""), "username": i.get("username", ""),
            "role": i.get("role", ROLE_MANAGER), "is_master": str(u) == str(MASTER_ADMIN_ID)}
           for u, i in users.items()]
    return web.json_response({"users": out})


@cors
async def api_admin_invite(request):
    """Створення одноразового коду для нового менеджера. Тільки адмін."""
    uid, role = auth_request(request)
    if role != ROLE_ADMIN:
        return web.json_response({"error": "forbidden"}, status=403)
    code = await asyncio.to_thread(create_invite, uid, ROLE_MANAGER)
    if not code:
        return web.json_response({"error": "create_failed"}, status=500)
    await async_log_action(f"web:{uid}", "🎟 Створив код доступу для менеджера")
    return web.json_response({"code": code, "ttl_days": 7})


@cors
async def api_admin_revoke(request):
    """Відкликання доступу. Майстер-адміна забрати не можна."""
    uid, role = auth_request(request)
    if role != ROLE_ADMIN:
        return web.json_response({"error": "forbidden"}, status=403)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "bad_json"}, status=400)
    target = str(data.get("user_id") or "")
    if not target or target == str(MASTER_ADMIN_ID):
        return web.json_response({"error": "forbidden"}, status=403)
    await asyncio.to_thread(remove_authorized_user, target)
    await async_log_action(f"web:{uid}", f"⛔️ Відкликав доступ у {target}")
    return web.json_response({"success": True})


@cors
async def api_admin_prices(request):
    """Прайс для редактора. Тільки адмін.

    Віддає ВСІ позиції калькулятора, а не лише збережені в БД: якщо позиції
    ще немає в таблиці prices, повертається значення з коду з прапорцем
    saved=false. Інакше редактор показував би порожній список на свіжій базі.
    """
    uid, role = auth_request(request)
    if role != ROLE_ADMIN:
        return web.json_response({"error": "forbidden"}, status=403)
    if not PRICES_EDITABLE:
        return web.json_response(
            {"error": "read_only",
             "message": "Ціни зараз беруться з Google-таблиці. Редагувати їх "
                        "можна там, або перемкнути PRICES_BACKEND=postgres."},
            status=409)
    try:
        items = await async_list_prices()
    except Exception as e:
        logging.error("Не вдалося віддати прайс редактору: %s", e)
        return web.json_response({"error": "read_failed"}, status=500)
    return web.json_response({"prices": items, "editable": True})


@cors
async def api_admin_prices_save(request):
    """Збереження змінених позицій. Тільки адмін.

    Фронт надсилає ЛИШЕ змінені рядки — так у журналі видно, що саме правили,
    і випадковий «зберегти все» не переписує 83 позиції з тими самими числами.
    """
    uid, role = auth_request(request)
    if role != ROLE_ADMIN:
        return web.json_response({"error": "forbidden"}, status=403)
    if not PRICES_EDITABLE:
        return web.json_response({"error": "read_only"}, status=409)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "bad_json"}, status=400)

    items = data.get("items") or []
    if not isinstance(items, list) or not items:
        return web.json_response({"error": "empty"}, status=400)
    if len(items) > 500:
        return web.json_response({"error": "too_many"}, status=400)

    try:
        n = await async_upsert_prices(items, updated_by=str(uid))
    except ValueError as e:
        # Осмислена помилка валідації — показуємо людині як є.
        return web.json_response({"error": "invalid", "message": str(e)}, status=400)
    except Exception as e:
        logging.error("Не вдалося зберегти прайс: %s", e)
        return web.json_response({"error": "save_failed"}, status=500)

    names = ", ".join(str(i.get("key", "")) for i in items[:5])
    if len(items) > 5:
        names += f" +{len(items) - 5}"
    info = get_all_authorized_users().get(str(uid), {})
    await async_log_action(info.get("name", f"web:{uid}"),
                           f"💰 Змінив прайс ({n} поз.): {names}")
    return web.json_response({"success": True, "saved": n})


@cors
async def api_admin_stats(request):
    """Зріз по джерелах і менеджерах."""
    uid, role = auth_request(request)
    if role != ROLE_ADMIN:
        return web.json_response({"error": "forbidden"}, status=403)
    orders = await async_list_orders(uid, ROLE_ADMIN)
    users = get_all_authorized_users()
    by_mgr = {}
    for o in orders:
        if not o["manager_id"]:
            continue
        nm = users.get(o["manager_id"], {}).get("name") or f"Менеджер #{o['manager_id']}"
        by_mgr[nm] = by_mgr.get(nm, 0) + 1
    return web.json_response({
        "total": len(orders),
        "web_leads": sum(1 for o in orders if o["source"] == "web"),
        "by_manager": by_mgr,
    })
