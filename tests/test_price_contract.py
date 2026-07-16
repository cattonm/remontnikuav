"""
CI-звірка контракту прайсу (Етап 2).

Ловить клас багів «фантомна ціна»: калькулятор кличе add_c(cat, "КЛЮЧ"),
але "КЛЮЧ" відсутній у DEFAULT_PRICES → _get_price повертає [0,0,0], позиція
тихо коштує 0, помилки немає. Раніше такий розсинхрон виявлявся лише в
клієнта. Тепер — падає в CI.

Перевіряємо ОБИДВА напрями:
  • кожен ключ, ужитий у add_c(), існує в DEFAULT_PRICES і в PRICE_META
    (інакше — фантомна ціна або технічний ID замість людської назви);
  • (наявний test_price_meta_covers_all_keys_used стереже зворотній бік:
    кожен ключ прайсу має назву).

Аналіз статичний (ast) — не запускає бота, не ходить у Google, тож працює
в CI без секретів.
"""
import ast
import os

from calculator import PRICE_META
from config import DEFAULT_PRICES

_CALC_PATH = os.path.join(os.path.dirname(__file__), "..", "calculator.py")


def _keys_used_in_add_c():
    """Дістаємо другий позиційний аргумент кожного виклику add_c(...),
    якщо це рядковий літерал. Виклики зі змінним ключем повертаємо окремо,
    щоб про них знати (їх статично не перевірити)."""
    tree = ast.parse(open(_CALC_PATH, encoding="utf-8").read())
    literal_keys, dynamic = set(), 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "add_c"):
            continue
        if len(node.args) < 2:
            continue
        key_arg = node.args[1]
        if isinstance(key_arg, ast.Constant) and isinstance(key_arg.value, str):
            literal_keys.add(key_arg.value)
        else:
            dynamic += 1
    return literal_keys, dynamic


def test_every_used_price_key_exists_in_default_prices():
    used, _ = _keys_used_in_add_c()
    missing = sorted(k for k in used if k not in DEFAULT_PRICES)
    assert not missing, (
        "Калькулятор використовує ключі, яких немає в DEFAULT_PRICES "
        f"(вони тихо коштуватимуть 0): {missing}"
    )


def test_every_used_price_key_has_a_label():
    used, _ = _keys_used_in_add_c()
    missing = sorted(k for k in used if k not in PRICE_META)
    assert not missing, (
        "Калькулятор використовує ключі без людської назви в PRICE_META "
        f"(у деталізації буде технічний ID): {missing}"
    )


def test_add_c_uses_only_literal_keys():
    """Якщо колись з'явиться add_c зі змінним ключем — цей тест підсвітить,
    що статична звірка вже не покриває все, і треба буде її доповнити."""
    _, dynamic = _keys_used_in_add_c()
    assert dynamic == 0, (
        f"{dynamic} виклик(ів) add_c зі змінним ключем — статична звірка їх "
        "не перевіряє. Онови контрактний тест або зроби ключі літералами."
    )
