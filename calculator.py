import math
import copy

def apply_virtual_measurements(data_json):
    """Створює 'віртуальні' площі для живого кошика, якщо точних ще немає."""
    data = copy.deepcopy(data_json)
    total_area = float(data.get("client", {}).get("area", 0) or 0)
    if total_area <= 0: return data

    ans = data.get("answers", {})
    meas = ans.get("measurements", {})
    aux = ans.get("aux_rooms", [])
    rooms_c = int(ans.get("rooms_count", 0) or 0)
    baths_c = int(ans.get("baths_count", 0) or 0)
    
    used_area = 0
    
    # Нормативи площ
    if "Передпокій" in aux and not meas.get("hallway"): sq = total_area * 0.10; meas["hallway"] = {"floor": sq, "walls": sq * 2.5}; used_area += sq
    if "Кухня" in aux and not meas.get("kitchen"): sq = total_area * 0.20; meas["kitchen"] = {"floor": sq, "walls": sq * 2.5}; used_area += sq
    for i in range(1, baths_c + 1):
        if not meas.get(f"bath_{i}"): sq = 4.5; meas[f"bath_{i}"] = {"floor": sq, "walls": sq * 2.5}; used_area += sq
    if "Балкон" in aux and not meas.get("balcony"): sq = 3.5; meas["balcony"] = {"floor": sq, "walls": sq * 2.5}; used_area += sq
    if "Гардероб" in aux and not meas.get("wardrobe"): sq = 3.5; meas["wardrobe"] = {"floor": sq, "walls": sq * 2.5}; used_area += sq
    if "Підвал" in aux and not meas.get("basement"): sq = total_area * 0.15; meas["basement"] = {"floor": sq, "walls": sq * 2.5}
    if "Горище" in aux and not meas.get("attic"): sq = total_area * 0.3; meas["attic"] = {"floor": sq, "walls": sq * 2.5}
        
    # Залишок площі ділимо на житлові кімнати
    rem_area = max(0, total_area - used_area)
    if rooms_c > 0:
        room_sq = rem_area / rooms_c
        for i in range(1, rooms_c + 1):
            if not meas.get(f"room_{i}"): meas[f"room_{i}"] = {"floor": room_sq, "walls": room_sq * 2.5}
            
    ans["measurements"] = meas
    data["answers"] = ans
    return data

def calculate_budget(data_json, PRICES):
    """Головний прорахунок кошторису на основі прайсу та даних."""
    costs = { "rough": [0,0,0], "electric": [0,0,0], "doors": [0,0,0], "rooms": [0,0,0], "baths": [0,0,0], "logistics": [0,0,0] }
    client = data_json.get("client", {})
    answers = data_json.get("answers", {})
    measurements = answers.get("measurements", {})
    
    total_area = float(client.get("area", 0) or 0)
    floor = int(client.get("floor", 1) or 1)
    elevator = client.get("elevator", "Немає")
    
    def get_sq(zone_id, key):
        try: return float(measurements.get(zone_id, {}).get(key, 0))
        except: return 0.0

    # 1. Логістика
    logistics_work = total_area * PRICES["logistics_base"][0]
    if elevator == "Немає" and floor > 1: logistics_work += (total_area * PRICES["logistics_stair"][0] * floor)
    elif elevator == "Пасажирський": logistics_work += (total_area * PRICES["logistics_elev"][0] * floor)
    costs["logistics"][0] += logistics_work

    # 2. Чорнові роботи
    screed_ans = answers.get("screed_done", "")
    if "Мокра" in screed_ans: costs["rough"][0] += total_area * PRICES["screed_wet"][0]; costs["rough"][1] += total_area * PRICES["screed_wet"][1]; costs["rough"][2] += total_area * PRICES["screed_wet"][2]
    elif "Напівсуха" in screed_ans: costs["rough"][0] += total_area * PRICES["screed_dry"][0]; costs["rough"][1] += total_area * PRICES["screed_dry"][1]; costs["rough"][2] += total_area * PRICES["screed_dry"][2]
    if answers.get("plumbing_done") == "Ні": costs["rough"][0] += total_area * PRICES["plumbing"][0]; costs["rough"][1] += total_area * PRICES["plumbing"][1]; costs["rough"][2] += total_area * PRICES["plumbing"][2]

    # 3. Електрика
    sockets = 0
    if answers.get('kitchen_needed') != 'Ні': sockets += 10
    if answers.get('hallway_needed') != 'Ні': sockets += 4
    sockets += int(answers.get('rooms_count', 0)) * 8
    sockets += int(answers.get('baths_count', 0)) * 4
    warm_floors = answers.get('warm_floor', [])
    sockets += len([w for w in warm_floors if w != 'Не потребується'])
    for tech in ["Посудомийна машина", "Подрібнювач відходів", "Мікрохвильова піч", "Духова шафа", "Підсвітка робочої поверхні"]:
        if tech in answers.get("kitchen_other", {}): sockets += 1

    if answers.get("electricity_done") == "Ні":
        costs["electric"][0] += total_area * PRICES["electric_wire"][0]; costs["electric"][1] += total_area * PRICES["electric_wire"][1]; costs["electric"][2] += total_area * PRICES["electric_wire"][2]
    costs["electric"][0] += sockets * PRICES["electric_point"][0]; costs["electric"][1] += sockets * PRICES["electric_point"][1]; costs["electric"][2] += sockets * PRICES["electric_point"][2]

    # 4. Двері
    if answers.get("entrance_door") == "Так": costs["doors"][0] += PRICES["door_entrance"][0]; costs["doors"][1] += PRICES["door_entrance"][1]; costs["doors"][2] += PRICES["door_entrance"][2]
    int_door = answers.get("interior_door", "")
    doors_count = int(answers.get('rooms_count', 0)) + int(answers.get('baths_count', 0))
    if "Прихований" in int_door: costs["doors"][0] += doors_count * PRICES["door_hidden"][0]; costs["doors"][1] += doors_count * PRICES["door_hidden"][1]; costs["doors"][2] += doors_count * PRICES["door_hidden"][2]
    elif "Стандарт" in int_door: costs["doors"][0] += doors_count * PRICES["door_std"][0]; costs["doors"][1] += doors_count * PRICES["door_std"][1]; costs["doors"][2] += doors_count * PRICES["door_std"][2]

    # 5. Оздоблення по кімнатах
    for zone_id in measurements.keys():
        floor_sq = get_sq(zone_id, "floor")
        wall_sq = get_sq(zone_id, "walls")
        prefix = zone_id.split('_')[0] if "room" not in zone_id and "bath" not in zone_id else zone_id
        is_bath = "bath" in prefix
        
        if is_bath:
            tile_sq = floor_sq * 4.5
            costs["baths"][0] += tile_sq * PRICES["bath_tile"][0]; costs["baths"][1] += tile_sq * PRICES["bath_tile"][1]; costs["baths"][2] += tile_sq * PRICES["bath_tile"][2]
            if answers.get(f"{prefix}_toilet", {}).get("type") == "Інсталяція": costs["baths"][0] += PRICES["bath_install"][0]; costs["baths"][1] += PRICES["bath_install"][1]; costs["baths"][2] += PRICES["bath_install"][2]
            tub_type = answers.get(f"{prefix}_tub", {}).get("type", "")
            if "Акрил" in tub_type or "Окремостояча" in tub_type: costs["baths"][0] += PRICES["bath_tub"][0]; costs["baths"][1] += PRICES["bath_tub"][1]; costs["baths"][2] += PRICES["bath_tub"][2]

        if not is_bath:
            f_type = answers.get(f"{prefix}_floor", "")
            if isinstance(f_type, dict): f_type = f_type.get("type", "")
            p_floor = [0,0,0]
            if "Ламінат" in f_type: p_floor = PRICES["room_lam"]
            elif "Кварц" in f_type: p_floor = PRICES["room_quartz"]
            elif "Керамограніт" in f_type or "Плитка" in f_type: p_floor = PRICES["room_keram"]
            elif "Паркет" in f_type: p_floor = PRICES["room_parket"]
            costs["rooms"][0] += floor_sq * p_floor[0]; costs["rooms"][1] += floor_sq * p_floor[1]; costs["rooms"][2] += floor_sq * p_floor[2]
            
            w_type = answers.get(f"{prefix}_walls", "")
            slopes_len = wall_sq * 0.35
            p_wall = [0,0,0]
            if "Шпалери" in w_type: p_wall = PRICES["wall_paper"]
            elif "Фарбування" in w_type: p_wall = PRICES["wall_paint"]
            elif "Штукатурка" in w_type or "Декор" in w_type: p_wall = PRICES["wall_stucco"]
            costs["rooms"][0] += wall_sq * p_wall[0]; costs["rooms"][1] += wall_sq * p_wall[1]; costs["rooms"][2] += wall_sq * p_wall[2]
            costs["rooms"][0] += slopes_len * p_wall[0]; costs["rooms"][1] += slopes_len * p_wall[1]; costs["rooms"][2] += slopes_len * p_wall[2]
            
            if floor_sq > 0:
                perimeter = math.sqrt(floor_sq) * 4
                base_t = answers.get("baseboard", "")
                p_base = [0,0,0]
                if "Стандартний" in base_t: p_base = PRICES["base_std"]
                elif "Тіньовий" in base_t: p_base = PRICES["base_shadow"]
                elif "Прихований" in base_t: p_base = PRICES["base_hidden"]
                costs["rooms"][0] += perimeter * p_base[0]; costs["rooms"][1] += perimeter * p_base[1]; costs["rooms"][2] += perimeter * p_base[2]
                if answers.get("ceiling_shadow") == "Так": costs["rooms"][0] += perimeter * PRICES["ceil_shadow_add"][0]

    ceil_t = answers.get("ceiling", "")
    p_ceil = [0,0,0]
    if "Натяжна" in ceil_t: p_ceil = PRICES["ceil_stretch"]
    elif "Гіпсокартон" in ceil_t: p_ceil = PRICES["ceil_gips"]
    costs["rooms"][0] += total_area * p_ceil[0]; costs["rooms"][1] += total_area * p_ceil[1]; costs["rooms"][2] += total_area * p_ceil[2]

    return { "total_work": round(sum(c[0] for c in costs.values())), "total_mat_min": round(sum(c[1] for c in costs.values())), "total_mat_max": round(sum(c[2] for c in costs.values())), "sockets": sockets, "costs": costs }
