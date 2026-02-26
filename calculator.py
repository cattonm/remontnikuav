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
    if not tier_str or tier_str == "-" or tier_str == "Standard" or tier_str == "S": return mat_min
    if tier_str == "Premium" or tier_str == "P": return mat_max
    if tier_str == "Comfort" or tier_str == "C": return mat_min + (mat_max - mat_min) * 0.4
    return mat_min

def calculate_budget(data_json, PRICES):
    costs = { "rough": [0,0,0], "electric": [0,0,0], "doors": [0,0,0], "rooms": [0,0,0], "baths": [0,0,0], "logistics": [0,0,0] }
    client = data_json.get("client", {})
    answers = data_json.get("answers", {})
    measurements = answers.get("measurements", {})
    total_area = float(client.get("area", 0) or 0)
    
    def get_sq(zone_id, key):
        try: return float(measurements.get(zone_id, {}).get(key, 0))
        except: return 0.0

    # 1. Логістика
    costs["logistics"][0] += total_area * PRICES["logistics_base"][0]

    # 2. Чорнові
    if answers.get("rough_plaster_done") == "Так":
        wa = total_area * 2.5
        costs["rough"][0] += wa * PRICES["rough_plaster"][0]; costs["rough"][1] += wa * PRICES["rough_plaster"][1]; costs["rough"][2] += wa * PRICES["rough_plaster"][2]
    
    if answers.get("plumbing_done") == "Ні": costs["rough"][0] += total_area * PRICES["plumbing"][0]; costs["rough"][1] += total_area * PRICES["plumbing"][1]; costs["rough"][2] += total_area * PRICES["plumbing"][2]

    # 3. Електрика / Тепла підлога
    sockets = (int(answers.get('rooms_count', 0)) * 8) + (int(answers.get('baths_count', 0)) * 4) + 14
    wf_zones = answers.get('warm_floor', [])
    valid_wf = [z for z in wf_zones if z != "Не потребується"]
    if valid_wf:
        wf_a = total_area * 0.5
        sockets += len(valid_wf)
        costs["electric"][0] += wf_a * PRICES["warm_floor_elec"][0]; costs["electric"][1] += wf_a * PRICES["warm_floor_elec"][1]; costs["electric"][2] += wf_a * PRICES["warm_floor_elec"][2]

    costs["electric"][0] += sockets * PRICES["electric_point"][0]
    costs["electric"][1] += sockets * PRICES["electric_point"][1]

    # 4. Двері
    ent_door = answers.get("entrance_door", {})
    if isinstance(ent_door, dict) and ent_door.get("type") and "Ні" not in ent_door.get("type"):
        pk = "door_entrance_mdf" if "МДФ" in ent_door.get("type") else "door_entrance_armor"
        costs["doors"][0] += PRICES[pk][0]; mat = get_tier_price(PRICES[pk], ent_door.get("tier")); costs["doors"][1] += mat; costs["doors"][2] += mat

    # 5. Зони
    for zid in measurements.keys():
        fsq = get_sq(zid, "floor"); wsq = get_sq(zid, "walls")
        pref = zid.split('_')[0] if "room" not in zid and "bath" not in zid else zid
        is_bath = "bath" in pref; cat = "baths" if is_bath else "rooms"
        
        # Плитка (Логіка за новими тарифами)
        ftype = answers.get(f"{pref}_floor", "")
        if isinstance(ftype, dict): ftype = ftype.get("type", "")
        if "Плитка" in ftype or "Керамограніт" in ftype or "Мозаїка" in ftype:
            pk = "tile_floor_std"
            if "Мозаїка" in ftype: pk = "tile_floor_mosaic"
            elif "Великоформатний" in ftype: pk = "tile_floor_large"
            costs[cat][0] += fsq * PRICES[pk][0]; costs[cat][1] += fsq * PRICES[pk][1]; costs[cat][2] += fsq * PRICES[pk][2]
        elif "Ламінат" in ftype: pk = "room_lam"; costs[cat][0] += fsq * PRICES[pk][0]; costs[cat][1] += fsq * PRICES[pk][1]
        
        if is_bath:
            # Стіни в санвузлі
            wtype = answers.get(f"{pref}_wall_tile", "")
            if isinstance(wtype, dict): wtype = wtype.get("type", "")
            pk_w = "tile_wall_std"
            if "Мозаїка" in wtype: pk_w = "tile_wall_mosaic"
            elif "Великоформатний" in wtype: pk_w = "tile_wall_large"
            costs["baths"][0] += wsq * PRICES[pk_w][0]; costs["baths"][1] += wsq * PRICES[pk_w][1]; costs["baths"][2] += wsq * PRICES[pk_w][2]
            
            # Сантехніка
            for item in ["toilet_install", "bath_tub", "boiler"]:
                costs["baths"][0] += PRICES[item][0] # спрощено для прикладу

        else:
            # Стіни в кімнатах
            wtype = answers.get(f"{pref}_walls", "")
            pk_w = "wall_paper" if "Шпалери" in wtype else ("wall_decor" if "Декоративна" in wtype else "wall_paint")
            costs["rooms"][0] += wsq * PRICES[pk_w][0]; costs["rooms"][1] += wsq * PRICES[pk_w][1]

    return { "total_work": round(sum(c[0] for c in costs.values())), "total_mat_min": round(sum(c[1] for c in costs.values())), "total_mat_max": round(sum(c[2] for c in costs.values())), "sockets": sockets, "costs": costs }
