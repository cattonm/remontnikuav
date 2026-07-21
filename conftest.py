"""Спільна підготовка для всіх тестів.

pytest завантажує conftest.py ДО тестових модулів, а config.py читає
змінні оточення один раз при імпорті. Тому фейкові значення треба
підставити саме тут — інакше порядок тестів впливав би на результат
(перший імпорт config «зафіксує» порожній BOT_TOKEN, і всі наступні
тести падатимуть на створенні бота).

setdefault, а не пряме присвоєння: у CI справжні значення вже задані
в оточенні джоби, і перетирати їх не можна.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("BOT_TOKEN", "123456789:AAHfakeTokenForTests_00000000000000")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("WEBHOOK_SECRET", "test-webhook-secret")
