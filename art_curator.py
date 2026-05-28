import os
import asyncio
import google.generativeai as genai
from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command

# Імпортуємо вашу систему безпеки
from security import is_authorized

art_router = Router()

# Налаштування Gemini (беремо той самий ключ, що і в main.py)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    # Використовуємо швидку і легку модель
    model = genai.GenerativeModel('gemini-2.5-flash-lite')
else:
    model = None

def load_wiki_context():
    """Зчитує всі .md файли з папки wiki у єдиний текст"""
    wiki_path = "wiki"
    context = ""
    
    if not os.path.exists(wiki_path):
        return "Помилка: Папка /wiki не знайдена."
        
    for root, dirs, files in os.walk(wiki_path):
        for file in files:
            if file.endswith(".md"):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        context += f"\n--- Файл: {file} ---\n"
                        context += f.read() + "\n"
                except Exception as e:
                    print(f"Помилка читання {file}: {e}")
                    
    return context

@art_router.message(Command("art"))
async def handle_art_query(message: Message):
    # Перевірка доступу через security.py
    if not is_authorized(message.from_user.id):
        return await message.answer("⛔️ У вас немає доступу до цієї команди.")

    if not model:
        return await message.answer("❌ API ключ Gemini не знайдено на сервері.")

    query = message.text.replace("/art", "").strip()
    if not query:
        return await message.answer(
            "🎨 **ШІ-Куратор Картин**\n\n"
            "Напишіть запит після команди. Наприклад:\n"
            "`/art картина в стилі лофт до 5000 грн`", 
            parse_mode="Markdown"
        )
        
    wait_msg = await message.answer("⏳ Читаю базу Obsidian та підбираю варіанти...")
    
    # Читаємо файли асинхронно, щоб бот не "зависав" для інших користувачів
    wiki_context = await asyncio.to_thread(load_wiki_context)
    
    prompt = f"""Ти — професійний куратор картин для будівельно-дизайнерської компанії.
Твоя мета — підібрати найкращі варіанти для дизайнера, спираючись ВИКЛЮЧНО на надану базу знань.

БАЗА ЗНАНЬ (Експорт з Obsidian):
{wiki_context}

ЗАПИТ ДИЗАЙНЕРА:
{query}

ПРАВИЛА ВІДПОВІДІ:
1. Використовуй ТІЛЬКИ картини з бази знань. Не вигадуй неіснуючі. Якщо нічого не підходить, чесно скажи про це.
2. Для кожного варіанту вкажи: Назву, Розмір, Ціну та аргументуй, чому вона підходить під запит.
3. Форматуй текст красиво для Telegram."""

    try:
        # Асинхронний запит до Gemini
        response = await model.generate_content_async(prompt)
        await wait_msg.edit_text(response.text, parse_mode="Markdown")
        
    except Exception as e:
        await wait_msg.edit_text(f"❌ Помилка звернення до Gemini: {e}")