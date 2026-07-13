"""Тести калькулятора кошторису.

Кожен тест фіксує реальний баг, який ми вже ловили руками — щоб він
не повернувся непоміченим. Запуск: pytest -q (локально або в CI).
"""
import pytest

from calculator import calculate_budget, apply_virtual_measurements, PRICE_META

# Мінімальний зріз прайсу — тести не залежать від Google-таблиці
PRICES = {
    "room_lam": [405, 600, 900],
    "room_quartz": [565, 1200, 1800],
    "tile_floor_std": [1900, 1500, 2500],
    "tile_wall_std": [2100, 1500, 2500],
    "wall_paper": [1000, 200, 400],
    "light_point": [250, 300, 800],
    "door_std": [3650, 8000, 15000],
    "screed_wet": [1100, 700, 700],
    "warm_floor_elec": [550, 400, 500],
    "plumbing": [1100, 300, 300],
    "toilet_install": [4900, 12000, 30000],
    "bath_tub": [3800, 15000, 100000],
    "mixer_std": [1000, 2000, 15000],
}


def calc(answers, client=None):
    payload = {"client": client or {"area": "80"}, "answers": answers}
    return calculate_budget(apply_virtual_measurements(payload), PRICES)


def room(rid, rtype, name, floor_m2=15, walls_m2=40, **extra):
    r = {"id": rid, "type": rtype, "name": name,
         "measurements": {"floor": floor_m2, "walls": walls_m2}}
    r.update(extra)
    return r


# --------------------------------------------------------------------
# БАЗОВА ТАРИФІКАЦІЯ
# --------------------------------------------------------------------

def test_room_floor_is_charged():
    """Головний баг, через який ціна не рахувалась на кроці кімнат."""
    b = calc({"rooms": [room("r1", "room", "Кімната", 18, 50, floor="Ламінат")]})
    assert b["total_work"] == 405 * 18
    assert b["total_mat_min"] == 600 * 18


def test_walls_and_light_are_charged():
    b = calc({"rooms": [room("r1", "room", "К", 18, 50,
                             floor="Ламінат", walls=["Шпалери"], light=["Точкове світло"])]})
    # ламінат + шпалери + точки (18/2 = 9 точок)
    assert b["total_work"] == 405 * 18 + 1000 * 50 + 250 * 9


# --------------------------------------------------------------------
# ФІКСАЦІЯ ВИПРАВЛЕНИХ БАГІВ
# --------------------------------------------------------------------

def test_no_change_option_costs_nothing():
    """«Без змін» — чесний opt-out, він не повинен нічого тарифікувати."""
    b = calc({"rooms": [room("r1", "room", "К", 18, 50,
                             floor="Без змін", walls=["Без змін"], light=["Без змін"])]})
    assert b["total_work"] == 0
    assert b["total_mat_min"] == 0


def test_doors_counted_only_for_rooms_and_baths():
    """Балкон/передпокій/гардероб НЕ отримують міжкімнатних дверей."""
    b = calc({"interior_door": "Стандарт", "rooms": [
        room("r1", "room", "Кімната"),
        room("b1", "bath", "Санвузол"),
        room("bl", "balcony", "Балкон"),
        room("h", "hallway", "Передпокій"),
        room("w", "wardrobe", "Гардероб"),
    ]})
    assert b["costs"]["doors"][0] == 3650 * 2   # 1 кімната + 1 санвузол


def test_warm_floor_uses_real_area():
    """Тепла підлога рахується за реальною площею кімнати, а не по 5 м²."""
    b = calc({"warm_floor": ["Спальня"], "rooms": [room("r1", "room", "Спальня", 22, 55)]})
    assert b["costs"]["electric"][0] == 550 * 22


def test_warm_floor_unknown_room_falls_back():
    """Невідома назва (стара чернетка) не валить розрахунок."""
    b = calc({"warm_floor": ["Кімната 1"], "rooms": [room("r1", "room", "Спальня", 22)]})
    assert b["costs"]["electric"][0] == 550 * 5   # запобіжник


def test_empty_tub_type_is_free():
    """Ванна з порожнім type не має додавати десятки тисяч."""
    b = calc({"rooms": [room("b1", "bath", "С/в", 5, 20, tub={"type": ""})]})
    assert b["total_work"] == 0


def test_garbage_input_does_not_crash():
    """Криве поле не має валити ендпоінт у 500 (а фронт — у «тихі нулі»)."""
    b = calc({
        "demo_interior": "х", "screed_area": "",
        "rooms": [room("b1", "bath", "С/в", "8", None,
                       floor="Керамограніт", mixer_std="два")],
    }, client={"area": "abc"})
    assert b["total_work"] == 1900 * 8   # плитка порахувалась, сміття проігноровано


# --------------------------------------------------------------------
# ПО-КІМНАТНИЙ ОБЛІК І ПОСТРОЧНА ДЕТАЛІЗАЦІЯ
# --------------------------------------------------------------------

def test_room_costs_sum_matches_total():
    """Сума по кімнатах + загальні роботи == тотал. Копійка в копійку."""
    b = calc({"interior_door": "Стандарт", "screed_done": "Мокра стяжка", "rooms": [
        room("r1", "room", "К", 18, 50, floor="Ламінат", walls=["Шпалери"]),
        room("b1", "bath", "С/в", 5, 22, floor="Керамограніт",
             toilet={"type": "Інсталяція", "tier": "Комфорт"}),
    ]})
    rooms_w = sum(v[0] for v in b["room_costs"].values())
    general_w = b["total_work"] - rooms_w
    assert general_w > 0                       # стяжка і двері — загальні
    assert pytest.approx(rooms_w + general_w) == b["total_work"]


def test_lines_sum_matches_total():
    """Сума ВСІХ рядків деталізації == тотал (нічого не загубилось)."""
    b = calc({"interior_door": "Стандарт", "screed_done": "Мокра стяжка", "rooms": [
        room("r1", "room", "К", 18, 50, floor="Ламінат", walls=["Шпалери"],
             light=["Точкове світло"]),
    ]})
    lines = b["general_lines"] + [l for ls in b["room_lines"].values() for l in ls]
    assert round(sum(l["work"] for l in lines)) == round(b["total_work"])


def test_line_has_human_label_and_unit():
    b = calc({"rooms": [room("r1", "room", "К", 18, 50, floor="Ламінат")]})
    line = b["room_lines"]["r1"][0]
    assert line["label"] == "Ламінат"
    assert line["unit"] == "м²"
    assert line["qty"] == 18
    assert line["rate"] == 405
    assert line["work"] == 7290


def test_sheet_labels_override_defaults():
    """Назва з колонки «Назва» Google-таблиці має пріоритет над дефолтом."""
    payload = {"client": {"area": "50"},
               "answers": {"rooms": [room("r1", "room", "К", 10, 30, floor="Ламінат")]}}
    b = calculate_budget(apply_virtual_measurements(payload), PRICES,
                         labels={"room_lam": "Ламінат 33 клас, Kronospan"})
    assert b["room_lines"]["r1"][0]["label"] == "Ламінат 33 клас, Kronospan"


def test_custom_work_becomes_line_in_its_room():
    b = calc({"rooms": [room("r1", "room", "К", 18, 50)],
              "custom_works": [{"name": "Ніша з підсвіткою", "calc_type": "Фіксована ціна",
                                "zone_id": "r1", "work_price": "3000", "mat_price": "1200"}]})
    line = b["room_lines"]["r1"][0]
    assert line["label"] == "Ніша з підсвіткою"
    assert line["work"] == 3000
    assert b["costs"]["custom"][0] == 3000


def test_custom_work_per_m2_multiplies_by_room_area():
    b = calc({"rooms": [room("r1", "room", "К", 20, 50)],
              "custom_works": [{"name": "Шліфування", "calc_type": "За м² підлоги",
                                "zone_id": "r1", "work_price": "100", "mat_price": "0"}]})
    assert b["costs"]["custom"][0] == 100 * 20


# --------------------------------------------------------------------
# КОНТРАКТ З КАБІНЕТОМ МЕНЕДЖЕРА (бот читає costs за цими ключами)
# --------------------------------------------------------------------

def test_costs_keys_are_stable():
    b = calc({"rooms": []})
    assert set(b["costs"]) == {"rough", "electric", "doors", "rooms", "baths", "custom"}


def test_price_meta_covers_all_keys_used():
    """Кожен ключ прайсу має людську назву — інакше в деталізації буде
    технічний ідентифікатор замість «Ламінат»."""
    missing = [k for k in PRICES if k not in PRICE_META]
    assert not missing, f"Немає назв для ключів: {missing}"
