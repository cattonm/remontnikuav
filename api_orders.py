"""Заявки: список, картка, редагування, кошик, PDF-кошторис, генерація ТЗ.

ВИДАЛЕННЯ ЗАЯВОК — два рівні:
1) КОШИК (soft delete) — заявка зникає зі списку, але фізично лишається:
   її можна відновити. Так працює будь-яка адекватна CRM, бо випадковий
   клік не має знищувати контакт клієнта назавжди.
2) ОСТАТОЧНА ЧИСТКА (hard delete) — рядок видаляється фізично. Доступна
   ЛИШЕ адміну і лише для того, що вже лежить у кошику.
"""
import asyncio
import html
import json
import logging
import time
from datetime import datetime, timedelta

from aiohttp import web

from calculator import calculate_budget, apply_virtual_measurements
from core import LOCKS, bot, model, notify_admin_about_error
from http_utils import (cors, rate_limited, too_many,
                        REPORT_MAX_PER_HOUR, PDF_MAX_PER_HOUR)
from lexicon import GEMINI_PROMPT
from sanitize import sanitize_report_html
from security import ROLE_ADMIN, get_all_authorized_users
from storage import (async_get_row_data, async_update_row, async_save_to_sheet,
                     async_log_action, async_soft_delete, async_purge_rows,
                     async_list_trash, async_list_orders, async_save_report,
                     async_get_prices, get_price_labels, get_tenant_branding,
                     _row_meta)
from webauth import auth_request


def _can_touch(role, meta, uid):
    """Менеджер бачить лише свої заявки і вільні ліди з сайту. Адмін — усе."""
    if role == ROLE_ADMIN:
        return True
    mine = meta["manager_id"] == str(uid)
    free_lead = (meta["source"] == "web" and not meta["manager_id"])
    return mine or free_lead


async def clean_locks_periodically():
    while True:
        await asyncio.sleep(60)
        now = time.time()
        for rid in [r for r, lock in LOCKS.items() if lock["expires"] < now]:
            LOCKS.pop(rid, None)


@cors
async def api_get_order(request):
    """Анкета для редагування + м'яке блокування на 10 хвилин."""
    uid, role = auth_request(request)
    if not uid:
        return web.json_response({"error": "unauthorized"}, status=401)

    row_id = request.rel_url.query.get('edit_id')
    if not row_id:
        return web.json_response({"error": "No ID"}, status=400)

    now = time.time()
    if str(row_id) in LOCKS:
        lock = LOCKS[str(row_id)]
        if lock["expires"] > now and lock["user_id"] != uid:
            return web.json_response(
                {"error": f"🔒 Цю заявку зараз редагує {lock['user_name']}!"}, status=423)

    row_data = await async_get_row_data(int(row_id))
    if not row_data:
        return web.json_response({"error": "Not found"}, status=404)

    # ПРАВА: перевіряємо ДО того, як віддати вміст анкети. Раніше блокування
    # ставилось і дані віддавались будь-кому авторизованому — менеджер міг
    # прочитати чужу заявку, підставивши edit_id.
    if not _can_touch(role, _row_meta(row_data), uid):
        return web.json_response({"error": "forbidden"}, status=403)

    auth_users = get_all_authorized_users()
    LOCKS[str(row_id)] = {"user_id": uid,
                          "user_name": auth_users.get(str(uid), {}).get("name", "Колега"),
                          "expires": now + 600}

    try:
        return web.json_response(json.loads(row_data[5]))
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


@cors
async def api_save_order(request):
    """Збереження відредагованої заявки."""
    user_id, role = auth_request(request)
    if not user_id:
        return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        data = await request.json()
        edit_id = data.get("edit_id")
        if not edit_id:
            return web.json_response({"error": "No edit_id"}, status=400)

        manager_name = get_all_authorized_users().get(str(user_id), {}).get("name", f"ID: {user_id}")
        existing = await async_get_row_data(int(edit_id))
        if not existing:
            return web.json_response({"error": "Row not found"}, status=404)
        if not _can_touch(role, _row_meta(existing), user_id):
            return web.json_response({"error": "forbidden"}, status=403)

        success, error_msg = await async_update_row(int(edit_id), data)
        if not success:
            await notify_admin_about_error(f"Оновлення заявки (ID: {edit_id})", error_msg)
            return web.json_response({"error": "Update failed"}, status=500)

        LOCKS.pop(str(edit_id), None)
        await async_log_action(manager_name, f"✏️ Відредагував об'єкт (Рядок {edit_id})")
        try:
            await bot.send_message(chat_id=user_id,
                                   text=f"✅ **Заявку оновлено!** (Рядок {edit_id})",
                                   parse_mode="Markdown")
        except Exception:
            pass
        return web.json_response({"success": True})
    except Exception as e:
        await notify_admin_about_error("API Збереження (Загальна помилка)", e)
        return web.json_response({"error": "server_error"}, status=500)


@cors
async def api_orders(request):
    """Список заявок для кабінету (з пошуком і пагінацією).
    Свідомо НЕ віддаємо тут повний JSON анкети й не рахуємо кошториси —
    інакше кожне відкриття списку тягнуло б мегабайти й тисячі множень."""
    uid, role = auth_request(request)
    if not uid:
        return web.json_response({"error": "unauthorized"}, status=401)
    query = request.query.get("q") or None
    try:
        limit = max(1, min(int(request.query.get("limit", 20)), 100))
        offset = max(0, int(request.query.get("offset", 0)))
    except ValueError:
        limit, offset = 20, 0

    orders = await async_list_orders(uid, role, None, query)
    users = get_all_authorized_users()
    page = orders[offset:offset + limit]
    for o in page:
        o["manager_name"] = users.get(o["manager_id"], {}).get("name", "") if o["manager_id"] else ""

    return web.json_response({
        "orders": page,
        "total": len(orders),
        "role": role,
        "has_more": offset + limit < len(orders),
    })


async def _load_order_bundle(request):
    """Заявка + кошторис + розбивка по кімнатах, із перевіркою прав.

    Спільний код для картки заявки і для PDF: обидва показують те саме,
    тож збирати дані двічі означало б гарантовано їх розсинхронити.

    Повертає (payload, помилка). Помилка — готова відповідь, якщо доступу
    немає або заявки не існує.

    ⚠️ ПЕРЕВІРЯТИ ЛИШЕ ЯК `if err is not None`. Об'єкт web.Response — це
    MutableMapping (у нього є словник стану запиту), тому `len(response)`
    дорівнює нулю і `if err:` ЗАВЖДИ хибне. Через це відповіді 401/403/404
    мовчки пропускались, і клієнт замість «немає доступу» отримував 500.
    """
    uid, role = auth_request(request)
    if not uid:
        return None, web.json_response({"error": "unauthorized"}, status=401)
    try:
        row_id = int(request.query.get("row"))
    except (TypeError, ValueError):
        return None, web.json_response({"error": "bad_row"}, status=400)

    row = await async_get_row_data(row_id)
    if not row:
        return None, web.json_response({"error": "not_found"}, status=404)
    m = _row_meta(row)

    if not _can_touch(role, m, uid):
        return None, web.json_response({"error": "forbidden"}, status=403)

    budget = None
    rooms = []
    try:
        payload = json.loads(row[5])
        prices = await async_get_prices()
        b = calculate_budget(apply_virtual_measurements(payload), prices, labels=get_price_labels())
        rc = b.get("room_costs") or {}
        for r in (payload.get("answers") or {}).get("rooms") or []:
            c = rc.get(r.get("id")) or [0, 0, 0]
            rooms.append({
                "name": r.get("name"),
                "area": (r.get("measurements") or {}).get("floor"),
                "work": round(c[0]), "mat_min": round(c[1]),
                "lines": (b.get("room_lines") or {}).get(r.get("id"), []),
            })
        budget = {
            "work": round(b["total_work"]),
            "mat_min": round(b["total_mat_min"]),
            "mat_max": round(b["total_mat_max"]),
            "total": round(b["total_work"] + b["total_mat_min"]),
            "general_lines": b.get("general_lines") or [],
        }
    except Exception:
        logging.exception("не вдалося порахувати кошторис для рядка %s", row_id)

    m["manager_name"] = get_all_authorized_users().get(m["manager_id"], {}).get("name", "") if m["manager_id"] else ""
    # Санітизація на ЧИТАННІ теж: у базі можуть лежати звіти, збережені до
    # появи очищення. Інакше старий звіт із небезпечним тегом жив би вічно.
    m["report"] = sanitize_report_html(row[6]) if len(row) > 6 else ""
    return {"row_id": row_id, "order": m, "budget": budget, "rooms": rooms, "uid": uid, "role": role}, None


@cors
async def api_order_detail(request):
    """Повна заявка + порахований кошторис. Викликається лише коли менеджер
    розгортає конкретну картку — тож важкий JSON читаємо точково."""
    data, err = await _load_order_bundle(request)
    if err is not None:
        return err
    return web.json_response({"order": data["order"], "budget": data["budget"],
                              "rooms": data["rooms"]})


@cors
async def api_order_pdf(request):
    """Кошторис у PDF — те, що менеджер надсилає клієнту.

    Генерація в потоці: reportlab синхронний, а на великій заявці це
    десятки мілісекунд, які не варто відбирати в циклу подій.
    """
    data, err = await _load_order_bundle(request)
    if err is not None:
        return err
    if rate_limited(data["uid"], "pdf", PDF_MAX_PER_HOUR):
        return too_many()
    if not data["budget"]:
        return web.json_response(
            {"error": "no_budget",
             "message": "Кошторис порахувати не вдалося — перевірте анкету."}, status=422)

    try:
        from pdf_estimate import build_estimate_pdf
        branding = await asyncio.to_thread(get_tenant_branding)
        pdf = await asyncio.to_thread(build_estimate_pdf, data["order"],
                                      data["budget"], data["rooms"], branding)
    except Exception:
        logging.exception("не вдалося зібрати PDF для рядка %s", data["row_id"])
        return web.json_response({"error": "pdf_failed"}, status=500)

    client = (data["order"].get("name") or "obiekt").strip()
    safe = "".join(ch for ch in client if ch.isalnum() or ch in " -_")[:40].strip() or "obiekt"
    return web.Response(
        body=pdf, content_type="application/pdf",
        headers={
            # inline — щоб на телефоні відкрилось у переглядачі, а не пішло
            # одразу в «Завантаження», звідки його ще треба шукати.
            "Content-Disposition": f'inline; filename="koshtorys-{data["row_id"]}.pdf"',
            "X-Client-Name": safe,
        })


@cors
async def api_generate_report(request):
    """Генерація ТЗ через Gemini для заявки.
    Ті самі права, що й на перегляд деталей; результат зберігаємо в заявці."""
    uid, role = auth_request(request)
    if not uid:
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await request.json()
        row_id = int(body.get("row"))
    except Exception:
        return web.json_response({"error": "bad_row"}, status=400)

    row = await async_get_row_data(row_id)
    if not row:
        return web.json_response({"error": "not_found"}, status=404)
    if not _can_touch(role, _row_meta(row), uid):
        return web.json_response({"error": "forbidden"}, status=403)

    if model is None:
        return web.json_response({"error": "gemini_unavailable"}, status=503)

    # Ліміт по КОРИСТУВАЧУ, а не по IP: кожен виклик — платний запит до Gemini,
    # а в менеджера з телефона IP змінюється при переході між мережами.
    if rate_limited(str(uid), "report", REPORT_MAX_PER_HOUR):
        return too_many()

    raw_answers = row[5]
    report_text = ""
    for attempt in range(3):
        try:
            resp = await model.generate_content_async(GEMINI_PROMPT.format(raw_answers=raw_answers))
            report_text = resp.text.replace("```html", "").replace("```", "").strip()
            break
        except Exception:
            if attempt == 2:
                logging.exception("api_generate_report: Gemini не відповів для рядка %s", row_id)
                return web.json_response({"error": "generation_failed"}, status=502)
            await asyncio.sleep(1)

    # Очищаємо ПЕРЕД збереженням: у базі має лежати вже безпечний HTML.
    report_text = sanitize_report_html(report_text)
    await async_save_report(row_id, report_text)
    info = get_all_authorized_users().get(str(uid), {})
    await async_log_action(info.get("name", f"web:{uid}"), f"✨ Згенерував ТЗ (Рядок {row_id})")
    return web.json_response({"report": report_text})


@cors
async def api_order_delete(request):
    """У КОШИК (м'яке видалення). Дані не знищуються."""
    uid, role = auth_request(request)
    if not uid:
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        row_id = int((await request.json()).get("row"))
    except (TypeError, ValueError):
        return web.json_response({"error": "bad_row"}, status=400)

    raw = await async_get_row_data(row_id)
    if not raw:
        return web.json_response({"error": "not_found"}, status=404)
    m = _row_meta(raw)
    if not _can_touch(role, m, uid):
        return web.json_response({"error": "forbidden"}, status=403)

    await async_soft_delete(row_id, True)
    await async_log_action(f"web:{uid}", f"🗑 У кошик: заявка {row_id} ({m['name']})")
    return web.json_response({"success": True})


@cors
async def api_order_restore(request):
    """Повернути заявку з кошика."""
    uid, role = auth_request(request)
    if not uid:
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        row_id = int((await request.json()).get("row"))
    except (TypeError, ValueError):
        return web.json_response({"error": "bad_row"}, status=400)
    raw = await async_get_row_data(row_id)
    if not raw:
        return web.json_response({"error": "not_found"}, status=404)
    m = _row_meta(raw)
    if role != ROLE_ADMIN and m["manager_id"] != str(uid):
        return web.json_response({"error": "forbidden"}, status=403)
    await async_soft_delete(row_id, False)
    await async_log_action(f"web:{uid}", f"♻️ Відновлено заявку {row_id}")
    return web.json_response({"success": True})


@cors
async def api_trash(request):
    """Вміст кошика."""
    uid, role = auth_request(request)
    if not uid:
        return web.json_response({"error": "unauthorized"}, status=401)
    items = await async_list_trash(role, uid)
    users = get_all_authorized_users()
    for m in items:
        m["manager_name"] = users.get(m["manager_id"], {}).get("name", "") if m["manager_id"] else ""
    return web.json_response({"orders": items, "role": role})


@cors
async def api_purge(request):
    """ОСТАТОЧНЕ видалення — тільки адмін і тільки з кошика.
    Приймає або список рядків, або older_than_days (авточистка).
    Захист: усе, що не позначене «видалена», ігнорується — тобто активну
    заявку неможливо знищити цим ендпоінтом навіть навмисно."""
    uid, role = auth_request(request)
    if role != ROLE_ADMIN:
        return web.json_response({"error": "forbidden"}, status=403)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "bad_json"}, status=400)

    trash = await async_list_trash(ROLE_ADMIN, uid)
    trash_rows = {m["row"]: m for m in trash}

    if data.get("older_than_days"):
        try:
            days = int(data["older_than_days"])
        except (TypeError, ValueError):
            return web.json_response({"error": "bad_days"}, status=400)
        limit_dt = datetime.now() - timedelta(days=days)
        targets = []
        for r, m in trash_rows.items():
            try:
                # У колонці A формат "YYYY-MM-DD HH:MM" (+ можливий суфікс)
                dt = datetime.strptime(m["date"][:16], "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            if dt < limit_dt:
                targets.append(r)
    else:
        requested = data.get("rows") or []
        try:
            targets = [int(r) for r in requested if int(r) in trash_rows]
        except (TypeError, ValueError):
            return web.json_response({"error": "bad_rows"}, status=400)

    if not targets:
        return web.json_response({"success": True, "deleted": 0})

    deleted = await async_purge_rows(targets)
    await async_log_action(f"web:{uid}", f"🔥 ОСТАТОЧНО видалено заявок: {deleted}")
    return web.json_response({"success": True, "deleted": deleted})


@cors
async def api_create_order(request):
    """Нова заявка з ВЕБ-кабінету. У міні-апці заявка йде через tg.sendData
    (бот ловить web_app_data), але в браузері такого каналу немає — тож
    менеджер з веб-сесією зберігає її сюди. Авторство підписуємо з сесії,
    а не з тіла запиту: підмінити чужий manager_id неможливо."""
    uid, role = auth_request(request)
    if not uid:
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        data = await request.json()
        payload = {"client": data.get("client") or {},
                   "answers": data.get("answers") or {},
                   "manager_id": str(uid), "source": "manager",
                   "submission_id": data.get("submission_id")}   # захист від дублю
        success, err = await async_save_to_sheet(payload)
        if not success:
            await notify_admin_about_error("Заявка з веб-кабінету", err)
            return web.json_response({"error": "save_failed"}, status=500)
        name = (payload["client"] or {}).get("name", "")
        await async_log_action(f"web:{uid}", f"🆕 СТВОРИВ нову заявку: {name}")
        try:
            await bot.send_message(chat_id=int(uid),
                                   text=f"✅ *Заявку прийнято* (з веб-кабінету)\n👤 {html.escape(str(name))}",
                                   parse_mode="Markdown")
        except Exception:
            pass
        return web.json_response({"success": True})
    except Exception:
        logging.exception("create_order failed")
        return web.json_response({"error": "server_error"}, status=500)
