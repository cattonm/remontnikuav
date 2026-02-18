from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Функція для створення клавіатури зі списком заявок
async def get_applications_keyboard(sh):
    worksheet = sh.sheet1
    # Отримуємо всі записи (припускаємо, що 1 рядок - це заголовки)
    all_values = worksheet.get_all_values()
    data = all_values[1:]  # пропускаємо заголовок
    
    builder = InlineKeyboardBuilder()
    
    # Якщо заявок немає
    if not data:
        return None

    # Проходимось по списку. 
    # i + 2, тому що в gspread рядки з 1, і 1-й рядок це заголовок
    for i, row in enumerate(data):
        row_id = i + 2 
        name = row[0] # Припускаємо, що ім'я в першій колонці (А)
        phone = row[1] # Припускаємо, що телефон в другій (B)
        
        # Кнопка: "Ім'я | Телефон" -> callback="view_row_номеррядка"
        text = f"{name} | {phone}"
        builder.button(text=text, callback_data=f"view_{row_id}")

    builder.adjust(1) # Кнопки в один стовпчик
    return builder.as_markup()

# Клавіатура дій для конкретної заявки
def get_action_keyboard(row_id):
    builder = InlineKeyboardBuilder()
    builder.button(text="🧮 Обрахувати", callback_data=f"calc_{row_id}")
    builder.button(text="🗑 Видалити", callback_data=f"del_{row_id}")
    builder.button(text="🔙 Назад до списку", callback_data="show_list")
    builder.adjust(1)
    return builder.as_markup()
