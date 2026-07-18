"""Рівні «Стандарт / Комфорт / Преміум».

БД не потрібна — перевіряємо саму логіку вибору ціни в калькуляторі.

Модель така: у прайсі три-чотири числа
    [робота, стандарт, преміум]           — комфорт рахується як середнє
    [робота, стандарт, преміум, комфорт]  — комфорт заданий явно
Рівень вибирає, яке з чисел піде в кошторис.
"""
import pytest

from calculator import calculate_budget, apply_virtual_measurements
from config import DEFAULT_PRICES, DEFAULT_COMFORT_PRICES

WORK, STD, PREM = 13000, 15000, 45000


def _budget(prices, tier):
    """Кошторис за одну позицію — кондиціонер із заданим рівнем."""
    data = apply_virtual_measurements({"client": {"area": "50"}, "answers": {"rooms": [
        {"id": "r1", "type": "room", "name": "Кімната",
         "measurements": {"floor": 20, "walls": 50},
         "other": {"Кондиціонер": tier}},
    ]}})
    return calculate_budget(data, prices)["total_mat_min"]


def _prices(comfort=None):
    p = dict(DEFAULT_PRICES)
    p["ac"] = [WORK, STD, PREM] if comfort is None else [WORK, STD, PREM, comfort]
    return p


def test_standard_uses_lower_bound():
    assert _budget(_prices(), "Стандарт") == STD


def test_premium_uses_upper_bound():
    assert _budget(_prices(), "Преміум") == PREM


def test_comfort_falls_back_to_code_default_when_price_has_three_numbers():
    """Режим Google-таблиці: четвертого числа немає, тож беруться дефолти
    з config — рівно ті, що раніше були захардкоджені в калькуляторі."""
    assert _budget(_prices(), "Комфорт") == DEFAULT_COMFORT_PRICES["ac"]


def test_comfort_from_price_wins_over_code_default():
    """Головне, заради чого все робилось: ціна з кабінету має пріоритет."""
    assert _budget(_prices(comfort=33000), "Комфорт") == 33000


def test_comfort_averages_when_no_default_and_no_explicit_value():
    """Позиція без окремої ціни комфорту поводиться як раніше — середнє.

    Звукоізоляція, на відміну від кондиціонера, свого рівня не має:
    значення в анкеті — це не рівень, тож діє глобальний тумблер.
    """
    p = dict(DEFAULT_PRICES)
    p["soundproof"] = [100, 200, 400]
    data = apply_virtual_measurements({"client": {"area": "50"}, "answers": {
        "global_tier": "Комфорт",
        "rooms": [
            {"id": "r1", "type": "room", "name": "Кімната",
             "measurements": {"floor": 10, "walls": 10},
             "other": {"Звукоізоляція": "Так"}},
        ]}})
    # 10 м² × середнє(200, 400) = 3000
    assert calculate_budget(data, p)["total_mat_min"] == 3000


def test_explicit_none_comfort_still_averages():
    """None у четвертому числі = «рахувати як середнє», а не «нуль»."""
    assert _budget(_prices(comfort=None), "Комфорт") == DEFAULT_COMFORT_PRICES["ac"]


@pytest.mark.parametrize("tier", ["Стандарт", "Комфорт", "Преміум"])
def test_changing_comfort_does_not_touch_other_tiers(tier):
    before = _budget(_prices(), tier)
    after = _budget(_prices(comfort=39000), tier)
    if tier == "Комфорт":
        assert before != after, "зміна комфорту не вплинула на рівень «Комфорт»"
    else:
        assert before == after, f"зміна комфорту зачепила рівень «{tier}»"


def test_defaults_stay_inside_their_range():
    """Комфорт мусить бути між стандартом і преміумом — інакше кошторис
    «Комфорт» вийде дорожчим за «Преміум»."""
    for key, comfort in DEFAULT_COMFORT_PRICES.items():
        price = DEFAULT_PRICES.get(key)
        if not price:
            continue
        assert price[1] <= comfort <= price[2], (
            f"{key}: комфорт {comfort} поза межами {price[1]}–{price[2]}")
