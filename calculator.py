import math
import copy

def apply_virtual_measurements(data_json):
    data = copy.deepcopy(data_json)
    total_area = float(data.get("client", {}).get("area", 0) or 0)
    if total_area <= 0: return data

    ans = data.get("answers", {})
    meas = ans.get("measurements", {})
    aux = ans.get("aux_rooms", [])
    rooms_c = int(ans.get("rooms_count", 0) or 0)
    baths_c = int(ans.get("baths_count", 0) or 0)
    
    used_area = 0
    if "Передпокій" in aux and not meas.get("hallway"): sq = total_area * 0.10; meas["hallway"] = {"floor": sq, "walls": sq * 2.5}; used_area += sq
    if "Кухня" in aux and not meas.get("kitchen"): sq = total_area * 0.20; meas["kitchen"] = {"floor": sq, "walls": sq * 2.5}; used_area += sq
    for i in range(1, baths_c + 1):
        if not meas.get(f"bath_{i}"): sq = 4.5; meas[f"bath_{i}"] = {"floor": sq, "walls": sq * 2.5}; used_area += sq
    if "Балкон" in aux and not meas.get("balcony"): sq = 3.5; meas["balcony"] = {"floor": sq, "walls": sq * 2.5}; used_area += sq
    if "Гардероб" in aux and not meas.get("wardrobe"): sq = 3.5; meas["wardrobe"] = {"floor": sq, "walls": sq * 2.5}; used_area += sq
    if "Підвал" in aux and not meas.get("basement"): sq = total_area * 0.15; meas["basement"] = {"floor": sq, "walls": sq * 2.5}
    if "Горище" in aux and not meas.get("attic"): sq = total_area * 0.3; meas["attic"] = {"floor": sq, "walls": sq * 2.5}
        
    rem_area = max(0, total_area - used_area)
    if rooms_c > 0:
        room_sq = rem_area / rooms_c
        for i in range(1, rooms_c + 1):
            if not meas.get(f"room_{i}"): meas[f"room_{i}"] = {"floor": room_sq, "walls": room_sq * 2.5}
            
    ans["measurements"] = meas
    data["answers"] = ans
    return data

def get_tier_price(base_price_tuple, tier_str):
    mat_min = base_price_tuple[1]
    mat_max = base_price_tuple[2]
    if not tier_str or tier_str == "-" or tier_str == "Standard" or tier_str == "S": 
        return mat_min, mat_min
    if tier_str == "Premium" or tier_str == "P": 
        return mat_max, mat_max
    if tier_str == "Comfort" or tier_str == "C": 
        avg = mat_min + (mat_max - mat_min) * 0.4
        return avg, avg
    return mat_min, mat_min

def calculate_budget(data_json, PRICES):
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

    # --- 1. ЛОГІСТИКА (ВИМКНЕНО) ---
    # log_w = total_area * PRICES["logistics_base"][0]
    # if elevator == "Немає" and floor > 1: log_w += (total_area * PRICES["logistics_stair"][0] * floor)
    # elif elevator == "Пасажирський": log_w += (total_area * PRICES["logistics_elev"][0] * floor)
    # costs["logistics"][0] += log_w

    # --- 2. ЧОРНОВІ РОБОТИ ---
    screed = answers.get("screed_done", "")
    if "Мокра" in screed: costs["rough"][0] += total_area * PRICES["screed_wet"][0]; costs["rough"][1] += total_area * PRICES["screed_wet"][1]; costs["rough"][2] += total_area * PRICES["screed_wet"][2]
    elif "Напівсуха" in screed: costs["rough"][0] += total_area * PRICES["screed_dry"][0]; costs["rough"][1] += total_area * PRICES["screed_dry"][1]; costs["rough"][2] += total_area * PRICES["screed_dry"][2]
    
    if answers.get("plumbing_done") == "Ні": costs["rough"][0] += total_area * PRICES["plumbing"][0]; costs["rough"][1] += total_area * PRICES["plumbing"][1]; costs["rough"][2] += total_area * PRICES["plumbing"][2]
    
    if answers.get("rough_plaster_done") == "Так":
        walls_area = total_area * 2.5
        costs["rough"][0] += walls_area * PRICES["rough_plaster"][0]; costs["rough"][1] += walls_area * PRICES["rough_plaster"][1]; costs["rough"][2] += walls_area * PRICES["rough_plaster"][2]

    # --- 3. ЕЛЕКТРИКА ТА ОПАЛЕННЯ ---
    sockets = 0
    rooms_count = int(answers.get('rooms_count', 0))
    baths_count = int(answers.get('baths_count', 0))
    sockets += rooms_count * 8
    sockets += baths_count * 4
    sockets += 14 # Кухня + Коридор
    
    wf_zones = answers.get('warm_floor', [])
    valid_zones = [z for z in wf_zones if z != "Не потребується"]
    if valid_zones:
        wf_area = total_area * 0.5
        sockets += max(1, len(valid_zones))
        costs["electric"][0] += wf_area * PRICES["warm_floor_elec"][0]; costs["electric"][1] += wf_area * PRICES["warm_floor_elec"][1]; costs["electric"][2] += wf_area * PRICES["warm_floor_elec"][2]

    for tech in ["Посудомийна машина", "Подрібнювач відходів", "Мікрохвильова піч", "Духова шафа", "Осмос", "Пральна машина", "Сушильна машина", "Бойлер"]:
        for zone in answers:
            if type(answers[zone]) == dict and tech in answers[zone]: sockets += 1

    if answers.get("electricity_done") == "Ні":
        costs["electric"][0] += total_area * PRICES["electric_wire"][0]; costs["electric"][1] += total_area * PRICES["electric_wire"][1]; costs["electric"][2] += total_area * PRICES["electric_wire"][2]
    
    costs["electric"][0] += sockets * PRICES["electric_point"][0]; costs["electric"][1] += sockets * PRICES["electric_point"][1]; costs["electric"][2] += sockets * PRICES["electric_point"][2]

    # --- 4. ДВЕРІ ---
    ent_door = answers.get("entrance_door", {})
    if isinstance(ent_door, dict) and ent_door.get("type") and "Ні" not in ent_door.get("type"):
        tier = ent_door.get("tier")
        pk = "door_entrance_mdf" if "МДФ" in ent_door.get("type") else "door_entrance_armor"
        costs["doors"][0] += PRICES[pk][0]
        mat_min, mat_max = get_tier_price(PRICES[pk], tier)
        costs["doors"][1] += mat_min; costs["doors"][2] += mat_max
    elif isinstance(ent_door, str) and ent_door == "Так":
        costs["doors"][0] += PRICES["door_entrance_mdf"][0]; costs["doors"][1] += PRICES["door_entrance_mdf"][1]; costs["doors"][2] += PRICES["door_entrance_mdf"][2]

    int_door = answers.get("interior_door", "")
    doors_total = rooms_count + baths_count + (1 if 'Гардероб' in answers.get('aux_rooms', []) else 0)
    if "Прихований" in int_door: costs["doors"][0] += doors_total * PRICES["door_hidden"][0]; costs["doors"][1] += doors_total * PRICES["door_hidden"][1]; costs["doors"][2] += doors_total * PRICES["door_hidden"][2]
    elif "Стандарт" in int_door: costs["doors"][0] += doors_total * PRICES["door_std"][0]; costs["doors"][1] += doors_total * PRICES["door_std"][1]; costs["doors"][2] += doors_total * PRICES["door_std"][2]

    # --- 5. ДЕТАЛІЗАЦІЯ ПО КІМНАТАХ ---
    for zone_id in measurements.keys():
        floor_sq = get_sq(zone_id, "floor")
        wall_sq = get_sq(zone_id, "walls")
        prefix = zone_id.split('_')[0] if "room" not in zone_id and "bath" not in zone_id else zone_id
        is_bath = "bath" in prefix
        c_cat = "baths" if is_bath else "rooms"
        
        lights = answers.get(f"{prefix}_light", [])
        if "Точкове світло" in lights and floor_sq > 0:
            count = max(1, floor_sq / 2.5)
            costs[c_cat][0] += count * PRICES["light_point"][0]; costs[c_cat][1] += count * PRICES["light_point"][1]; costs[c_cat][2] += count * PRICES["light_point"][2]
        if "Люстра" in lights:
            costs[c_cat][0] += PRICES["light_chandelier"][0]; costs[c_cat][1] += PRICES["light_chandelier"][1]; costs[c_cat][2] += PRICES["light_chandelier"][2]
        if "Трек / Лінія" in lights:
            costs[c_cat][0] += 4 * PRICES["light_track"][0]; costs[c_cat][1] += 4 * PRICES["light_track"][1]; costs[c_cat][2] += 4 * PRICES["light_track"][2]
        if "LED підсвітка" in lights or "Декор підсвітка" in lights:
            costs[c_cat][0] += 5 * PRICES["light_led"][0]; costs[c_cat][1] += 5 * PRICES["light_led"][1]; costs[c_cat][2] += 5 * PRICES["light_led"][2]

        mix_std = int(answers.get(f"{prefix}_mixer_std", 0) or 0)
        mix_hid = int(answers.get(f"{prefix}_mixer_hidden", 0) or 0)
        if mix_std > 0: costs[c_cat][0] += mix_std * PRICES["mixer_std"][0]; costs[c_cat][1] += mix_std * PRICES["mixer_std"][1]; costs[c_cat][2] += mix_std * PRICES["mixer_std"][2]
        if mix_hid > 0: costs[c_cat][0] += mix_hid * PRICES["mixer_hidden"][0]; costs[c_cat][1] += mix_hid * PRICES["mixer_hidden"][1]; costs[c_cat][2] += mix_hid * PRICES["mixer_hidden"][2]

        f_type = answers.get(f"{prefix}_floor", "")
        if isinstance(f_type, dict): f_type = f_type.get("type", "")
        if "Мозаїка" in f_type:
            pk = "tile_floor_mosaic"; costs[c_cat][0] += floor_sq * PRICES[pk][0]; costs[c_cat][1] += floor_sq * PRICES[pk][1]; costs[c_cat][2] += floor_sq * PRICES[pk][2]
        elif "Великоформат" in f_type:
            pk = "tile_floor_large"; costs[c_cat][0] += floor_sq * PRICES[pk][0]; costs[c_cat][1] += floor_sq * PRICES[pk][1]; costs[c_cat][2] += floor_sq * PRICES[pk][2]
        elif "Керамограніт" in f_type or "Плитка" in f_type:
            pk = "tile_floor_std"; costs[c_cat][0] += floor_sq * PRICES[pk][0]; costs[c_cat][1] += floor_sq * PRICES[pk][1]; costs[c_cat][2] += floor_sq * PRICES[pk][2]
        elif "Ламінат" in f_type:
            pk = "room_lam"; costs[c_cat][0] += floor_sq * PRICES[pk][0]; costs[c_cat][1] += floor_sq * PRICES[pk][1]; costs[c_cat][2] += floor_sq * PRICES[pk][2]
        elif "Кварц" in f_type:
            pk = "room_quartz"; costs[c_cat][0] += floor_sq * PRICES[pk][0]; costs[c_cat][1] += floor_sq * PRICES[pk][1]; costs[c_cat][2] += floor_sq * PRICES[pk][2]
        elif "Паркет" in f_type:
            pk = "room_parket"; costs[c_cat][0] += floor_sq * PRICES[pk][0]; costs[c_cat][1] += floor_sq * PRICES[pk][1]; costs[c_cat][2] += floor_sq * PRICES[pk][2]
        elif "Лінолеум" in f_type:
            pk = "linoleum"; costs[c_cat][0] += floor_sq * PRICES[pk][0]; costs[c_cat][1] += floor_sq * PRICES[pk][1]; costs[c_cat][2] += floor_sq * PRICES[pk][2]

        if is_bath:
            w_type = answers.get(f"{prefix}_wall_tile", "")
            if isinstance(w_type, dict): w_type = w_type.get("type", "")
            pk_w = "tile_wall_std"
            if "Мозаїка" in w_type: pk_w = "tile_wall_mosaic"
            elif "Великоформат" in w_type: pk_w = "tile_wall_large"
            costs["baths"][0] += wall_sq * PRICES[pk_w][0]; costs["baths"][1] += wall_sq * PRICES[pk_w][1]; costs["baths"][2] += wall_sq * PRICES[pk_w][2]

            toilet = answers.get(f"{prefix}_toilet", {})
            if toilet.get("type") == "Окремостоячий":
                m_min, m_max = get_tier_price(PRICES["toilet_okrem"], toilet.get("tier"))
                costs["baths"][0] += PRICES["toilet_okrem"][0]; costs["baths"][1] += m_min; costs["baths"][2] += m_max
            elif toilet.get("type") == "Інсталяція":
                m_min, m_max = get_tier_price(PRICES["toilet_install"], toilet.get("tier"))
                costs["baths"][0] += PRICES["toilet_install"][0]; costs["baths"][1] += m_min; costs["baths"][2] += m_max
            
            tub = answers.get(f"{prefix}_tub", {})
            if tub.get("type") and "Не обл" not in tub.get("type"):
                m_min, m_max = get_tier_price(PRICES["bath_tub"], tub.get("tier"))
                work = 7500 if tub.get("tier") in ["P", "Premium"] else PRICES["bath_tub"][0]
                costs["baths"][0] += work; costs["baths"][1] += m_min; costs["baths"][2] += m_max
                
            shower = answers.get(f"{prefix}_shower", [])
            if "Піддон (акрил/камінь)" in shower: costs["baths"][0] += PRICES["shower_tray"][0]; costs["baths"][1] += PRICES["shower_tray"][1]; costs["baths"][2] += PRICES["shower_tray"][2]
            if "Душовий трап (з плитки)" in shower: costs["baths"][0] += PRICES["shower_trap"][0]; costs["baths"][1] += PRICES["shower_trap"][1]; costs["baths"][2] += PRICES["shower_trap"][2]
            if "Скляна перегородка" in shower: costs["baths"][0] += PRICES["shower_glass"][0]; costs["baths"][1] += PRICES["shower_glass"][1]; costs["baths"][2] += PRICES["shower_glass"][2]
            if "Скляна конструкція з дверима" in shower: costs["baths"][0] += PRICES["shower_doors"][0]; costs["baths"][1] += PRICES["shower_doors"][1]; costs["baths"][2] += PRICES["shower_doors"][2]

            b_other = answers.get(f"{prefix}_other", {})
            for item, tier in b_other.items():
                if item == "Бойлер": costs["baths"][0] += PRICES["boiler"][0]; m_min, m_max = get_tier_price(PRICES["boiler"], tier); costs["baths"][1] += m_min; costs["baths"][2] += m_max
                elif item == "Рушникосушка": costs["baths"][0] += PRICES["towel_dryer"][0]; m_min, m_max = get_tier_price(PRICES["towel_dryer"], tier); costs["baths"][1] += m_min; costs["baths"][2] += m_max
                elif item == "Гігієнічний душ": costs["baths"][0] += PRICES["hygienic_shower"][0]; m_min, m_max = get_tier_price(PRICES["hygienic_shower"], tier); costs["baths"][1] += m_min; costs["baths"][2] += m_max
                elif item == "Дзеркало з підсвіткою" or item == "Дзеркало": m_min, m_max = get_tier_price(PRICES["mirror_led"], tier); work = 600 if tier in ['S','Standard','-'] else (1000 if tier in ['C','Comfort'] else 2000); costs["baths"][0] += work; costs["baths"][1] += m_min; costs["baths"][2] += m_max
                elif item == "Пральна машина" or item == "Сушильна машина": costs["baths"][0] += PRICES["tech_washer"][0]; m_min, m_max = get_tier_price(PRICES["tech_washer"], tier); costs["baths"][1] += m_min; costs["baths"][2] += m_max
                elif item == "Умивальник з тумбою": costs["baths"][0] += PRICES["sink_cabinet"][0]; m_min, m_max = get_tier_price(PRICES["sink_cabinet"], tier); costs["baths"][1] += m_min; costs["baths"][2] += m_max

        if not is_bath:
            w_type = answers.get(f"{prefix}_walls", "")
            slopes_len = wall_sq * 0.35
            p_wall = [0,0,0]
            if "Шпалери" in w_type: p_wall = PRICES["wall_paper"]
            elif "Фарбування" in w_type: p_wall = PRICES["wall_paint"]
            elif "Декоративна" in w_type: p_wall = PRICES["wall_decor"]
            elif "Побілка" in w_type: p_wall = PRICES["whitewash"]
            elif "рейками" in w_type: p_wall = PRICES["wood_rails"]
            costs["rooms"][0] += wall_sq * p_wall[0]; costs["rooms"][1] += wall_sq * p_wall[1]; costs["rooms"][2] += wall_sq * p_wall[2]
            costs["rooms"][0] += slopes_len * p_wall[0]; costs["rooms"][1] += slopes_len * p_wall[1]; costs["rooms"][2] += slopes_len * p_wall[2]
            
            sill = answers.get(f"{prefix}_sills", "")
            if "Пластик" in sill: costs["rooms"][0] += PRICES["sill_plastic"][0]; costs["rooms"][1] += PRICES["sill_plastic"][1]; costs["rooms"][2] += PRICES["sill_plastic"][2]
            elif "Дерево" in sill: costs["rooms"][0] += PRICES["sill_wood"][0]; costs["rooms"][1] += PRICES["sill_wood"][1]; costs["rooms"][2] += PRICES["sill_wood"][2]
            elif "Камінь" in sill: costs["rooms"][0] += PRICES["sill_stone"][0]; costs["rooms"][1] += PRICES["sill_stone"][1]; costs["rooms"][2] += PRICES["sill_stone"][2]

            apron = answers.get("kitchen_apron", "")
            if "Керамограніт" in apron: costs["rooms"][0] += PRICES["kitchen_apron"][0]; costs["rooms"][1] += PRICES["kitchen_apron"][1]; costs["rooms"][2] += PRICES["kitchen_apron"][1]
            elif "Матеріал" in apron: costs["rooms"][0] += PRICES["kitchen_apron"][0]; costs["rooms"][1] += PRICES["kitchen_apron"][2]; costs["rooms"][2] += PRICES["kitchen_apron"][2]
            
            decor = answers.get(f"{prefix}_decor", "")
            if decor and "ні" not in decor.lower() and "Ні" not in decor:
                costs["rooms"][0] += PRICES["wall_decor_panels"][0]; costs["rooms"][1] += PRICES["wall_decor_panels"][1]; costs["rooms"][2] += PRICES["wall_decor_panels"][2]

            if prefix == "balcony":
                b_other = answers.get("balcony_other", {})
                if "Утеплення" in b_other: costs["rooms"][0] += floor_sq * 3 * PRICES["balcony_warm"][0]; costs["rooms"][1] += floor_sq * 3 * PRICES["balcony_warm"][1]; costs["rooms"][2] += floor_sq * 3 * PRICES["balcony_warm"][2]
                if "Робоче місце" in b_other: costs["rooms"][0] += PRICES["balcony_workspace"][0]; costs["rooms"][1] += PRICES["balcony_workspace"][1]; costs["rooms"][2] += PRICES["balcony_workspace"][2]

            r_other = answers.get(f"{prefix}_other", {})
            for item, tier in r_other.items():
                if item == "Кондиціонер": costs["rooms"][0] += PRICES["ac"][0]; m_min, m_max = get_tier_price(PRICES["ac"], tier); costs["rooms"][1] += m_min; costs["rooms"][2] += m_max
                elif item == "Радіатор": costs["rooms"][0] += PRICES["radiator"][0]; m_min, m_max = get_tier_price(PRICES["radiator"], tier); costs["rooms"][1] += m_min; costs["rooms"][2] += m_max
                elif item == "Звукоізоляція": costs["rooms"][0] += wall_sq * PRICES["soundproof"][0]; costs["rooms"][1] += wall_sq * PRICES["soundproof"][1]; costs["rooms"][2] += wall_sq * PRICES["soundproof"][2]
                elif item in ["Посудомийна машина", "Мікрохвильова піч", "Духова шафа"]: costs["rooms"][0] += PRICES["tech_kitchen"][0]; m_min, m_max = get_tier_price(PRICES["tech_kitchen"], tier); costs["rooms"][1] += m_min; costs["rooms"][2] += m_max
                elif item == "Осмос" or item == "Подрібнювач відходів": costs["rooms"][0] += PRICES["tech_osmos"][0]; m_min, m_max = get_tier_price(PRICES["tech_osmos"], tier); costs["rooms"][1] += m_min; costs["rooms"][2] += m_max
                elif item == "Підсвітка робочої поверхні": costs["rooms"][0] += PRICES["kitchen_workspace_led"][0]; costs["rooms"][1] += PRICES["kitchen_workspace_led"][1]; costs["rooms"][2] += PRICES["kitchen_workspace_led"][2]

    ceil_t = answers.get("ceiling", "")
    if "Натяжна" in ceil_t: costs["rooms"][0] += total_area * PRICES["ceil_stretch"][0]; costs["rooms"][1] += total_area * PRICES["ceil_stretch"][1]; costs["rooms"][2] += total_area * PRICES["ceil_stretch"][2]
    elif "Гіпсокартон" in ceil_t: costs["rooms"][0] += total_area * PRICES["ceil_gips"][0]; costs["rooms"][1] += total_area * PRICES["ceil_gips"][1]; costs["rooms"][2] += total_area * PRICES["ceil_gips"][2]
    
    if answers.get("ceiling_shadow") == "Так":
        peri = math.sqrt(total_area) * 4
        costs["rooms"][0] += peri * PRICES["ceil_shadow_add"][0]; costs["rooms"][1] += peri * PRICES["ceil_shadow_add"][1]; costs["rooms"][2] += peri * PRICES["ceil_shadow_add"][2]
        
    base_t = answers.get("baseboard", "")
    if base_t and "Ні" not in base_t:
        peri = math.sqrt(total_area) * 4
        if "Стандартний" in base_t: pk = "base_std"
        elif "Тіньовий" in base_t: pk = "base_shadow"
        elif "Прихований" in base_t: pk = "base_hidden"
        costs["rooms"][0] += peri * PRICES[pk][0]; costs["rooms"][1] += peri * PRICES[pk][1]; costs["rooms"][2] += peri * PRICES[pk][2]
    
    return { "total_work": round(sum(c[0] for c in costs.values())), "total_mat_min": round(sum(c[1] for c in costs.values())), "total_mat_max": round(sum(c[2] for c in costs.values())), "sockets": sockets, "costs": costs }
