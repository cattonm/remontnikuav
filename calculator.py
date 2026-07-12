import math

def _num(v, default=0.0):
    """Безпечний float: '15' → 15.0; '' / None / 'abc' → default.
    Раніше голий float() на кривому полі кидав ValueError, ендпоінт
    відповідав 500, а фронтенд МОВЧКИ показував старі (нульові) суми."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

def _int(v, default=0):
    return int(_num(v, default))

# ==========================================================
# ДОВІДНИК ПОЗИЦІЙ: людська назва + одиниця виміру за ключем прайсу.
# Використовується для ПОСТРОЧНОЇ деталізації ("Ламінат · 18 м² × 405 ₴").
# Назву можна перевизначити з Google-таблиці (колонка "Назва") — вона
# приїжджає в calculate_budget через аргумент labels і має пріоритет.
# ==========================================================
UNIT_M2 = "м²"
UNIT_PCS = "шт"
UNIT_PT = "точок"

PRICE_META = {
    # Чорнові
    "screed_wet": ("Стяжка мокра", UNIT_M2), "screed_dry": ("Стяжка напівсуха", UNIT_M2),
    "rough_plaster": ("Штукатурка стін", UNIT_M2), "plumbing": ("Розводка сантехніки", UNIT_PT),
    "electric_wire": ("Електрика: кабель", UNIT_M2), "electric_point": ("Електрика: точки", UNIT_PT),
    "warm_floor_elec": ("Тепла підлога (електро)", UNIT_M2),
    "demo_door_ent": ("Демонтаж вхідних дверей", UNIT_PCS), "demo_door_int": ("Демонтаж міжкімнатних дверей", UNIT_PCS),
    "demo_walls": ("Демонтаж стін", UNIT_M2), "demo_floor_wood": ("Демонтаж дерев'яної підлоги", UNIT_M2),
    "demo_floor_lin": ("Демонтаж лінолеуму/ламінату", UNIT_M2), "demo_screed": ("Демонтаж стяжки", UNIT_M2),
    "build_gkl": ("Монтаж стін: гіпсокартон", UNIT_M2), "build_brick": ("Монтаж стін: цегла", UNIT_M2),
    "build_gazoblok": ("Монтаж стін: газоблок", UNIT_M2),
    "logistics_base": ("Логістика", UNIT_M2), "logistics_stair": ("Підйом сходами", UNIT_M2),
    "logistics_elev": ("Підйом ліфтом", UNIT_M2),
    # Двері
    "door_entrance_mdf": ("Вхідні двері (МДФ)", UNIT_PCS), "door_entrance_armor": ("Вхідні двері (броньовані)", UNIT_PCS),
    "door_hidden": ("Двері прихованого монтажу", UNIT_PCS), "door_std": ("Двері стандарт", UNIT_PCS),
    # Підлога
    "tile_floor_std": ("Керамограніт на підлогу", UNIT_M2), "tile_floor_large": ("Великоформатний керамограніт (підлога)", UNIT_M2),
    "tile_floor_mosaic": ("Мозаїка на підлогу", UNIT_M2), "room_lam": ("Ламінат", UNIT_M2),
    "room_quartz": ("Кварц-вініл", UNIT_M2), "room_parket": ("Паркет", UNIT_M2), "linoleum": ("Лінолеум", UNIT_M2),
    # Стіни
    "tile_wall_std": ("Плитка на стіни", UNIT_M2), "tile_wall_large": ("Великоформатний керамограніт (стіни)", UNIT_M2),
    "tile_wall_mosaic": ("Мозаїка на стіни", UNIT_M2), "wall_paper": ("Шпалери", UNIT_M2),
    "wall_paint": ("Фарбування стін", UNIT_M2), "wall_decor": ("Декоративна штукатурка", UNIT_M2),
    "wall_primer": ("Грунтовка стін", UNIT_M2), "wall_vagonka": ("Вагонка", UNIT_M2),
    "wall_koroid": ("Короїд", UNIT_M2), "wood_rails": ("Дерев'яні рейки", UNIT_M2),
    "wall_decor_panels": ("Декоративні панелі", UNIT_PCS), "soundproof": ("Звукоізоляція", UNIT_M2),
    "whitewash": ("Побілка", UNIT_M2),
    # Стеля / плінтус
    "ceil_stretch": ("Натяжна стеля", UNIT_M2), "ceil_gips": ("Гіпсокартонна стеля", UNIT_M2),
    "ceil_shadow_add": ("Тіньовий шов (стеля)", UNIT_M2),
    "base_std": ("Плінтус стандарт", UNIT_M2), "base_shadow": ("Тіньовий плінтус", UNIT_M2),
    "base_hidden": ("Прихований плінтус", UNIT_M2),
    # Світло
    "light_point": ("Точкове світло", UNIT_PT), "light_chandelier": ("Люстра", UNIT_PCS),
    "light_track": ("Трек / лінійне світло", UNIT_PT), "light_led": ("LED-підсвітка", UNIT_M2),
    "kitchen_workspace_led": ("Підсвітка робочої зони", UNIT_PCS),
    # Санвузол
    "toilet_install": ("Унітаз з інсталяцією", UNIT_PCS), "toilet_okrem": ("Унітаз окремостоячий", UNIT_PCS),
    "bath_tub": ("Ванна", UNIT_PCS), "shower_tray": ("Душовий піддон", UNIT_PCS),
    "shower_trap": ("Душовий трап", UNIT_PCS), "shower_glass": ("Скляна перегородка", UNIT_PCS),
    "shower_doors": ("Скляна конструкція з дверима", UNIT_PCS),
    "sink_cabinet": ("Умивальник з тумбою", UNIT_PCS), "mirror_led": ("Дзеркало з підсвіткою", UNIT_PCS),
    "towel_dryer": ("Рушникосушка", UNIT_PCS), "hygienic_shower": ("Гігієнічний душ", UNIT_PCS),
    "mixer_std": ("Змішувач стандарт", UNIT_PCS), "mixer_hidden": ("Змішувач прихований", UNIT_PCS),
    "boiler_100": ("Бойлер 100 л", UNIT_PCS), "boiler_300": ("Бойлер 300 л", UNIT_PCS),
    # Кухня / техніка
    "kitchen_apron": ("Фартух кухні", UNIT_M2), "tech_washer": ("Підключення пральної/посудомийної", UNIT_PCS),
    "tech_kitchen": ("Підключення кухонної техніки", UNIT_PCS), "tech_osmos": ("Осмос / подрібнювач", UNIT_PCS),
    # Інше
    "radiator": ("Радіатор", UNIT_PCS), "ac": ("Кондиціонер", UNIT_PCS), "curtains": ("Карнизи / штори", UNIT_PCS),
    "sill_plastic": ("Підвіконня пластик", UNIT_PCS), "sill_wood": ("Підвіконня дерево", UNIT_PCS),
    "sill_stone": ("Підвіконня штучний камінь", UNIT_PCS),
    "balcony_warm": ("Утеплення балкона", UNIT_M2), "balcony_workspace": ("Робоче місце на балконі", UNIT_PCS),
    "balcony_glazing_outer": ("Зовнішнє скління", UNIT_PCS), "balcony_glazing_block": ("Балконний блок", UNIT_PCS),
}

def apply_virtual_measurements(data):
    """
    Залишаємо для сумісності з main.py. 
    Тут можна реалізувати автогенерацію площ стін, якщо фронтенд передав тільки підлогу.
    """
    return data

def calculate_budget(data, prices, labels=None):
    # labels — необов'язковий словник {price_key: "Людська назва"} з колонки
    # "Назва" Google-таблиці. Якщо не передали — беремо PRICE_META.
    labels = labels or {}
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
    client_area = _num(client_data.get("area"))

    def add_c(category, price_key, multiplier=1.0, tier=None):
        if price_key not in prices: return None
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
            dw, dm1, dm2 = w * multiplier, m1 * multiplier, m1 * multiplier
        elif is_comf:
            dw, dm1, dm2 = w * multiplier, m_c * multiplier, m_c * multiplier
        elif is_prem:
            dw, dm1, dm2 = w * multiplier, m2 * multiplier, m2 * multiplier
        else:
            dw, dm1, dm2 = w * multiplier, m1 * multiplier, m2 * multiplier

        costs[category][0] += dw
        costs[category][1] += dm1
        costs[category][2] += dm2

        # --- ПОСТРОЧНА ДЕТАЛІЗАЦІЯ ---
        # Кожне нарахування = рядок кошторису. Спочатку він "нічий"
        # (room=None → потрапить у «Загальні роботи»); якщо виклик обгорнутий
        # у track(rid, ...), рядок переїде в кімнату rid.
        label, unit = PRICE_META.get(price_key, (price_key, UNIT_PCS))
        line = {
            "key": price_key,
            "label": labels.get(price_key) or label,   # назва з таблиці має пріоритет
            "unit": unit,
            "qty": round(float(multiplier), 2),
            "rate": round(w, 2),                       # ставка за одиницю (робота)
            "work": round(dw),
            "mat_min": round(dm1),
            "mat_max": round(dm2),
            "room": None,
        }
        if tier and isinstance(tier, str) and tier.strip():
            line["tier"] = tier.strip()
        all_lines.append(line)

        # Повертаємо сам рядок: він же слугує "дельтою" для по-кімнатного обліку.
        return line

    # --- ПОЗИЦІЇ ТА ПО-КІМНАТНИЙ ОБЛІК ---
    all_lines = []      # усі рядки кошторису в порядку нарахування
    room_costs = {}     # {room_id: [work, mat_min, mat_max]}

    def track(room_id, line):
        """Прив'язує рядок від add_c до конкретного приміщення."""
        if not room_id or not line:
            return
        line["room"] = room_id
        rc = room_costs.setdefault(room_id, [0.0, 0.0, 0.0])
        rc[0] += line["work"]; rc[1] += line["mat_min"]; rc[2] += line.get("mat_max", line["mat_min"])

    # === 1. ДЕМОНТАЖ ТА ЧОРНОВІ ВАРІАНТИ ===
    if answers.get("demo_entrance") == "Так": add_c("rough", "demo_door_ent", 1)
    demo_int_count = _int(answers.get("demo_interior"))
    if demo_int_count > 0:
        add_c("rough", "demo_door_int", demo_int_count)
        
    demo_walls = answers.get("demo_build_walls") or {}
    if demo_walls:
        add_c("rough", "demo_walls", _num(demo_walls.get("Демонтаж існуючих стін")))
        add_c("rough", "build_gkl", _num(demo_walls.get("Монтаж: Гіпсокартон")))
        add_c("rough", "build_brick", _num(demo_walls.get("Монтаж: Цегла (1/2)")))
        add_c("rough", "build_gazoblok", _num(demo_walls.get("Монтаж: Газоблок")))

    demo_floor = answers.get("demo_floor") or {}
    if demo_floor:
        add_c("rough", "demo_floor_wood", _num(demo_floor.get("Паркет / Дерев'яна")))
        add_c("rough", "demo_floor_lin", _num(demo_floor.get("Лінолеум / Ламінат")))
        add_c("rough", "demo_screed", _num(demo_floor.get("Стара стяжка")))

    # Рахуємо загальну площу стін динамічно на основі масиву кімнат
    rooms_list = answers.get("rooms") or []
    total_walls_area = sum([_num((r.get("measurements") or {}).get("walls")) for r in rooms_list])

    # Кількість кімнат/санвузлів ВИВОДИМО з масиву приміщень: питання
    # rooms_count/baths_count з анкети прибрані. Старі відповіді (режим
    # редагування давньої заявки) мають пріоритет, якщо раптом прийдуть.
    rooms_c = _int(answers.get("rooms_count")) or sum(1 for r in rooms_list if r.get("type") == "room")
    baths_c = _int(answers.get("baths_count")) or sum(1 for r in rooms_list if r.get("type") == "bath")
    
    if answers.get("rough_plaster_done") == "Ні":
        if total_walls_area == 0: 
            total_walls_area = client_area * 2.5
        add_c("rough", "rough_plaster", total_walls_area)

    screed = answers.get("screed_done")
    screed_area = _num(answers.get("screed_area"))
    if screed_area <= 0: 
        screed_area = client_area

    if "Мокра" in str(screed): add_c("rough", "screed_wet", screed_area)
    elif "Напівсуха" in str(screed): add_c("rough", "screed_dry", screed_area)

    # === 2. МЕРЕЖІ (ЕЛЕКТРИКА ТА САНТЕХНІКА) ===
    if answers.get("electricity_done") == "Ні":
        add_c("electric", "electric_wire", client_area)
        add_c("electric", "electric_point", client_area * 1.5)

    if answers.get("plumbing_done") == "Ні":
        add_c("rough", "plumbing", max(1, baths_c) * 5 + 3)

    # === 3. ДВЕРІ ===
    ent_door = answers.get("entrance_door") or {}
    if isinstance(ent_door, dict):
        tier = ent_door.get("tier")
        e_type = ent_door.get("type", "")
        if "Брон" in e_type: add_c("doors", "door_entrance_armor", 1, tier)
        elif e_type and e_type not in ["Ні", "Немає"]: add_c("doors", "door_entrance_mdf", 1, tier)

    int_door = answers.get("interior_door")
    # Міжкімнатні двері ставляться в кімнати та санвузли. Старий fallback
    # len(rooms_list) помилково рахував двері й на балкон/передпокій/гардероб.
    door_count = rooms_c + baths_c
        
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

    # Тепла підлога: користувач обирає приміщення ЗА НАЗВАМИ (options
    # будуються з масиву rooms у фронтенді). Замість старих плоских 5 м²
    # на пункт беремо РЕАЛЬНУ площу підлоги знайденої кімнати; 5 м² лишаємо
    # запобіжником, якщо назва не збіглась (стара чернетка тощо).
    # Коефіцієнт покриття (звично гріють ~70% підлоги) за бажанням додається
    # тут одним множником — це цінова політика, рішення за власником.
    warm_f = answers.get("warm_floor") or []
    if isinstance(warm_f, list) and len(warm_f) > 0 and "Не потребується" not in warm_f:
        for warm_name in warm_f:
            target = next((r for r in rooms_list if r.get("name") == warm_name), None)
            area = _num((target.get("measurements") or {}).get("floor")) if target else 0.0
            if area <= 0:
                area = 5.0
            track(target.get("id") if target else None,
                  add_c("electric", "warm_floor_elec", area))

    # === 5. ОБРОБКА ДИНАМІЧНОГО МАСИВУ ПРИМІЩЕНЬ ===
    # Кожне нарахування загорнуте в track(rid, ...): гроші йдуть у СТАРІ
    # категорії costs (rooms/baths/electric/rough — кабінет менеджера) і
    # ПАРАЛЕЛЬНО в room_costs[rid] — для живої розбивки по приміщеннях.
    for room in rooms_list:
        r_type = room.get("type", "room")
        rid = room.get("id")
        is_bath = (r_type == "bath")
        cat = "baths" if is_bath else "rooms"
        
        meas = room.get("measurements") or {}
        f_area = _num(meas.get("floor"))
        w_area = _num(meas.get("walls"))
        
        # Підлога
        floor = room.get("floor")
        if floor == "Керамограніт": track(rid, add_c(cat, "tile_floor_std", f_area))
        elif floor in ["Кварцвініл", "Кварц-вініл", "Кварц вініл"]: track(rid, add_c(cat, "room_quartz", f_area))
        elif floor == "Ламінат": track(rid, add_c(cat, "room_lam", f_area))
        elif floor == "Паркет": track(rid, add_c(cat, "room_parket", f_area))
        elif floor == "Лінолеум": track(rid, add_c(cat, "linoleum", f_area))
        elif floor == "Великоформатний керамограніт": track(rid, add_c(cat, "tile_floor_large", f_area))
        elif floor == "Мозаїка": track(rid, add_c(cat, "tile_floor_mosaic", f_area))
        
        # Стіни
        walls = room.get("walls") or []
        if "Шпалери" in walls: track(rid, add_c(cat, "wall_paper", w_area))
        if "Декоративна штукатурка" in walls: track(rid, add_c(cat, "wall_decor", w_area))
        if "Фарбування" in walls: track(rid, add_c(cat, "wall_paint", w_area))
        if "Грунтовка без фарбування" in walls: track(rid, add_c(cat, "wall_primer", w_area))
        if "Вагонка" in walls: track(rid, add_c(cat, "wall_vagonka", w_area))
        if "Короїд" in walls: track(rid, add_c(cat, "wall_koroid", w_area))
        if "Обшивка деревʼяними рейками" in walls: track(rid, add_c(cat, "wood_rails", w_area))

        # Плитка на стінах (для санвузлів)
        w_tile = room.get("wall_tile")
        if w_tile == "Керамограніт/Плитка до 120*60": track(rid, add_c(cat, "tile_wall_std", w_area))
        elif w_tile == "Великоформатний керамограніт": track(rid, add_c(cat, "tile_wall_large", w_area))
        elif w_tile == "Мозаїка": track(rid, add_c(cat, "tile_wall_mosaic", w_area))

        # Освітлення
        light = room.get("light") or []
        if "Точкове світло" in light: track(rid, add_c("electric", "light_point", max(1, int(f_area/2))))
        if "Люстра" in light: track(rid, add_c("electric", "light_chandelier", 1))
        if "Трек / Лінія" in light: track(rid, add_c("electric", "light_track", max(1, int(f_area/3))))
        if "LED підсвітка" in light or "Декор підсвітка" in light: track(rid, add_c("electric", "light_led", 5))

        # Декор
        decor = room.get("decor")
        if decor in ["Панелі гіпсові", "Панелі ДСП", "ДСП панелі"]: 
            track(rid, add_c(cat, "wall_decor_panels", max(1, int(w_area/4))))

        # Фартух кухні
        if room.get("apron") == "Керамограніт": track(rid, add_c(cat, "kitchen_apron", 3))
        
        # Змішувачі
        mix_std = _num(room.get("mixer_std"))
        if mix_std > 0: track(rid, add_c(cat, "mixer_std", mix_std))
        mix_hid = _num(room.get("mixer_hidden"))
        if mix_hid > 0: track(rid, add_c(cat, "mixer_hidden", mix_hid))

        # Сантехніка (Ванна, Душ, Унітаз)
        shower = room.get("shower") or []
        if "Піддон (акрил/камінь)" in shower: track(rid, add_c(cat, "shower_tray", 1))
        if "Душовий трап (з плитки)" in shower: track(rid, add_c(cat, "shower_trap", 1))
        if "Скляна перегородка" in shower: track(rid, add_c(cat, "shower_glass", 1))
        if "Скляна конструкція з дверима" in shower: track(rid, add_c(cat, "shower_doors", 1))
        
        tub = room.get("tub") or {}
        if isinstance(tub, dict) and tub.get("type") not in [None, "", "Ні", "Немає", "Не потрібно"]:
            track(rid, add_c(cat, "bath_tub", 1, tub.get("tier")))
            
        toilet = room.get("toilet") or {}
        if isinstance(toilet, dict):
            t_type = toilet.get("type", "")
            t_tier = toilet.get("tier")
            if "Інсталяція" in t_type or "Підвісний" in t_type: 
                track(rid, add_c(cat, "toilet_install", 1, t_tier))
            elif t_type and t_type not in ["Ні", "Немає", "Не потрібно"]: 
                track(rid, add_c(cat, "toilet_okrem", 1, t_tier))

        # Підвіконня
        sills = room.get("sills")
        if sills == "Пластик": track(rid, add_c("rooms", "sill_plastic", 1))
        elif sills == "Дерево": track(rid, add_c("rooms", "sill_wood", 1))
        elif sills == "Штучний камінь": track(rid, add_c("rooms", "sill_stone", 1))

        # Інше додаткове обладнання (Радіатори, Клімат, Техніка)
        other = room.get("other") or {}
        for k, v in other.items():
            tier = v if isinstance(v, str) else None
            if "Радіатор" in k: track(rid, add_c("rough", "radiator", 1, tier))
            elif "Кондиціонер" in k: track(rid, add_c("electric", "ac", 1, tier))
            elif "Звукоізоляція" in k: track(rid, add_c(cat, "soundproof", w_area))
            elif "Утеплення" in k: track(rid, add_c("rough", "balcony_warm", f_area))
            elif "Робоче місце" in k: track(rid, add_c(cat, "balcony_workspace", 1))
            elif "Зовнішнє скління" in k: track(rid, add_c(cat, "balcony_glazing_outer", _num(v, 1)))
            elif "Балконний блок" in k: track(rid, add_c(cat, "balcony_glazing_block", _num(v, 1)))
            elif any(x in k for x in ["Посудомийн", "Пральн", "Сушильн"]): track(rid, add_c(cat, "tech_washer", 1, tier))
            elif any(x in k for x in ["Осмос", "Подрібнювач"]): track(rid, add_c(cat, "tech_osmos", 1, tier))
            elif any(x in k for x in ["Духов", "Мікрохвильов"]): track(rid, add_c(cat, "tech_kitchen", 1, tier))
            elif "Гігієнічний душ" in k: track(rid, add_c(cat, "hygienic_shower", 1, tier))
            elif "Бойлер" in k and "300" in k: track(rid, add_c(cat, "boiler_300", 1, tier))
            elif "Бойлер" in k: track(rid, add_c(cat, "boiler_100", 1, tier))
            elif "Умивальник" in k or "Раковина" in k: track(rid, add_c(cat, "sink_cabinet", 1, tier))
            elif "Дзеркало" in k: track(rid, add_c(cat, "mirror_led", 1, tier))
            elif "Рушникосушка" in k: track(rid, add_c(cat, "towel_dryer", 1, tier))

    # === 6. НЕСТАНДАРТНІ РОБОТИ ===
    custom_works = answers.get("custom_works") or []
    for cw in custom_works:
        calc_type = cw.get("calc_type", "Фіксована ціна")
        w_price = _num(cw.get("work_price"))
        m_price = _num(cw.get("mat_price"))
        zone_id = cw.get("zone_id") # зв'язка йде через унікальний id кімнати
        multiplier = 1.0

        if calc_type in ["За м² підлоги", "За м² стін"] and zone_id:
            target_room = next((r for r in rooms_list if r.get("id") == zone_id), None)
            if target_room:
                r_meas = target_room.get("measurements") or {}
                multiplier = _num(r_meas.get("floor")) if calc_type == "За м² підлоги" else _num(r_meas.get("walls"))
            else:
                multiplier = 0.0

        costs["custom"][0] += w_price * multiplier
        costs["custom"][1] += m_price * multiplier
        costs["custom"][2] += m_price * multiplier
        # Кастомна робота — теж повноцінний рядок кошторису (з назвою,
        # яку ввів менеджер). Якщо прив'язана до кімнати — track() її туди.
        cw_line = {
            "key": "custom",
            "label": cw.get("name") or "Нестандартна робота",
            "unit": {"За м² підлоги": UNIT_M2, "За м² стін": UNIT_M2}.get(calc_type, UNIT_PCS),
            "qty": round(float(multiplier), 2),
            "rate": round(w_price, 2),
            "work": round(w_price * multiplier),
            "mat_min": round(m_price * multiplier),
            "mat_max": round(m_price * multiplier),
            "room": None,
        }
        all_lines.append(cw_line)
        track(zone_id, cw_line)

    total_work = sum(v[0] for v in costs.values())
    total_mat_min = sum(v[1] for v in costs.values())
    total_mat_max = sum(v[2] for v in costs.values())

    # Групуємо рядки: ті, що прив'язані до кімнати — у room_lines,
    # решта (демонтаж, стяжка, стеля, двері, розводки) — «Загальні роботи».
    room_lines = {}
    general_lines = []
    for ln in all_lines:
        if ln["work"] == 0 and ln["mat_min"] == 0:
            continue  # нульові позиції (напр. грунтовка з 0 матеріалу) не показуємо
        rid = ln.pop("room")
        if rid:
            room_lines.setdefault(rid, []).append(ln)
        else:
            general_lines.append(ln)

    return {
        "total_work": total_work,
        "total_mat_min": total_mat_min,
        "total_mat_max": total_mat_max,
        "costs": costs,
        # По-кімнатний облік: {room_id: [work, mat_min, mat_max]}.
        # НЕ входить у total_* повторно — це той самий кошик грошей,
        # просто розкладений за приміщеннями.
        "room_costs": room_costs,
        # Построчна деталізація: {room_id: [{label, qty, unit, rate, work, mat_min}]}
        "room_lines": room_lines,
        "general_lines": general_lines,
    }

