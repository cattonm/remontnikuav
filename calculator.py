import math

def apply_virtual_measurements(data):
    """
    Залишаємо для сумісності з main.py. 
    Тут можна реалізувати автогенерацію площ стін, якщо фронтенд передав тільки підлогу.
    """
    return data

def calculate_budget(data, prices):
    costs = {
        "rough": [0.0, 0.0, 0.0],
        "electric": [0.0, 0.0, 0.0],
        "doors": [0.0, 0.0, 0.0],
        "rooms": [0.0, 0.0, 0.0],
        "baths": [0.0, 0.0, 0.0],
        "custom": [0.0, 0.0, 0.0] 
    }

    answers = data.get("answers", {})
    client_data = data.get("client", {})
    client_area = float(client_data.get("area", 0) or 0)

    def add_c(category, price_key, multiplier=1.0, tier=None):
        if price_key not in prices: return
        p = prices[price_key]
        w = float(p[0])
        m1 = float(p[1])
        m2 = float(p[2])
        
        tier_norm = ""
        if tier and isinstance(tier, str):
            tier_norm = tier.strip().upper()
        
        is_std = tier_norm in ["СТАНДАРТ", "S", "С", "STANDARD"]
        is_comf = tier_norm in ["КОМФОРТ", "C", "К", "COMFORT"]
        is_prem = tier_norm in ["ПРЕМІУМ", "ПРЕМИУМ", "P", "П", "PREMIUM"]

        m_c = (m1 + m2) / 2
        overrides_c = {
            "radiator": 6000, "ac": 27000, "bath_tub": 40000,
            "toilet_okrem": 10000, "toilet_install": 22000,
            "sink_cabinet": 20000, "boiler_100": 13800, "boiler_300": 13800, 
            "towel_dryer": 7500, "hygienic_shower": 6000, "mirror_led": 5500,
            "mixer_std": 6000, "mixer_hidden": 10000, "tech_washer": 25000,
            "tech_kitchen": 18000, "tech_osmos": 15000,
            "door_entrance_mdf": 30000, "door_entrance_armor": 30000
        }
        if price_key in overrides_c:
            m_c = overrides_c[price_key]

        if price_key == "mirror_led" and tier_norm:
            if is_std: w = 600
            elif is_comf: w = 1000
            elif is_prem: w = 2000

        if is_std:
            costs[category][0] += w * multiplier
            costs[category][1] += m1 * multiplier
            costs[category][2] += m1 * multiplier
        elif is_comf:
            costs[category][0] += w * multiplier
            costs[category][1] += m_c * multiplier
            costs[category][2] += m_c * multiplier
        elif is_prem:
            costs[category][0] += w * multiplier
            costs[category][1] += m2 * multiplier
            costs[category][2] += m2 * multiplier
        else:
            costs[category][0] += w * multiplier
            costs[category][1] += m1 * multiplier
            costs[category][2] += m2 * multiplier

    # === 1. ДЕМОНТАЖ ТА ЧОРНОВІ ВАРІАНТИ ===
    if answers.get("demo_entrance") == "Так": add_c("rough", "demo_door_ent", 1)
    if int(answers.get("demo_interior", 0) or 0) > 0: 
        add_c("rough", "demo_door_int", int(answers["demo_interior"]))
        
    demo_walls = answers.get("demo_build_walls") or {}
    if demo_walls:
        add_c("rough", "demo_walls", float(demo_walls.get("Демонтаж існуючих стін", 0) or 0))
        add_c("rough", "build_gkl", float(demo_walls.get("Монтаж: Гіпсокартон", 0) or 0))
        add_c("rough", "build_brick", float(demo_walls.get("Монтаж: Цегла (1/2)", 0) or 0))
        add_c("rough", "build_gazoblok", float(demo_walls.get("Монтаж: Газоблок", 0) or 0))

    demo_floor = answers.get("demo_floor") or {}
    if demo_floor:
        add_c("rough", "demo_floor_wood", float(demo_floor.get("Паркет / Дерев'яна", 0) or 0))
        add_c("rough", "demo_floor_lin", float(demo_floor.get("Лінолеум / Ламінат", 0) or 0))
        add_c("rough", "demo_screed", float(demo_floor.get("Стара стяжка", 0) or 0))

    # Рахуємо загальну площу стін динамічно на основі масиву кімнат
    rooms_list = answers.get("rooms") or []
    total_walls_area = sum([float(r.get("measurements", {}).get("walls", 0) or 0) for r in rooms_list])
    
    if answers.get("rough_plaster_done") == "Ні":
        if total_walls_area == 0: 
            total_walls_area = client_area * 2.5
        add_c("rough", "rough_plaster", total_walls_area)

    screed = answers.get("screed_done")
    screed_area = float(answers.get("screed_area", 0) or 0)
    if screed_area <= 0: 
        screed_area = client_area

    if "Мокра" in str(screed): add_c("rough", "screed_wet", screed_area)
    elif "Напівсуха" in str(screed): add_c("rough", "screed_dry", screed_area)

    # === 2. МЕРЕЖІ (ЕЛЕКТРИКА ТА САНТЕХНІКА) ===
    if answers.get("electricity_done") == "Ні":
        add_c("electric", "electric_wire", client_area)
        add_c("electric", "electric_point", client_area * 1.5)

    if answers.get("plumbing_done") == "Ні":
        baths_c = int(answers.get("baths_count", 0) or 0)
        if baths_c == 0:
            baths_c = sum([1 for r in rooms_list if r.get("type") == "bath"])
        add_c("rough", "plumbing", max(1, baths_c) * 5 + 3)

    # === 3. ДВЕРІ ===
    ent_door = answers.get("entrance_door") or {}
    if isinstance(ent_door, dict):
        tier = ent_door.get("tier")
        e_type = ent_door.get("type", "")
        if "Брон" in e_type: add_c("doors", "door_entrance_armor", 1, tier)
        elif e_type and e_type not in ["Ні", "Немає"]: add_c("doors", "door_entrance_mdf", 1, tier)

    int_door = answers.get("interior_door")
    rooms_c = int(answers.get("rooms_count", 0) or 0)
    baths_c = int(answers.get("baths_count", 0) or 0)
    door_count = rooms_c + baths_c
    if door_count == 0:
        door_count = len(rooms_list)
        
    if int_door == "Прихований монтаж": add_c("doors", "door_hidden", door_count)
    elif int_door == "Стандарт": add_c("doors", "door_std", door_count)

    # === 4. СТЕЛЯ ТА ПЛІНТУСИ ===
    ceil = answers.get("ceiling")
    if ceil == "Натяжна": add_c("rooms", "ceil_stretch", client_area)
    elif ceil == "Гіпсокартон": add_c("rooms", "ceil_gips", client_area)
    if answers.get("ceiling_shadow") == "Так": add_c("rooms", "ceil_shadow_add", client_area)

    baseb = answers.get("baseboard")
    perim = client_area * 1.2
    if baseb == "Прихований монтаж": add_c("rooms", "base_hidden", perim)
    elif baseb == "Тіньовий шов": add_c("rooms", "base_shadow", perim)
    elif baseb == "Стандартний": add_c("rooms", "base_std", perim)

    warm_f = answers.get("warm_floor") or []
    if isinstance(warm_f, list) and len(warm_f) > 0 and "Не потребується" not in warm_f:
        add_c("electric", "warm_floor_elec", len(warm_f) * 5)

    # === 5. ОБРОБКА ДИНАМІЧНОГО МАСИВУ ПРИМІЩЕНЬ ===
    for room in rooms_list:
        r_type = room.get("type", "room")
        is_bath = (r_type == "bath")
        cat = "baths" if is_bath else "rooms"
        
        meas = room.get("measurements") or {}
        f_area = float(meas.get("floor", 0) or 0)
        w_area = float(meas.get("walls", 0) or 0)
        
        # Підлога
        floor = room.get("floor")
        if floor == "Керамограніт": add_c(cat, "tile_floor_std", f_area)
        elif floor in ["Кварцвініл", "Кварц-вініл", "Кварц вініл"]: add_c(cat, "room_quartz", f_area)
        elif floor == "Ламінат": add_c(cat, "room_lam", f_area)
        elif floor == "Паркет": add_c(cat, "room_parket", f_area)
        elif floor == "Лінолеум": add_c(cat, "linoleum", f_area)
        elif floor == "Великоформатний керамограніт": add_c(cat, "tile_floor_large", f_area)
        elif floor == "Мозаїка": add_c(cat, "tile_floor_mosaic", f_area)
        
        # Стіни
        walls = room.get("walls") or []
        if "Шпалери" in walls: add_c(cat, "wall_paper", w_area)
        if "Декоративна штукатурка" in walls: add_c(cat, "wall_decor", w_area)
        if "Фарбування" in walls: add_c(cat, "wall_paint", w_area)
        if "Грунтовка без фарбування" in walls: add_c(cat, "wall_primer", w_area)
        if "Вагонка" in walls: add_c(cat, "wall_vagonka", w_area)
        if "Короїд" in walls: add_c(cat, "wall_koroid", w_area)
        if "Обшивка деревʼяними рейками" in walls: add_c(cat, "wood_rails", w_area)

        # Плитка на стінах (для санвузлів)
        w_tile = room.get("wall_tile")
        if w_tile == "Керамограніт/Плитка до 120*60": add_c(cat, "tile_wall_std", w_area)
        elif w_tile == "Великоформатний керамограніт": add_c(cat, "tile_wall_large", w_area)
        elif w_tile == "Мозаїка": add_c(cat, "tile_wall_mosaic", w_area)

        # Освітлення
        light = room.get("light") or []
        if "Точкове світло" in light: add_c("electric", "light_point", max(1, int(f_area/2)))
        if "Люстра" in light: add_c("electric", "light_chandelier", 1)
        if "Трек / Лінія" in light: add_c("electric", "light_track", max(1, int(f_area/3)))
        if "LED підсвітка" in light or "Декор підсвітка" in light: add_c("electric", "light_led", 5)

        # Декор
        decor = room.get("decor")
        if decor in ["Панелі гіпсові", "Панелі ДСП", "ДСП панелі"]: 
            add_c(cat, "wall_decor_panels", max(1, int(w_area/4)))

        # Фартух кухні
        if room.get("apron") == "Керамограніт": add_c(cat, "kitchen_apron", 3)
        
        # Змішувачі
        if room.get("mixer_std"): add_c(cat, "mixer_std", float(room.get("mixer_std", 0)))
        if room.get("mixer_hidden"): add_c(cat, "mixer_hidden", float(room.get("mixer_hidden", 0)))

        # Сантехніка (Ванна, Душ, Унітаз)
        shower = room.get("shower") or []
        if "Піддон (акрил/камінь)" in shower: add_c(cat, "shower_tray", 1)
        if "Душовий трап (з плитки)" in shower: add_c(cat, "shower_trap", 1)
        if "Скляна перегородка" in shower: add_c(cat, "shower_glass", 1)
        if "Скляна конструкція з дверима" in shower: add_c(cat, "shower_doors", 1)
        
        tub = room.get("tub") or {}
        if isinstance(tub, dict) and tub.get("type") not in [None, "Ні", "Немає", "Не потрібно"]:
            add_c(cat, "bath_tub", 1, tub.get("tier"))
            
        toilet = room.get("toilet") or {}
        if isinstance(toilet, dict):
            t_type = toilet.get("type", "")
            t_tier = toilet.get("tier")
            if "Інсталяція" in t_type or "Підвісний" in t_type: 
                add_c(cat, "toilet_install", 1, t_tier)
            elif t_type and t_type not in ["Ні", "Немає", "Не потрібно"]: 
                add_c(cat, "toilet_okrem", 1, t_tier)

        # Підвіконня
        sills = room.get("sills")
        if sills == "Пластик": add_c("rooms", "sill_plastic", 1)
        elif sills == "Дерево": add_c("rooms", "sill_wood", 1)
        elif sills == "Штучний камінь": add_c("rooms", "sill_stone", 1)

        # Інше додаткове обладнання (Радіатори, Клімат, Техніка)
        other = room.get("other") or {}
        for k, v in other.items():
            tier = v if isinstance(v, str) else None
            if "Радіатор" in k: add_c("rough", "radiator", 1, tier)
            elif "Кондиціонер" in k: add_c("electric", "ac", 1, tier)
            elif "Звукоізоляція" in k: add_c(cat, "soundproof", w_area)
            elif "Утеплення" in k: add_c("rough", "balcony_warm", f_area)
            elif "Робоче місце" in k: add_c(cat, "balcony_workspace", 1)
            elif "Зовнішнє скління" in k: add_c(cat, "balcony_glazing_outer", float(v) if str(v).replace('.','').isdigit() else 1)
            elif "Балконний блок" in k: add_c(cat, "balcony_glazing_block", float(v) if str(v).replace('.','').isdigit() else 1)
            elif any(x in k for x in ["Посудомийн", "Пральн", "Сушильн"]): add_c(cat, "tech_washer", 1, tier)
            elif any(x in k for x in ["Осмос", "Подрібнювач"]): add_c(cat, "tech_osmos", 1, tier)
            elif any(x in k for x in ["Духов", "Мікрохвильов"]): add_c(cat, "tech_kitchen", 1, tier)
            elif "Гігієнічний душ" in k: add_c(cat, "hygienic_shower", 1, tier)
            elif "Бойлер" in k and "300" in k: add_c(cat, "boiler_300", 1, tier)
            elif "Бойлер" in k: add_c(cat, "boiler_100", 1, tier)
            elif "Умивальник" in k or "Раковина" in k: add_c(cat, "sink_cabinet", 1, tier)
            elif "Дзеркало" in k: add_c(cat, "mirror_led", 1, tier)
            elif "Рушникосушка" in k: add_c(cat, "towel_dryer", 1, tier)

    # === 6. НЕСТАНДАРТНІ РОБОТИ ===
    custom_works = answers.get("custom_works") or []
    for cw in custom_works:
        calc_type = cw.get("calc_type", "Фіксована ціна")
        w_price = float(cw.get("work_price", 0) or 0)
        m_price = float(cw.get("mat_price", 0) or 0)
        zone_id = cw.get("zone_id") # зв'язка йде через унікальний id кімнати
        multiplier = 1.0

        if calc_type in ["За м² підлоги", "За м² стін"] and zone_id:
            target_room = next((r for r in rooms_list if r.get("id") == zone_id), None)
            if target_room:
                r_meas = target_room.get("measurements", {})
                multiplier = float(r_meas.get("floor", 0) or 0) if calc_type == "За м² підлоги" else float(r_meas.get("walls", 0) or 0)
            else:
                multiplier = 0.0

        costs["custom"][0] += w_price * multiplier
        costs["custom"][1] += m_price * multiplier
        costs["custom"][2] += m_price * multiplier

    total_work = sum(v[0] for v in costs.values())
    total_mat_min = sum(v[1] for v in costs.values())
    total_mat_max = sum(v[2] for v in costs.values())

    return {
        "total_work": total_work,
        "total_mat_min": total_mat_min,
        "total_mat_max": total_mat_max,
        "costs": costs
