"""Хендлери Telegram-бота.

Бот СВІДОМО став лаунчером: анкета, заявки, статистика й коди доступу
живуть у веб-кабінеті. Дублювання цих екранів у боті прибрано раніше —
підтримувати два інтерфейси одного й того самого не має сенсу.

Лишилось тільки те, чого в кабінеті бути не може:
  /start        — вхід і прив'язка коду входу на сайт;
  /upd          — розсилка в робочу групу;
  Super#*       — три сервісні команди для власника;
  WEB_APP_DATA  — збереження заявки, надісланої з міні-апки;
  текст-ловець  — активація одноразового коду доступу.
"""
import asyncio
import csv
import html
import io
import json
import logging
from datetime import datetime

from aiogram import F
from aiogram.filters import Command, CommandObject
from aiogram.types import (Message, CallbackQuery, ContentType, BufferedInputFile,
                           ReplyKeyboardMarkup, KeyboardButton, WebAppInfo)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from api_login import bind_login_code
from config import GROUP_CHAT_ID, WEBAPP_URL
from core import bot, dp, notify_admin_about_error
from lexicon import (MSG_START_AUTH, MSG_START_MAIN, MSG_AUTH_SUCCESS,
                     MSG_AUTH_FAIL, MSG_ACCESS_DENIED)
from security import (ADMIN_PASSWORD, MASTER_ADMIN_ID, ROLE_ADMIN,
                      is_authorized, get_all_authorized_users,
                      add_authorized_user, remove_authorized_user,
                      clear_auth_cache, redeem_invite)
from storage import (async_save_to_sheet, async_log_action,
                     invalidate_prices_cache, invalidate_orders_cache,
                     _fetch_orders_rows_sync, _meta_from_parts)


def get_main_menu_keyboard(user_id=None):
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🚀 Відкрити застосунок",
                                  web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True,
    )


@dp.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject = None):
    # DEEP LINK ВХОДУ НА САЙТ: t.me/<bot>?start=web_ABC123
    # Людина натиснула кнопку на сайті — тут ми підтверджуємо, що це справді
    # вона (Telegram гарантує user_id), і прив'язуємо код. Сайт впустить сам.
    payload = (command.args or "") if command else ""
    if payload.startswith("web_"):
        code = payload[4:]
        if not is_authorized(message.from_user.id):
            return await message.answer(
                "🔒 У вас ще немає доступу до кабінету.\n\n"
                "Надішліть мені *код доступу*, який видав адміністратор, "
                "і повторіть вхід на сайті.", parse_mode="Markdown")
        if bind_login_code(code, message.from_user.id):
            return await message.answer(
                "✅ *Вхід підтверджено!*\n\nПоверніться на сайт — кабінет уже відкрито.",
                parse_mode="Markdown", reply_markup=get_main_menu_keyboard(message.from_user.id))
        return await message.answer("⌛️ Код входу застарів. Оновіть сторінку сайту й спробуйте ще раз.")

    if not is_authorized(message.from_user.id):
        return await message.answer(
            MSG_START_AUTH.format(name=message.from_user.first_name),
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True))
    await message.answer(MSG_START_MAIN.format(name=message.from_user.first_name),
                         reply_markup=get_main_menu_keyboard(message.from_user.id),
                         parse_mode="Markdown")


@dp.message(Command("upd"))
async def send_update_to_group(message: Message):
    """Розсилка в робочу групу — з підтримкою фото, відео та документів."""
    if not is_authorized(message.from_user.id):
        return

    # Витягуємо текст незалежно від того, чи це просто текст, чи опис до фото
    full_text = message.text or message.caption or ""
    args = full_text.split(maxsplit=1)
    content_to_send = args[1] if len(args) > 1 else ""

    if not GROUP_CHAT_ID or GROUP_CHAT_ID == "-100XXXXXXXXXX":
        return await message.answer("⚠️ Спочатку вкажіть реальний ID групи "
                                    "у змінній оточення GROUP_CHAT_ID.")

    try:
        if message.photo:
            await bot.send_photo(chat_id=GROUP_CHAT_ID, photo=message.photo[-1].file_id,
                                 caption=content_to_send)
        elif message.video:
            await bot.send_video(chat_id=GROUP_CHAT_ID, video=message.video.file_id,
                                 caption=content_to_send)
        elif message.document:
            await bot.send_document(chat_id=GROUP_CHAT_ID, document=message.document.file_id,
                                    caption=content_to_send)
        else:
            if not content_to_send:
                return await message.answer(
                    "⚠️ Напишіть текст після команди. Формат:\n`/upd Ваш текст тут`\n"
                    "*(Або прикріпіть фото і напишіть команду в описі)*", parse_mode="Markdown")
            await bot.send_message(chat_id=GROUP_CHAT_ID, text=content_to_send)

        await message.answer("✅ Повідомлення успішно відправлено в групу!")
    except Exception as e:
        await message.answer(f"❌ Помилка відправки: {e}")


@dp.message(F.text == "Super#secusers")
async def secret_admin_panel(message: Message):
    try:
        await message.delete()
    except Exception:
        pass
    if message.from_user.id != MASTER_ADMIN_ID:
        return
    auth_data = get_all_authorized_users()
    kb = InlineKeyboardBuilder()
    for uid, info in auth_data.items():
        kb.button(text=f"❌ {info.get('name', '')} (@{info.get('username', '')})",
                  callback_data=f"revoke_{uid}")
    kb.adjust(1)
    await message.answer("🕵️‍♂️ **Секретна панель:**", reply_markup=kb.as_markup(),
                         parse_mode="Markdown")


@dp.message(F.text == "Super#reload_cache")
async def cmd_reload_cache(message: Message):
    """Скидання всіх кешів.

    БУЛО ЗЛАМАНО: команда оголошувала `global _PRICES_CACHE` у main.py, хоча
    кеш цін давно переїхав у storage. Тобто вона створювала дві порожні
    змінні в main і НІЧОГО не скидала, а власник бачив «✅ Кеш очищено» і
    думав, що ціни оновились. Тепер кличемо справжні функції скидання.
    """
    if message.from_user.id != MASTER_ADMIN_ID:
        return
    clear_auth_cache()
    invalidate_prices_cache()
    invalidate_orders_cache()
    await message.answer("🔄 **Кеші очищено:** доступи, ціни, список заявок.",
                         parse_mode="Markdown")


@dp.message(F.text == "Super#backup")
async def cmd_backup(message: Message):
    """Вивантаження контактів заявок у CSV.

    БУЛО ЗЛАМАНО: команда читала Google-аркуш напряму, оминаючи фасад. Після
    переїзду на Postgres вона віддавала застарілий аркуш (а з мертвим ключем
    Google — взагалі нічого). Тепер бере дані з АКТИВНОГО сховища.

    Це резервна копія контактів, а не повний дамп: тіло анкети (JSON) сюди
    не входить свідомо — файл має лишатись придатним для читання людиною.
    Повний бекап бази роблять засобами Postgres.
    """
    if message.from_user.id != MASTER_ADMIN_ID:
        return

    def _get_csv():
        rows = _fetch_orders_rows_sync(include_deleted=True)
        output = io.StringIO()
        w = csv.writer(output)
        w.writerow(["Рядок", "Дата", "Ім'я", "Телефон", "Тип об'єкта", "Адреса",
                    "Статус", "Менеджер", "Джерело"])
        for entry in rows:
            m = _meta_from_parts(entry)
            w.writerow([m["row"], m["date"], m["name"], m["phone"], m["type"],
                        m["address"], m["status"], m["manager_id"], m["source"]])
        # BOM — щоб Excel не зіпсував кирилицю при відкритті
        return ("\ufeff" + output.getvalue()).encode("utf-8"), len(rows)

    try:
        csv_data, n = await asyncio.to_thread(_get_csv)
    except Exception as e:
        logging.exception("backup failed")
        return await message.answer(f"❌ Не вдалося зібрати бекап: {e}")

    await message.answer_document(
        BufferedInputFile(csv_data, filename=f"remont_{datetime.now():%Y_%m_%d_%H%M}.csv"),
        caption=f"📦 Заявок у файлі: {n}")


@dp.callback_query(F.data.startswith("revoke_"))
async def revoke_access(callback: CallbackQuery):
    if callback.from_user.id != MASTER_ADMIN_ID:
        return
    target = callback.data.split("_", 1)[1]
    if str(target) == str(MASTER_ADMIN_ID):
        return await callback.answer("⛔️ Себе відкликати не можна.", show_alert=True)
    if remove_authorized_user(target):
        await callback.answer("✅ Доступ скасовано!", show_alert=True)


@dp.message(F.content_type == ContentType.WEB_APP_DATA)
async def web_app_data_handler(message: Message):
    """Заявка, надіслана з міні-апки через tg.sendData."""
    if not is_authorized(message.from_user.id):
        return await message.answer(MSG_ACCESS_DENIED)
    try:
        data = json.loads(message.web_app_data.data)
    except Exception:
        logging.exception("web_app_data: не вдалося розібрати JSON")
        return await message.answer("⚠️ Дані анкети пошкоджені. Спробуйте ще раз.")
    # Підписуємо заявку автором — саме за цим полем менеджер потім
    # бачить її у «Мої заявки», а адмін знає, чия це робота.
    data["manager_id"] = str(message.from_user.id)
    data["source"] = "manager"
    success, error_msg = await async_save_to_sheet(data)
    if success:
        await message.answer("✅ **Нову заявку прийнято!**", parse_mode="Markdown")
        await async_log_action(message.from_user.full_name,
                               f"🆕 СТВОРИВ нову заявку: {(data.get('client') or {}).get('name', '')}")
    else:
        await notify_admin_about_error(
            f"Збереження заявки від {message.from_user.full_name}", error_msg)
        await message.answer("⚠️ Помилка збереження. Адміністратора повідомлено.")


@dp.message(F.text)
async def process_password_attempts(message: Message):
    """Ловець тексту від НЕавторизованих: одноразовий інвайт-код."""
    if is_authorized(message.from_user.id):
        return

    code = (message.text or "").strip()
    if len(code) == 8 and code.isalnum():
        ok, res = await asyncio.to_thread(
            redeem_invite, code, message.from_user.id,
            message.from_user.full_name, message.from_user.username or "немає")
        if ok:
            try:
                await message.delete()     # прибираємо код із чату
            except Exception:
                pass
            await message.answer(MSG_AUTH_SUCCESS,
                                 reply_markup=get_main_menu_keyboard(message.from_user.id),
                                 parse_mode="Markdown")
            try:
                await bot.send_message(
                    MASTER_ADMIN_ID,
                    f"🟢 <b>НОВИЙ МЕНЕДЖЕР</b>\n{html.escape(message.from_user.full_name)} "
                    f"(@{message.from_user.username or '—'})", parse_mode="HTML")
            except Exception:
                pass
            return
        return await message.answer(f"❌ {res}")

    # Аварійний вхід власника спільним паролем. Працює ЛИШЕ для майстер-адміна
    # і лише якщо ADMIN_PASSWORD реально заданий в оточенні — інакше будь-хто,
    # хто вгадає рядок-заглушку з коду, отримав би права адміна.
    if (ADMIN_PASSWORD and ADMIN_PASSWORD != "SECURE_FALLBACK_ERR_999"
            and message.text == ADMIN_PASSWORD
            and message.from_user.id == MASTER_ADMIN_ID):
        add_authorized_user(message.from_user.id, message.from_user.full_name,
                            message.from_user.username or "немає", ROLE_ADMIN)
        return await message.answer(MSG_AUTH_SUCCESS,
                                    reply_markup=get_main_menu_keyboard(message.from_user.id),
                                    parse_mode="Markdown")

    await message.answer(MSG_AUTH_FAIL)
