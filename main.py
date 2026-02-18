import asyncio
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- КОНФІГУРАЦІЯ ---
BOT_TOKEN = "8264242241:AAFCkITlx-nUPLb4IczYaUWnyma0d_-WZ04" # Встав сюди свій токен
SPREADSHEET_NAME = "remonts sheets"  # Точна назва таблиці в Google Sheets

# Налаштування логування
logging.basicConfig(level=logging.INFO)

# --- ПІДКЛЮЧЕННЯ ДО GOOGLE SHEETS ---
def get_google_sheet():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    client = gspread.authorize(creds)
    sheet = client.open(SPREADSHEET_NAME).sheet1  # Відкриваємо перший аркуш
    return sheet

# --- КЛАВІАТУРИ ---

# 1. Головне меню
def main_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Список заявок", callback_data="show_list")
    return builder.as_markup()

# 2. Клавіатура зі списком клієнтів (генерується динамічно)
def get_applications_keyboard(worksheet):
    # Отримуємо всі записи. Припускаємо, що 1-й рядок - це заголовки
    all_values = worksheet.get_all_values()
    
    # Якщо тільки заголовок або пусто
    if len(all_values) <= 1:
        return None

    data = all_values[1:]  # Пропускаємо заголовок (рядок 1)
    builder = InlineKeyboardBuilder()

    # Проходимось по списку
    for i, row in enumerate(data):
        # row_id в gspread починається з 1.
        # 1-й рядок - заголовок, тому дані починаються з 2-го.
        real_row_id = i + 2 
        
        # ПРИПУЩЕННЯ: Колонка A (0) - Ім'я, Колонка B (1) - Телефон
        name = row[0] if len(row) > 0 else "Без імені"
        phone = row[1] if len(row) > 1 else "Без телефону"
        
        text = f"{name} | {phone}"
        builder.button(text=text, callback_data=f"view_{real_row_id}")

    builder.adjust(1) # Кнопки в один стовпчик
    
    # Кнопка оновлення списку
    builder.button(text="🔄 Оновити список", callback_data="show_list")
    return builder.as_markup()

# 3. Клавіатура дій для конкретної заявки
def get_action_keyboard(row_id):
    builder = InlineKeyboardBuilder()
    builder.button(text="🧮 Обрахувати", callback_data=f"calc_{row_id}")
    builder.button(text="🗑 Видалити заявку", callback_data=f"del_{row_id}")
    builder.button(text="🔙 Назад до списку", callback_data="show_list")
    builder.adjust(1)
    return builder.as_markup()

# --- ХЕНДЛЕРИ (ОБРОБНИКИ) ---
router = Router()

@router.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привіт! Я менеджер заявок. Натисни кнопку, щоб побачити список.",
        reply_markup=main_menu_keyboard()
    )

# --- 1. ПОКАЗ СПИСКУ ЗАЯВОК ---
@router.callback_query(F.data == "show_list")
async def show_requests_list(callback: types.CallbackQuery):
    try:
        sh = get_google_sheet()
        keyboard = get_applications_keyboard(sh)
        
        if keyboard:
            await callback.message.edit_text("Оберіть заявку зі списку:", reply_markup=keyboard)
        else:
            await callback.message.edit_text(
                "Список заявок порожній або сталася помилка.",
                reply_markup=main_menu_keyboard()
            )
    except Exception as e:
        await callback.message.answer(f"Помилка доступу до таблиці: {e}")

# --- 2. ПЕРЕГЛЯД КОНКРЕТНОЇ ЗАЯВКИ (ЗВІТ) ---
@router.callback_query(F.data.startswith("view_"))
async def view_application(callback: types.CallbackQuery):
    row_id = int(callback.data.split("_")[1])
    
    try:
        sh = get_google_sheet()
        # Отримуємо дані конкретного рядка
        row_data = sh.row_values(row_id)
        
        # --- НАЛАШТУВАННЯ КОЛОНОК (ЗМІНИ ЦІ ЦИФРИ ПІД СВОЮ ТАБЛИЦЮ) ---
        # 0 = А, 1 = B, 2 = C, 3 = D і т.д.
        client_name = row_data[0] if len(row_data) > 0 else "—"
        phone = row_data[1] if len(row_data) > 1 else "—"
        address = row_data[2] if len(row_data) > 2 else "—"  # Вулиця має бути тут
        details = row_data[3] if len(row_data) > 3 else "—"  # Додаткові деталі

        # Формуємо ЗВІТ (Строго по факту)
        report_text = (
            f"📋 **ЗВІТ ПО ЗАЯВЦІ №{row_id}**\n"
            f"➖➖➖➖➖➖➖➖➖➖\n"
            f"👤 **Клієнт:** {client_name}\n"
            f"📞 **Телефон:** {phone}\n"
            f"🏠 **Адреса:** {address}\n"
            f"📝 **Деталі:** {details}\n"
            f"➖➖➖➖➖➖➖➖➖➖\n"
            f"Оберіть дію:"
        )

        await callback.message.edit_text(
            report_text, 
            reply_markup=get_action_keyboard(row_id),
            parse_mode="Markdown"
        )
    except Exception as e:
        await callback.answer(f"Помилка читання рядка: {e}", show_alert=True)

# --- 3. ВИДАЛЕННЯ ЗАЯВКИ ---
@router.callback_query(F.data.startswith("del_"))
async def delete_application(callback: types.CallbackQuery):
    row_id = int(callback.data.split("_")[1])
    
    try:
        sh = get_google_sheet()
        # Видаляємо рядок фізично
        sh.delete_rows(row_id)
        
        await callback.answer("✅ Заявку успішно видалено!", show_alert=True)
        
        # Повертаємо оновлений список
        keyboard = get_applications_keyboard(sh)
        if keyboard:
            await callback.message.edit_text("Оберіть заявку (список оновлено):", reply_markup=keyboard)
        else:
            await callback.message.edit_text("Список тепер порожній.", reply_markup=main_menu_keyboard())
            
    except Exception as e:
        await callback.answer(f"Не вдалося видалити: {e}", show_alert=True)

# --- 4. ОБРАХУНОК (Заготовка) ---
@router.callback_query(F.data.startswith("calc_"))
async def calculate_application(callback: types.CallbackQuery):
    row_id = int(callback.data.split("_")[1])
    # Тут ти можеш додати логіку для виклику AI або формули
    await callback.answer(f"Тут буде логіка розрахунку для рядка {row_id}", show_alert=True)
    
    # Можна також відправити повідомлення
    await callback.message.answer("🧮 Починаю розрахунок вартості робіт...")

# --- ЗАПУСК БОТА ---
async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    
    print("Бот запущено...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот зупинено")
