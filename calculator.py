import math
import copy

def apply_virtual_measurements(data_json):
    """
    Заповнює віртуальні заміри, якщо вони відсутні.
    """
    data = copy.deepcopy(data_json)
    total_area = float(data.get("client", {}).get("area", 0) or 0)
    if total_area <= 0:
        return data

    ans = data.get("answers", {})
    meas = ans.get("measurements", {})
    aux = ans.get("aux_rooms", [])
    rooms_c = int(ans.get("rooms_count", 0) or 0)
    baths_c = int(ans.get("baths_count", 0) or 0)
    
    used_area = 0
    if "Передпокій" in aux and not meas.get("hallway"):
        sq = total_area * 0.10
        meas["hallway"] = {"floor": sq, "walls": sq * 2.5}
        used_area += sq
    if "Кухня" in aux and not meas.get("kitchen"):
        sq = total_area * 0.20
        meas["kitchen"] = {"floor": sq, "walls": sq * 2.5}
        used_area += sq
    for i in range(1, baths_c + 1):
        if not meas.get(f"bath_{i}"):
            sq = 4.5
            meas[f"bath_{i}"] = {"floor": sq, "walls": sq * 2.5}
            used_area += sq
    if "Балкон" in aux and not meas.get("balcony"):
        sq = 3.5
        meas["balcony"] = {"floor": sq, "walls": sq * 2.5}
        used_area += sq
    if "Гардероб" in aux and not meas.get("wardrobe"):
        sq = 3.5
        meas["wardrobe"] = {"floor": sq, "walls": sq * 2.5}
        used_area += sq
        
    rem_area = max(0, total_area - used_area)
    if rooms_c > 0:
        room_sq = rem_area / rooms_c
        for i in range(1, rooms_c + 1):
            if not meas.get(f"room_{i}"):
                meas[f"room_{i}"] = {"floor": room_sq, "walls": room_sq * 2.5}
            
    ans["measurements"] = meas
    data["answers"] = ans
    return data


def get_tier_price(base_price_tuple, tier_str):
    """
    Повертає вартість матеріалів залежно від tier.
    base_price_tuple: (робота, матеріал_мін, матеріал_макс)
    tier_str: Standard (S), Comfort (C), Premium (P) або інше.
    """
    work = base_price_tuple[0]
    mat_min = base_price_tuple[1]
    mat_max = base_price_tuple[2]
    if not tier_str or tier_str == "-" or tier_str == "Standard" or tier_str == "S":
        return work, mat_min
    if tier_str == "Premium" or tier_str == "P":
        return work, mat_max
    if tier_str == "Comfort" or tier_str == "C":
        # середнє значення між мін і макс
        return work, mat_min + (mat_max - mat_min) * 0.4
    return work, mat_min


def calculate_budget(data_json, PRICES):
    """
    Розраховує бюджет на основі анкети та прайсу.
    Повертає словник з детальними витратами.
    """
    # Ініціалізація витрат за категоріями
    costs = {
        "rough": [0, 0, 0],      # чорнові роботи
        "electric": [0, 0, 0],    # електрика
        "doors": [0, 0, 0],       # двері
        "rooms": [0, 0, 0],       # оздоблення кімнат
        "baths": [0, 0, 0],       # санвузли
        "logistics": [0, 0, 0],   # логістика
        "ceilings": [0, 0, 0],    # стелі
        "baseboards": [0, 0, 0],  # плінтуси
        "windowsills": [0, 0, 0], # підвіконня
        "radiators": [0, 0, 0],   # радіатори
        "ac": [0, 0, 0],          # кондиціонери
        "other": [0, 0, 0]        # інше
    }

    client = data_json.get("client", {})
    answers = data_json.get("answers", {})
    measurements = answers.get("measurements", {})
    total_area = float(client.get("area", 0) or 0)
    
    def get_sq(zone_id, key):
        try:
            return float(measurements.get(zone_id, {}).get(key, 0))
        except:
            return 0.0

    # ---------- Логістика ----------
    costs["logistics"][0] += total_area * PRICES["logistics_base"][0]

    # ---------- Чорнові роботи ----------
    # Машинна штукатурка стін
    if answers.get("rough_plaster_done") == "Так":
        wall_area = total_area * 2.5  # приблизна площа стін
        costs["rough"][0] += wall_area * PRICES["rough_plaster"][0]
        costs["rough"][1] += wall_area * PRICES["rough_plaster"][1]
        costs["rough"][2] += wall_area * PRICES["rough_plaster"][2]

    # Розводка каналізації (якщо немає)
    if answers.get("plumbing_done") == "Ні":
        costs["rough"][0] += total_area * PRICES["plumbing"][0]
        costs["rough"][1] += total_area * PRICES["plumbing"][1]
        costs["rough"][2] += total_area * PRICES["plumbing"][2]

    # Розводка електрики (якщо немає)
    if answers.get("electricity_done") == "Ні":
        # Додатково враховуємо в розділі електрики
        pass  # буде нижче

    # Стяжка підлоги
    screed = answers.get("screed_done")
    if screed == "Потрібна: Мокра":
        costs["rough"][0] += total_area * PRICES["screed_wet"][0]
        costs["rough"][1] += total_area * PRICES["screed_wet"][1]
        costs["rough"][2] += total_area * PRICES["screed_wet"][2]
    elif screed == "Потрібна: Напівсуха":
        costs["rough"][0] += total_area * PRICES["screed_dry"][0]
        costs["rough"][1] += total_area * PRICES["screed_dry"][1]
        costs["rough"][2] += total_area * PRICES["screed_dry"][2]

    # ---------- Електрика та тепла підлога ----------
    # Кількість розеток (приблизно)
    sockets = (int(answers.get('rooms_count', 0)) * 8) + (int(answers.get('baths_count', 0)) * 4) + 14
    # Тепла підлога
    wf_zones = answers.get('warm_floor', [])
    valid_wf = [z for z in wf_zones if z != "Не потребується"]
    if valid_wf:
        # Припустимо, тепла підлога займає 50% від площі кожного приміщення (спрощено)
        wf_area = total_area * 0.5
        sockets += len(valid_wf)  # додаткові розетки для терморегуляторів
        costs["electric"][0] += wf_area * PRICES["warm_floor_elec"][0]
        costs["electric"][1] += wf_area * PRICES["warm_floor_elec"][1]
        costs["electric"][2] += wf_area * PRICES["warm_floor_elec"][2]

    costs["electric"][0] += sockets * PRICES["electric_point"][0]
    costs["electric"][1] += sockets * PRICES["electric_point"][1]
    # Якщо електрика не розведена, додаємо проводку
    if answers.get("electricity_done") == "Ні":
        costs["electric"][0] += total_area * PRICES["electric_wire"][0]
        costs["electric"][1] += total_area * PRICES["electric_wire"][1]
        costs["electric"][2] += total_area * PRICES["electric_wire"][2]

    # ---------- Вхідні двері ----------
    ent_door = answers.get("entrance_door", {})
    if isinstance(ent_door, dict) and ent_door.get("type") and ent_door["type"] not in ["Ні", "Не потребується"]:
        door_type = ent_door["type"]
        tier = ent_door.get("tier", "Standard")
        if "МДФ" in door_type:
            price_key = "door_entrance_mdf"
        else:
            price_key = "door_entrance_armor"
        w, m = get_tier_price(PRICES[price_key], tier)
        costs["doors"][0] += w
        costs["doors"][1] += m
        costs["doors"][2] += m

    # ---------- Міжкімнатні двері ----------
    int_door = answers.get("interior_door")
    if int_door and int_door not in ["Ні", "Не потребується"]:
        # Припустимо, кількість дверей = кількість кімнат + 1
        door_count = int(answers.get('rooms_count', 0)) + 1
        if int_door == "Прихований монтаж":
            price_key = "door_hidden"
        else:
            price_key = "door_std"
        w, m = get_tier_price(PRICES[price_key], "Standard")  # можна додати tier, якщо буде
        costs["doors"][0] += door_count * w
        costs["doors"][1] += door_count * m
        costs["doors"][2] += door_count * m

    # ---------- Перебір приміщень за вимірами ----------
    for zone_id in measurements.keys():
        fsq = get_sq(zone_id, "floor")
        wsq = get_sq(zone_id, "walls")
        prefix = zone_id.split('_')[0]
        if "room" in zone_id:
            cat = "rooms"
        elif "bath" in zone_id:
            cat = "baths"
        else:
            # для передпокою, кухні, балкону тощо – об'єднаємо в "rooms" для спрощення
            cat = "rooms"

        # ---------- Підлога ----------
        floor_key = f"{prefix}_floor"
        floor_ans = answers.get(floor_key)
        if floor_ans:
            if isinstance(floor_ans, dict):
                ftype = floor_ans.get("type", "")
                tier = floor_ans.get("tier", "Standard")
            else:
                ftype = floor_ans
                tier = "Standard"

            # Визначаємо ціновий ключ
            if "Мозаїка" in ftype:
                price_key = "tile_floor_mosaic"
            elif "Великоформатний" in ftype:
                price_key = "tile_floor_large"
            elif "Керамограніт" in ftype or "плитка" in ftype:
                price_key = "tile_floor_std"
            elif "Ламінат" in ftype:
                price_key = "room_lam"
            elif "Паркет" in ftype:
                price_key = "room_parket"
            elif "Кварцвініл" in ftype or "Кварц-вініл" in ftype:
                price_key = "room_quartz"
            elif "Лінолеум" in ftype:
                price_key = "linoleum"
            else:
                price_key = None

            if price_key and price_key in PRICES:
                w, m = get_tier_price(PRICES[price_key], tier)
                costs[cat][0] += fsq * w
                costs[cat][1] += fsq * m
                costs[cat][2] += fsq * m

        # ---------- Стіни ----------
        walls_key = f"{prefix}_walls"
        walls_ans = answers.get(walls_key)
        if walls_ans and cat == "rooms":
            if isinstance(walls_ans, dict):
                wtype = walls_ans.get("type", "")
                tier = walls_ans.get("tier", "Standard")
            else:
                wtype = walls_ans
                tier = "Standard"

            if "Шпалери" in wtype:
                price_key = "wall_paper"
            elif "Декоративна" in wtype:
                price_key = "wall_decor"
            elif "Фарбування" in wtype:
                price_key = "wall_paint"
            elif "Побілка" in wtype:
                price_key = "whitewash"
            elif "Обшивка деревʼяними рейками" in wtype:
                price_key = "wood_rails"
            else:
                price_key = None

            if price_key and price_key in PRICES:
                w, m = get_tier_price(PRICES[price_key], tier)
                costs[cat][0] += wsq * w
                costs[cat][1] += wsq * m
                costs[cat][2] += wsq * m

        # Для санвузлів стіни окремо (плитка)
        if cat == "baths":
            wall_tile_key = f"{zone_id}_wall_tile"  # наприклад, bath_1_wall_tile
            wall_tile_ans = answers.get(wall_tile_key)
            if wall_tile_ans:
                if isinstance(wall_tile_ans, dict):
                    wtype = wall_tile_ans.get("type", "")
                    tier = wall_tile_ans.get("tier", "Standard")
                else:
                    wtype = wall_tile_ans
                    tier = "Standard"

                if "Мозаїка" in wtype:
                    price_key = "tile_wall_mosaic"
                elif "Великоформатний" in wtype:
                    price_key = "tile_wall_large"
                else:
                    price_key = "tile_wall_std"

                w, m = get_tier_price(PRICES[price_key], tier)
                costs["baths"][0] += wsq * w
                costs["baths"][1] += wsq * m
                costs["baths"][2] += wsq * m

        # ---------- Освітлення ----------
        light_key = f"{prefix}_light"
        light_ans = answers.get(light_key)
        if light_ans and isinstance(light_ans, list):
            for item in light_ans:
                if item == "Точкове світло":
                    # кількість точок приблизно = площа * 1.5
                    points = int(fsq * 1.5)
                    costs[cat][0] += points * PRICES["light_point"][0]
                    costs[cat][1] += points * PRICES["light_point"][1]
                elif item == "Люстра":
                    costs[cat][0] += PRICES["light_chandelier"][0]
                    costs[cat][1] += PRICES["light_chandelier"][1]
                elif item == "Трек / Лінія":
                    # припустимо довжина треку = периметр/4
                    length = math.sqrt(fsq) * 2  # приблизно
                    costs[cat][0] += length * PRICES["light_track"][0]
                    costs[cat][1] += length * PRICES["light_track"][1]
                elif "LED" in item or "підсвітка" in item:
                    # припустимо 5 метрів
                    costs[cat][0] += 5 * PRICES["light_led"][0]

    # ---------- Сантехніка (санвузли) ----------
    baths_count = int(answers.get('baths_count', 0))
    for i in range(1, baths_count + 1):
        # Унітаз
        toilet = answers.get(f"bath_{i}_toilet")
        if toilet and isinstance(toilet, dict) and toilet.get("type") not in ["Не обладнувати", None]:
            tier = toilet.get("tier", "Standard")
            if "Окремостоячий" in toilet.get("type", ""):
                price_key = "toilet_okrem"
            else:
                price_key = "toilet_install"
            w, m = get_tier_price(PRICES[price_key], tier)
            costs["baths"][0] += w
            costs["baths"][1] += m
            costs["baths"][2] += m

        # Ванна
        tub = answers.get(f"bath_{i}_tub")
        if tub and isinstance(tub, dict) and tub.get("type") not in ["Не обладнувати", None]:
            tier = tub.get("tier", "Standard")
            if "Гідро масаж" in tub.get("type", ""):
                price_key = "bath_tub"  # окремої ціни немає, використаємо bath_tub
            else:
                price_key = "bath_tub"
            w, m = get_tier_price(PRICES[price_key], tier)
            costs["baths"][0] += w
            costs["baths"][1] += m
            costs["baths"][2] += m

        # Душ
        shower = answers.get(f"bath_{i}_shower")
        if shower and isinstance(shower, list):
            for item in shower:
                if "Піддон" in item:
                    costs["baths"][0] += PRICES["shower_tray"][0]
                    costs["baths"][1] += PRICES["shower_tray"][1]
                elif "Душовий трап" in item:
                    costs["baths"][0] += PRICES["shower_trap"][0]
                    costs["baths"][1] += PRICES["shower_trap"][1]
                elif "Скляна перегородка" in item:
                    costs["baths"][0] += PRICES["shower_glass"][0]
                    costs["baths"][1] += PRICES["shower_glass"][1]
                elif "Скляна конструкція з дверима" in item:
                    costs["baths"][0] += PRICES["shower_doors"][0]
                    costs["baths"][1] += PRICES["shower_doors"][1]

        # Змішувачі
        mixer_std = int(answers.get(f"bath_{i}_mixer_std", 0))
        mixer_hidden = int(answers.get(f"bath_{i}_mixer_hidden", 0))
        costs["baths"][0] += mixer_std * PRICES["mixer_std"][0]
        costs["baths"][1] += mixer_std * PRICES["mixer_std"][1]
        costs["baths"][0] += mixer_hidden * PRICES["mixer_hidden"][0]
        costs["baths"][1] += mixer_hidden * PRICES["mixer_hidden"][1]

        # Інші потреби (пральна машина, сушильна, бойлер, рушникосушка тощо)
        other = answers.get(f"bath_{i}_other")
        if other and isinstance(other, dict):
            for item, tier in other.items():
                if tier is True:
                    tier = "Standard"
                if item == "Пральна машина":
                    costs["baths"][0] += PRICES["tech_washer"][0]
                    costs["baths"][1] += PRICES["tech_washer"][1]
                elif item == "Сушильна машина":
                    costs["baths"][0] += PRICES["tech_washer"][0]  # умовно
                elif item == "Бойлер":
                    w, m = get_tier_price(PRICES["boiler"], tier)
                    costs["baths"][0] += w
                    costs["baths"][1] += m
                elif item == "Рушникосушка":
                    w, m = get_tier_price(PRICES["towel_dryer"], tier)
                    costs["baths"][0] += w
                    costs["baths"][1] += m
                elif item == "Гігієнічний душ":
                    w, m = get_tier_price(PRICES["hygienic_shower"], tier)
                    costs["baths"][0] += w
                    costs["baths"][1] += m
                elif item == "Дзеркало з підсвіткою":
                    w, m = get_tier_price(PRICES["mirror_led"], tier)
                    costs["baths"][0] += w
                    costs["baths"][1] += m
                elif item == "Умивальник з тумбою":
                    w, m = get_tier_price(PRICES["sink_cabinet"], tier)
                    costs["baths"][0] += w
                    costs["baths"][1] += m

    # ---------- Кухня ----------
    kitchen_other = answers.get("kitchen_other")
    if kitchen_other and isinstance(kitchen_other, dict):
        for item, tier in kitchen_other.items():
            if tier is True:
                tier = "Standard"
            if "Посудомийна машина" in item:
                costs["rooms"][0] += PRICES["tech_kitchen"][0]
                costs["rooms"][1] += PRICES["tech_kitchen"][1]
            elif "Осмос" in item:
                w, m = get_tier_price(PRICES["tech_osmos"], tier)
                costs["rooms"][0] += w
                costs["rooms"][1] += m
            elif "Подрібнювач відходів" in item:
                # немає окремої ціни, використаємо tech_kitchen
                costs["rooms"][0] += PRICES["tech_kitchen"][0]
                costs["rooms"][1] += PRICES["tech_kitchen"][1]
            elif "Мікрохвильова піч" in item:
                costs["rooms"][0] += PRICES["tech_kitchen"][0]
            elif "Духова шафа" in item:
                costs["rooms"][0] += PRICES["tech_kitchen"][0]
            elif "Радіатор" in item:
                w, m = get_tier_price(PRICES["radiator"], tier)
                costs["rooms"][0] += w
                costs["rooms"][1] += m
            elif "Підсвітка робочої поверхні" in item:
                costs["rooms"][0] += 2 * PRICES["light_led"][0]  # 2 метри

    # ---------- Інші елементи ----------
    # Плінтуси
    baseboard = answers.get("baseboard")
    if baseboard and baseboard not in ["Ні", None]:
        if baseboard == "Стандартний":
            price_key = "base_std"
        elif baseboard == "Тіньовий шов":
            price_key = "base_shadow"
        elif baseboard == "Прихований монтаж":
            price_key = "base_hidden"
        else:
            price_key = None
        if price_key:
            # довжина плінтуса приблизно = периметр кімнат
            perimeter = total_area * 4  # дуже приблизно
            costs["baseboards"][0] += perimeter * PRICES[price_key][0]
            costs["baseboards"][1] += perimeter * PRICES[price_key][1]

    # Стеля
    ceiling = answers.get("ceiling")
    if ceiling and ceiling != "Ні":
        if ceiling == "Натяжна":
            price_key = "ceil_stretch"
        elif ceiling == "Гіпсокартон":
            price_key = "ceil_gips"
        else:
            price_key = None
        if price_key:
            costs["ceilings"][0] += total_area * PRICES[price_key][0]
            costs["ceilings"][1] += total_area * PRICES[price_key][1]
        # Тіньовий шов
        if answers.get("ceiling_shadow") == "Так":
            # додаткова робота, ціна може бути окремо, поки не враховуємо
            pass

    # Підвіконня (для кімнат)
    # В ідеалі треба пройти по кожній кімнаті, але спростимо
    rooms_count = int(answers.get('rooms_count', 0))
    for i in range(1, rooms_count + 1):
        sills = answers.get(f"room_{i}_sills")
        if sills and sills not in ["Не потребується", None]:
            if sills == "Пластик":
                price_key = "sill_plastic"
            elif sills == "Дерево":
                price_key = "sill_wood"
            elif sills == "Штучний камінь":
                price_key = "sill_stone"
            else:
                continue
            # припустимо 1 підвіконня на кімнату
            costs["windowsills"][0] += PRICES[price_key][0]
            costs["windowsills"][1] += PRICES[price_key][1]

    # Радіатори (окрім тих, що в інших)
    # Додатково можна взяти з room_other
    for i in range(1, rooms_count + 1):
        other = answers.get(f"room_{i}_other")
        if other and isinstance(other, dict) and "Радіатор" in other:
            tier = other["Радіатор"] if other["Радіатор"] != "Так" else "Standard"
            w, m = get_tier_price(PRICES["radiator"], tier)
            costs["radiators"][0] += w
            costs["radiators"][1] += m

    # Кондиціонери
    for i in range(1, rooms_count + 1):
        other = answers.get(f"room_{i}_other")
        if other and isinstance(other, dict) and "Кондиціонер" in other:
            tier = other["Кондиціонер"] if other["Кондиціонер"] != "Так" else "Standard"
            w, m = get_tier_price(PRICES["ac"], tier)
            costs["ac"][0] += w
            costs["ac"][1] += m

    # ---------- Підсумок ----------
    total_work = sum(c[0] for c in costs.values())
    total_mat_min = sum(c[1] for c in costs.values())
    total_mat_max = sum(c[2] for c in costs.values())

    return {
        "total_work": round(total_work),
        "total_mat_min": round(total_mat_min),
        "total_mat_max": round(total_mat_max),
        "sockets": sockets,
        "costs": costs
    }
