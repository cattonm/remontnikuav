import math

def apply_virtual_measurements(data):
    return data

def calculate_budget(data, prices):
    costs = {
        "rough": [0, 0, 0],
        "electric": [0, 0, 0],
        "doors": [0, 0, 0],
        "rooms": [0, 0, 0],
        "baths": [0, 0, 0],
        "custom": [0, 0, 0] 
    }

    answers = data.get("answers", {})
    meas = answers.get("measurements", {})
    client_area = float(data.get("client", {}).get("area", 0) or 0)

    def add_c(category, price_key, multiplier=1.0, tier=None):
        if price_key not in prices: return
        p = prices[price_key]
        w = p[0]
        m1 = p[1]
        m2 = p[2]
        
        # Кастомні ціни для "Комфорт" згідно з ТЗ
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
            
        # Унікальна логіка для дзеркала (змінюється робота)
        if price_key == "mirror_led" and tier:
            if tier == "Стандарт": w = 600
            elif tier == "Комфорт": w = 1000
            elif tier == "Преміум": w = 2000

        # Визначаємо, яку цифру брати для матеріалу
        if tier == "Стандарт":
            costs[category][0] += w * multiplier
            costs[category][1] += m1 * multiplier
            costs[category][2] += m1 * multiplier
        elif tier == "Комфорт":
            costs[category][0] += w * multiplier
            costs[category][1] += m_c * multiplier
            costs[category][2] += m_c * multiplier
        elif tier == "Преміум":
            costs[category][0] += w * multiplier
            costs[category][1] += m2 * multiplier
            costs[category][2] += m2 * multiplier
        else:
            # Якщо клас не вказаний, залишаємо "вилку" (мінімум і максимум)
            costs[category][0] += w * multiplier
            costs[category][1] += m1 * multiplier
            costs[category][2] += m2 * multiplier

    # === 1. ДЕМОНТАЖ ТА ЧОРНОВІ ===
    if answers.get("demo_entrance") == "Так": add_c("rough", "demo_door_ent", 1)
    if int(answers.get("demo_interior", 0) or 0) > 0: add_c("rough", "demo_door_int", int(answers["demo_interior"]))
        
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

    if answers.get("rough_plaster_done") == "Так":
        total_walls = sum([float(m.get("walls", 0) or 0) for m in meas.values()])
        if total_walls == 0: total_walls = client_area * 2.5
        add_c("rough", "rough_plaster", total_walls)

    screed = answers.get("screed_done")
    try:
        screed_area_raw = answers.get("screed_area")
        if screed_area_raw in [None, "", "0", 0]: screed_area = client_area
        else: screed_area = float(screed_area_raw)
    except: screed_area = client_area

    if screed == "Потрібна: Мокра": add_c("rough", "screed_wet", screed_area)
    elif screed == "Потрібна: Напівсуха": add_c("rough", "screed_dry", screed_area)

    # === 2. ЕЛЕКТРИКА ТА САНТЕХНІКА ===
        
    if answers.get("electricity_done") == "Ні":
        add_c("electric", "electric_wire", client_area)
        add_c("electric", "electric_point", client_area * 1.5)

    if answers.get("plumbing_done") == "Ні":
        baths_c = int(answers.get("baths_count", 0) or 0)
        add_c("rough", "plumbing", baths_c * 5 + 3)

    # === 3. ДВЕРІ ===
    ent_door = answers.get("entrance_door") or {}
    if isinstance(ent_door, dict):
        tier = ent_door.get("tier")
        if ent_door.get("type") == "МДФ": add_c("doors", "door_entrance_mdf", 1, tier)
        elif ent_door.get("type") == "Броньовані": add_c("doors", "door_entrance_armor", 1, tier)

    int_door = answers.get("interior_door")
    rooms_c = int(answers.get("rooms_count", 0) or 0)
    baths_c = int(answers.get("baths_count", 0) or 0)
    door_count = rooms_c + baths_c
    if int_door == "Прихований монтаж": add_c("doors", "door_hidden", door_count)
    elif int_door == "Стандарт": add_c("doors", "door_std", door_count)

    # === 4. ЗАГАЛЬНЕ ОЗДОБЛЕННЯ ===
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

    # === 5. КІМНАТИ ТА ПРИМІЩЕННЯ ===
    def process_room(zone_id, zone_data, is_bath=False):
        m = meas.get(zone_id) or {}
        f_area = float(m.get("floor", 0) or 0)
        w_area = float(m.get("walls", 0) or 0)
        cat = "baths" if is_bath else "rooms"
        
        floor = zone_data.get("floor")
        if floor == "Керамограніт": add_c(cat, "tile_floor_std", f_area)
        elif floor in ["Кварцвініл", "Кварц-вініл", "Кварц вініл"]: add_c(cat, "room_quartz", f_area)
        elif floor == "Ламінат": add_c(cat, "room_lam", f_area)
        elif floor == "Паркет": add_c(cat, "room_parket", f_area)
        elif floor == "Лінолеум": add_c(cat, "linoleum", f_area)
        elif floor == "Великоформатний керамограніт": add_c(cat, "tile_floor_large", f_area)
        elif floor == "Мозаїка": add_c(cat, "tile_floor_mosaic", f_area)
        
        walls = zone_data.get("walls") or []
        if "Шпалери" in walls: add_c(cat, "wall_paper", w_area)
        if "Декоративна штукатурка" in walls: add_c(cat, "wall_decor", w_area)
        if "Фарбування" in walls: add_c(cat, "wall_paint", w_area)
        if "Грунтовка без фарбування" in walls: add_c(cat, "wall_primer", w_area)
        if "Вагонка" in walls: add_c(cat, "wall_vagonka", w_area)
        if "Короїд" in walls: add_c(cat, "wall_koroid", w_area)
        if "Обшивка деревʼяними рейками" in walls: add_c(cat, "wood_rails", w_area)

        w_tile = zone_data.get("wall_tile")
        if w_tile == "Керамограніт/Плитка до 120*60": add_c(cat, "tile_wall_std", w_area)
        elif w_tile == "Великоформатний керамограніт": add_c(cat, "tile_wall_large", w_area)
        elif w_tile == "Мозаїка": add_c(cat, "tile_wall_mosaic", w_area)

        light = zone_data.get("light") or []
        if "Точкове світло" in light: add_c("electric", "light_point", max(1, int(f_area/2)))
        if "Люстра" in light: add_c("electric", "light_chandelier", 1)
        if "Трек / Лінія" in light: add_c("electric", "light_track", max(1, int(f_area/3)))
        if "LED підсвітка" in light or "Декор підсвітка" in light: add_c("electric", "light_led", 5)

        decor = zone_data.get("decor")
        if decor in ["Панелі гіпсові", "Панелі ДСП", "ДСП панелі"]: add_c(cat, "wall_decor_panels", max(1, int(w_area/4)))

        other = zone_data.get("other") or {}
        for k, v in other.items():
            # Витягуємо клас (Стандарт/Комфорт/Преміум), якщо він є
            tier = v if isinstance(v, str) and v in ["Стандарт", "Комфорт", "Преміум"] else None
            
            if k == "Радіатор": add_c("rough", "radiator", 1, tier)
            elif k == "Кондиціонер": add_c("electric", "ac", 1, tier)
            elif k == "Звукоізоляція": add_c(cat, "soundproof", w_area)
            elif k == "Утеплення": add_c("rough", "balcony_warm", f_area)
            elif k == "Робоче місце": add_c(cat, "balcony_workspace", 1)
            elif k == "Зовнішнє скління": add_c(cat, "balcony_glazing_outer", float(v) if str(v).replace('.','').isdigit() else 1)
            elif k == "Балконний блок": add_c(cat, "balcony_glazing_block", float(v) if str(v).replace('.','').isdigit() else 1)
            elif k in ["Посудомийна машина", "Пральна машина", "Сушильна машина"]: add_c(cat, "tech_washer", 1, tier)
            elif k in ["Осмос", "Подрібнювач"]: add_c(cat, "tech_osmos", 1, tier)
            elif k in ["Духова шафа", "Мікрохвильова піч"]: add_c(cat, "tech_kitchen", 1, tier)
            elif k == "Гігієнічний душ": add_c(cat, "hygienic_shower", 1, tier)
            elif k == "Бойлер до 100л": add_c(cat, "boiler_100", 1, tier)
            elif k == "Бойлер непрямого нагріву (до 300л)": add_c(cat, "boiler_300", 1, tier)
            elif k == "Умивальник з тумбою": add_c(cat, "sink_cabinet", 1, tier)
            elif k in ["Дзеркало з підігрівом", "Дзеркало"]: add_c(cat, "mirror_led", 1, tier)
            elif k == "Рушникосушка": add_c(cat, "towel_dryer", 1, tier)

        if zone_data.get("apron") == "Керамограніт": add_c(cat, "kitchen_apron", 3)
        add_c(cat, "mixer_std", float(zone_data.get("mixer_std", 0) or 0))
        add_c(cat, "mixer_hidden", float(zone_data.get("mixer_hidden", 0) or 0))

        shower = zone_data.get("shower") or []
        if "Піддон (акрил/камінь)" in shower: add_c(cat, "shower_tray", 1)
        if "Душовий трап (з плитки)" in shower: add_c(cat, "shower_trap", 1)
        if "Скляна перегородка" in shower: add_c(cat, "shower_glass", 1)
        if "Скляна конструкція з дверима" in shower: add_c(cat, "shower_doors", 1)
        
        tub = zone_data.get("tub") or {}
        if isinstance(tub, dict) and tub.get("type") in ["Акрил", "Гідро масаж", "Окремостояча"]: 
            add_c(cat, "bath_tub", 1, tub.get("tier"))
        
        toilet = zone_data.get("toilet") or {}
        if isinstance(toilet, dict):
            t_tier = toilet.get("tier")
            if toilet.get("type") == "Окремостоячий": add_c(cat, "toilet_okrem", 1, t_tier)
            elif toilet.get("type") == "Інсталяція": add_c(cat, "toilet_install", 1, t_tier)

        sills = zone_data.get("sills")
        if sills == "Пластик": add_c("rooms", "sill_plastic", 1)
        elif sills == "Дерево": add_c("rooms", "sill_wood", 1)
        elif sills == "Штучний камінь": add_c("rooms", "sill_stone", 1)

    for i in range(1, rooms_c + 1):
        room_data = {
            "floor": answers.get(f"room_{i}_floor"),
            "walls": answers.get(f"room_{i}_walls") or [],
            "light": answers.get(f"room_{i}_light") or [],
            "sills": answers.get(f"room_{i}_sills"),
            "decor": answers.get(f"room_{i}_decor"),
            "other": answers.get(f"room_{i}_other") or {}
        }
        process_room(f"room_{i}", room_data)

    for i in range(1, baths_c + 1):
        bath_data = {
            "floor": answers.get(f"bath_{i}_floor"),
            "wall_tile": answers.get(f"bath_{i}_wall_tile"),
            "shower": answers.get(f"bath_{i}_shower") or [],
            "tub": answers.get(f"bath_{i}_tub") or {},
            "toilet": answers.get(f"bath_{i}_toilet") or {},
            "mixer_std": answers.get(f"bath_{i}_mixer_std"),
            "mixer_hidden": answers.get(f"bath_{i}_mixer_hidden"),
            "other": answers.get(f"bath_{i}_other") or {}
        }
        process_room(f"bath_{i}", bath_data, is_bath=True)

    aux_map = {"Передпокій": "hallway", "Кухня": "kitchen", "Балкон": "balcony", "Гардероб": "wardrobe", "Підвал": "basement", "Горище": "attic"}
    aux_rooms = answers.get("aux_rooms") or []
    for a in aux_rooms:
        prefix = aux_map.get(a)
        if prefix:
            aux_data = {
                "floor": answers.get(f"{prefix}_floor"),
                "walls": answers.get(f"{prefix}_walls") or [],
                "light": answers.get(f"{prefix}_light") or [],
                "decor": answers.get(f"{prefix}_decor"),
                "apron": answers.get(f"{prefix}_apron"),
                "mixer_std": answers.get(f"{prefix}_mixer_std"),
                "mixer_hidden": answers.get(f"{prefix}_mixer_hidden"),
                "other": answers.get(f"{prefix}_other") or {}
            }
            process_room(prefix, aux_data)

    # === 6. НЕСТАНДАРТНІ РОБОТИ ===
    custom_works = answers.get("custom_works") or []
    for cw in custom_works:
        calc_type = cw.get("calc_type", "Фіксована ціна")
        w_price = float(cw.get("work_price", 0) or 0)
        m_price = float(cw.get("mat_price", 0) or 0)
        zone = cw.get("zone", "Загальні")
        multiplier = 1.0

        if calc_type in ["За м² підлоги", "За м² стін"]:
            zone_key = None
            if zone == "Передпокій": zone_key = "hallway"
            elif zone == "Кухня": zone_key = "kitchen"
            elif zone == "Балкон": zone_key = "balcony"
            elif zone == "Гардероб": zone_key = "wardrobe"
            elif zone == "Підвал": zone_key = "basement"
            elif zone == "Горище": zone_key = "attic"
            elif zone.startswith("Кімната"): zone_key = f"room_{zone.split()[1]}"
            elif zone.startswith("Санвузол"): zone_key = f"bath_{zone.split()[1]}"

            if zone_key and zone_key in meas:
                multiplier = float(meas[zone_key].get("floor", 0) or 0) if calc_type == "За м² підлоги" else float(meas[zone_key].get("walls", 0) or 0)
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
    }
