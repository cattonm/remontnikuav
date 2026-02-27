<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Анкета Об'єкту</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        body { font-family: -apple-system, sans-serif; padding: 20px; background: #fff; color: #000; padding-bottom: 120px; margin: 0; }
        .hidden { display: none !important; }
        #top-bar { display: flex; justify-content: space-between; align-items: center; background: #f0f4f8; padding: 12px 15px; border-radius: 12px; margin-bottom: 20px; }
        #menu-btn { font-size: 16px; font-weight: bold; cursor: pointer; color: #3390ec; display: flex; align-items: center; gap: 8px;}
        #step-count { color: #888; font-size: 14px; font-weight: bold; }
        #menu-overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 1999; display: none; }
        #menu-overlay.open { display: block; }
        #side-menu { position: fixed; top: 0; left: 0; width: 260px; height: 100%; background: #fff; box-shadow: 2px 0 10px rgba(0,0,0,0.1); z-index: 2000; transform: translateX(-100%); transition: transform 0.3s ease-in-out; overflow-y: auto; padding-top: 20px; }
        #side-menu.open { transform: translateX(0); }
        .menu-header { padding: 0 20px 10px 20px; font-size: 18px; font-weight: bold; border-bottom: 1px solid #eee; margin-bottom: 10px; color: #333;}
        .menu-item { padding: 12px 20px; border-bottom: 1px solid #f9f9f9; font-weight: 500; color: #555; cursor: pointer; }
        .zone-badge { display: inline-block; background: #f0f0f0; color: #888; padding: 4px 8px; border-radius: 6px; font-size: 12px; font-weight: bold; margin-bottom: 8px; text-transform: uppercase; }
        h3 { margin: 0 0 20px 0; font-size: 20px; font-weight: 700; line-height: 1.3; }
        label { display: block; margin-bottom: 8px; font-weight: bold; font-size: 14px; color: #555; }
        input[type="text"], input[type="tel"], input[type="number"], select { width: 100%; padding: 14px; border-radius: 12px; border: 1px solid #ddd; font-size: 16px; margin-bottom: 20px; box-sizing: border-box; background: #f9f9f9; }
        .card-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
        .card { border: 2px solid #eee; border-radius: 12px; padding: 10px; text-align: center; cursor: pointer; transition: 0.2s; position: relative; background: #fff; display: flex; flex-direction: column; align-items: center; justify-content: flex-start; }
        .card.selected { border-color: #3390ec; background: #eaf4ff; }
        .img-wrapper { position: relative; width: 100%; height: 110px; margin-bottom: 10px; }
        .card img { width: 100%; height: 100%; object-fit: cover; border-radius: 8px; background-color: #f0f0f0; }
        .zoom-icon { position: absolute; top: 5px; right: 5px; background: rgba(0,0,0,0.6); color: white; padding: 4px 8px; border-radius: 20px; font-size: 14px; cursor: pointer; z-index: 10; transition: background 0.2s; }
        .zoom-icon:hover { background: rgba(0,0,0,0.8); }
        .tier-container { display: flex; gap: 5px; justify-content: center; margin-top: 8px; flex-wrap: wrap; }
        .tier-btn { border: 1px solid #ccc; padding: 6px 10px; border-radius: 6px; font-size: 12px; font-weight: bold; cursor: pointer; background: #fff; color: #555; min-width: 24px; }
        .tier-btn.active { background: #3390ec; color: #fff; border-color: #3390ec; }
        .list-item { display: flex; justify-content: space-between; align-items: center; padding: 12px; border-bottom: 1px solid #eee; }
        .list-item input[type="checkbox"] { width: 22px; height: 22px; margin-right: 12px; accent-color: #3390ec; }
        .tier-select { padding: 5px; border-radius: 6px; border: 1px solid #ddd; background: #fff; font-size: 14px; }
        #imageModal { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.9); z-index: 3000; display: none; justify-content: center; align-items: center; flex-direction: column; }
        #imageModal img { max-width: 95%; max-height: 80%; border-radius: 8px; }
        #closeModal { color: #fff; margin-top: 20px; font-size: 18px; cursor: pointer; border: 1px solid #fff; padding: 10px 20px; border-radius: 20px; }
        #section-thankyou, #loading-screen { text-align: center; padding-top: 50px; }
        .check-icon { font-size: 60px; color: #3390ec; margin-bottom: 20px; }
        .measurement-box { background: #f9f9f9; padding: 15px; border-radius: 12px; border: 1px solid #eee; margin-bottom: 20px; }
        .nav-bar { position: fixed; bottom: 0; left: 0; width: 100%; background: #fff; padding: 15px; border-top: 1px solid #eee; display: flex; gap: 10px; box-sizing: border-box; z-index: 100; }
        .btn { flex: 1; padding: 15px; border: none; border-radius: 10px; font-size: 16px; font-weight: 600; cursor: pointer; }
        .btn-back { background: #f0f0f0; color: #333; flex: 0.3; }
        .btn-next { background: #3390ec; color: #fff; }
        #live-cart { position: fixed; bottom: 75px; left: 10px; right: 10px; background: rgba(33, 43, 54, 0.95); color: #fff; padding: 12px 15px; border-radius: 12px; box-shadow: 0 -2px 15px rgba(0,0,0,0.2); z-index: 99; display: flex; justify-content: space-between; align-items: center; font-size: 14px; cursor: pointer; backdrop-filter: blur(5px); transition: opacity 0.3s; opacity: 0; pointer-events: none;}
        #live-cart.visible { opacity: 1; pointer-events: auto; }
        #live-cart div { display: flex; flex-direction: column; }
        #live-cart span.cart-val { color: #4CAF50; font-weight: bold; font-size: 16px;}
        #cart-modal { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: #fff; z-index: 4000; display: none; flex-direction: column; overflow-y: auto; padding: 20px; box-sizing: border-box; }
        .cart-close { text-align: right; font-size: 24px; cursor: pointer; color: #333; margin-bottom: 10px; font-weight: bold; }
        .cart-list { margin-top: 10px; font-size: 14px; color: #444; line-height: 1.6; }
        .cart-zone-title { font-weight: bold; color: #3390ec; margin-top: 15px; border-bottom: 1px solid #eee; padding-bottom: 5px; font-size: 16px; text-transform: uppercase;}
    </style>
</head>
<body>
<script> const BACKEND_URL = "https://remontnikuav.onrender.com"; </script>
<div id="imageModal" onclick="closeImage()"><img id="modalImg" src=""><div id="closeModal">Закрити</div></div>
<div id="menu-overlay" onclick="toggleMenu()"></div>
<div id="side-menu"><div class="menu-header">📋 Розділи анкети</div><div id="menu-list"></div></div>
<div id="loading-screen" class="hidden"><h3>⏳ Завантаження...</h3><p style="color: #888;">Підтягуємо дані об'єкту.</p></div>

<div id="live-cart" onclick="openCartModal()">
    <div><span style="font-size:12px; color:#aaa;">Робота</span><span class="cart-val" id="cart-work">0 ₴</span></div>
    <div><span style="font-size:12px; color:#aaa;">Матеріали (від)</span><span class="cart-val" id="cart-mat">0 ₴</span></div>
    <div style="font-size: 20px;">🛒</div>
</div>
<div id="cart-modal">
    <div class="cart-close" onclick="closeCartModal()">✕ Закрити</div>
    <h3 style="margin-bottom: 5px;">Ваш вибір</h3>
    <p style="color: #888; font-size: 13px; margin-top:0;">Натисніть хрестик, щоб повернутися.</p>
    <div class="cart-list" id="cart-list-content"></div>
</div>

<div id="section-client">
    <h3>👷 Інформація про об'єкт</h3>
    <label>Ім'я клієнта</label> <input type="text" id="clientName" placeholder="Введіть ім'я">
    <label>Телефон</label> <input type="tel" id="clientPhone" placeholder="+380...">
    <label>Тип об'єкту</label><select id="objectType"><option value="Квартира (Новобудова)">Квартира (Новобудова)</option><option value="Квартира (Вторинна)">Квартира (Вторинна)</option><option value="Будинок">Будинок</option><option value="Комерція">Комерція</option></select>
    <label>Адреса</label> <input type="text" id="clientAddress" placeholder="Вулиця / ЖК">
    <label>Загальна площа об'єкта (м²)</label> <input type="number" id="clientArea" placeholder="Наприклад: 65">
    <div style="display: flex; gap: 10px;"><div style="flex: 1;"><label>Поверх</label> <input type="number" id="clientFloor" placeholder="1" value="1"></div>
    <div style="flex: 1;"><label>Ліфт</label><select id="clientElevator"><option value="Вантажний">Вантажний</option><option value="Пасажирський">Пасажирський</option><option value="Немає">Немає</option></select></div></div>
</div>

<div id="section-questions" class="hidden">
    <div id="top-bar"><div id="menu-btn" onclick="toggleMenu()">☰ Меню</div><div id="step-count">1/X</div></div>
    <div id="question-container"></div>
</div>

<div id="section-measurements" class="hidden">
    <h3>📏 Точні заміри приміщень</h3>
    <p style="color: #666; font-size: 14px; margin-bottom: 20px;">Сума оновлюється автоматично під час введення.</p>
    <div id="measurements-container"></div>
</div>

<div id="section-thankyou" class="hidden"></div>
<div class="nav-bar" id="navBar">
    <button type="button" class="btn btn-back" onclick="goBack()" id="btnBack" style="display:none;">←</button>
    <button type="button" class="btn btn-next" onclick="goNext()" id="btnNext">Далі</button>
</div>

<script>
    const tg = window.Telegram.WebApp;
    tg.expand();
    let answers = {}; 
    let currentStep = -1; 
    let finalQuestions = []; 
    let editId = null; 
    let calcTimeout = null;

    const blockGeneral = [
        { id: "rooms_count", zone: "ЗАГАЛЬНІ", text: "Скільки ЖИТЛОВИХ кімнат?", type: "input_number", placeholder: "Введіть число" },
        { id: "baths_count", zone: "ЗАГАЛЬНІ", text: "Скільки САНВУЗЛІВ?", type: "input_number", placeholder: "Введіть число" },
        { id: "aux_rooms", zone: "ЗАГАЛЬНІ", text: "Які ДОДАТКОВІ приміщення ремонтуємо?", type: "cards_multiselect", options: [
            {label:"Передпокій",val:"Передпокій"}, {label:"Кухня",val:"Кухня"}, {label:"Балкон",val:"Балкон"},
            {label:"Гардероб",val:"Гардероб"}, {label:"Підвал",val:"Підвал"}, {label:"Горище",val:"Горище"}
        ]},
        { id: "rough_plaster_done", zone: "ЗАГАЛЬНІ", text: "Чи потрібна машинна штукатурка стін (чорнова)?", type: "cards", options: [{label:"Так",val:"Так"},{label:"Ні",val:"Ні"}] },
        { id: "entrance_door", zone: "ЗАГАЛЬНІ", text: "Тип вхідних дверей.", type: "cards_with_tier", options: [
            { label: "МДФ", val: "МДФ", variants: ['Standard', 'Comfort', 'Premium'] },
            { label: "Броньовані", val: "Броньовані", variants: ['Standard', 'Comfort', 'Premium'] },
            { label: "Не потребується", val: "Не потребується" }
        ]},
        { id: "interior_door", zone: "ЗАГАЛЬНІ", text: "Тип міжкімнатних дверей.", type: "cards", options: [
            {label:"Прихований монтаж",val:"Прихований монтаж",img:"img/hidemont.webp"},
            {label:"Стандарт",val:"Стандарт",img:"img/standarddoor.webp"},
            {label:"Ні / Залишаються",val:"Ні"}
        ]},
        { id: "baseboard", zone: "ЗАГАЛЬНІ", text: "Тип підлогового плінтусу.", type: "cards", options: [
            {label:"Прихований монтаж",val:"Прихований монтаж",img:"img/plinthidem.webp"},
            {label:"Тіньовий шов",val:"Тіньовий шов",img:"img/shadowshow.webp"},
            {label:"Стандартний",val:"Стандартний",img:"img/plintstandard.webp"}
        ]},
        { id: "ceiling", zone: "ЗАГАЛЬНІ", text: "Тип стелі.", type: "cards", options: [
            {label:"Натяжна",val:"Натяжна"}, {label:"Гіпсокартон",val:"Гіпсокартон"},{label:"Ні",val:"Ні"}
        ]},
        { id: "ceiling_shadow", zone: "ЗАГАЛЬНІ", text: "Чи потрібен тіньовий шов для стелі?", type: "cards", options: [{label:"Так",val:"Так"},{label:"Ні",val:"Ні"}] },
        { id: "screed_done", zone: "ЗАГАЛЬНІ", text: "Чи є стяжка підлоги?", type: "cards", options: [
            {label:"Є від забудовника",val:"Є від забудовника"}, {label:"Потрібна: Мокра",val:"Потрібна: Мокра"}, {label:"Потрібна: Напівсуха",val:"Потрібна: Напівсуха"}
        ]},
        { id: "electricity_done", zone: "ЗАГАЛЬНІ", text: "Чи розведена електрика?", type: "cards", options: [{label:"Так",val:"Так"},{label:"Ні",val:"Ні"}] },
        { id: "plumbing_done", zone: "ЗАГАЛЬНІ", text: "Чи розведена каналізація?", type: "cards", options: [{label:"Так",val:"Так"},{label:"Ні",val:"Ні"}] },
        { id: "warm_floor", zone: "ЗАГАЛЬНІ", text: "Встановлення теплої підлоги:", type: "multiselect_dynamic" }
    ];

    const blockHallway = [
        { id: "hallway_floor", zone: "ПЕРЕДПОКІЙ", text: "Тип підлоги.", type: "cards", options: [
            {label:"Керамограніт",val:"Керамограніт",img:"img/keramogranit.webp"},
            {label:"Кварцвініл",val:"Кварцвініл",img:"img/kvarzvinil.webp"},
            {label:"Ламінат",val:"Ламінат",img:"img/laminat.jpeg"}
        ]},
        { id: "hallway_walls", zone: "ПЕРЕДПОКІЙ", text: "Стіни.", type: "cards", options: [
            {label:"Шпалери",val:"Шпалери",img:"img/spaleri.jpeg"},
            {label:"Декоративна штукатурка",val:"Декоративна штукатурка",img:"img/dekorstuk.webp"},
            {label:"Фарбування",val:"Фарбування",img:"img/farbuvannya.webp"}
        ]},
        { id: "hallway_light", zone: "ПЕРЕДПОКІЙ", text: "Освітлення", type: "cards_multiselect", options: [
            {label:"Точкове світло",val:"Точкове світло",img:"img/tochkove.webp"}, {label:"Люстра",val:"Люстра",img:"img/lustra.webp"},
            {label:"Трек / Лінія",val:"Трек / Лінія"}, {label:"Декор підсвітка",val:"Декор підсвітка",img:"img/dekorpidsv.webp"}
        ]},
        { id: "hallway_decor", zone: "ПЕРЕДПОКІЙ", text: "Декор", type: "cards", options: [
            {label:"ДСП панелі",val:"ДСП панелі",img:"img/dsppaneli.webp"}, {label:"Ні",val:"Ні"}
        ]}
    ];

    const blockKitchen = [
        { id: "kitchen_walls", zone: "КУХНЯ", text: "Стіни.", type: "cards", options: [
            {label:"Шпалери",val:"Шпалери",img:"img/spaleri.jpeg"}, {label:"Декоративна штукатурка",val:"Декоративна штукатурка",img:"img/dekorstuk.webp"}, {label:"Фарбування",val:"Фарбування",img:"img/farbuvannya.webp"}
        ]},
        { id: "kitchen_light", zone: "КУХНЯ", text: "Освітлення", type: "cards_multiselect", options: [
            {label:"Точкове світло",val:"Точкове світло",img:"img/tochkove.webp"}, {label:"Люстра",val:"Люстра",img:"img/lustra.webp"},
            {label:"Трек / Лінія",val:"Трек / Лінія"}, {label:"Декор підсвітка",val:"Декор підсвітка",img:"img/dekorpidsv.webp"}
        ]},
        { id: "kitchen_floor", zone: "КУХНЯ", text: "Підлога.", type: "cards", options: [
            {label:"Керамограніт",val:"Керамограніт",img:"img/kitchenkeramo.webp"}, {label:"Кварц-вініл",val:"Кварц-вініл",img:"img/kitchenkvarzv.webp"}
        ]},
        { id: "kitchen_apron", zone: "КУХНЯ", text: "Фартух.", type: "cards", options: [
            {label:"Керамограніт",val:"Керамограніт",img:"img/fartukkeramo.webp"}, {label:"Матеріал стільниці",val:"Матеріал стільниці",img:"img/fartukstiln.jpeg"}
        ]},
        { id: "kitchen_mixer_std", zone: "КУХНЯ", text: "Кількість звичайних змішувачів (шт)", type: "input_number", placeholder: "0" },
        { id: "kitchen_mixer_hidden", zone: "КУХНЯ", text: "Змішувачі прихованого монтажу (шт)", type: "input_number", placeholder: "0" },
        { id: "kitchen_other", zone: "КУХНЯ", text: "Інші потреби", type: "multiselect_complex", hasOther: false, options: [
            {label:"Підсвітка робочої поверхні", tier: false}, {label:"Посудомийна машина", tier: true}, {label:"Осмос", tier: true},
            {label:"Подрібнювач відходів", tier: true}, {label:"Мікрохвильова піч", tier: true}, {label:"Духова шафа", tier: true}, {label:"Радіатор", tier: true}
        ]}
    ];

    const blockBalcony = [
        { id: "balcony_floor", zone: "БАЛКОН", text: "Тип підлоги.", type: "cards", options: [
            {label:"Керамограніт",val:"Керамограніт",img:"img/keramogranit.webp"}, {label:"Кварц вініл",val:"Кварц вініл",img:"img/kvarzvinil.webp"}, {label:"Ламінат",val:"Ламінат",img:"img/laminat.jpeg"}
        ]},
        { id: "balcony_walls", zone: "БАЛКОН", text: "Стіни.", type: "cards", options: [
            {label:"Шпалери",val:"Шпалери",img:"img/spaleri.jpeg"}, {label:"Декоративна штукатурка",val:"Декоративна штукатурка",img:"img/dekorstuk.webp"}, {label:"Фарбування",val:"Фарбування",img:"img/farbuvannya.webp"}
        ]},
        { id: "balcony_light", zone: "БАЛКОН", text: "Освітлення", type: "cards_multiselect", options: [
            {label:"Точкове світло",val:"Точкове світло",img:"img/tochkove.webp"}, {label:"Люстра",val:"Люстра",img:"img/lustra.webp"}, {label:"LED підсвітка",val:"LED підсвітка",img:"img/ledpidsv.webp"}
        ]},
        { id: "balcony_other", zone: "БАЛКОН", text: "Облаштування балкону", type: "multiselect_complex", hasOther: false, options: [
            {label:"Утеплення", tier: false}, {label:"Робоче місце", tier: false}
        ]}
    ];

    const blockWardrobe = [
        { id: "wardrobe_floor", zone: "ГАРДЕРОБ", text: "Тип підлоги.", type: "cards", options: [
            {label:"Керамограніт",val:"Керамограніт",img:"img/keramogranit.webp"}, {label:"Кварц вініл",val:"Кварц вініл",img:"img/kvarzvinil.webp"}, {label:"Ламінат",val:"Ламінат",img:"img/laminat.jpeg"}
        ]},
        { id: "wardrobe_walls", zone: "ГАРДЕРОБ", text: "Стіни.", type: "cards", options: [
            {label:"Шпалери",val:"Шпалери",img:"img/spaleri.jpeg"}, {label:"Декоративна штукатурка",val:"Декоративна штукатурка",img:"img/dekorstuk.webp"}, {label:"Фарбування",val:"Фарбування",img:"img/farbuvannya.webp"}, {label:"Грунтовка без фарбування",val:"Грунтовка без фарбування",img:"img/gruntovka.webp"}
        ]},
        { id: "wardrobe_light", zone: "ГАРДЕРОБ", text: "Освітлення", type: "cards_multiselect", options: [
            {label:"Точкове світло",val:"Точкове світло",img:"img/tochkove.webp"}, {label:"Люстра",val:"Люстра",img:"img/lustra.webp"}, {label:"LED підсвітка",val:"LED підсвітка",img:"img/ledgarderob.webp"}
        ]}
    ];

    const blockBasement = [
        { id: "basement_floor", zone: "ПІДВАЛ", text: "Тип підлоги.", type: "cards", options: [
            {label:"Керамограніт",val:"Керамограніт",img:"img/keramogranit.webp"}, {label:"Лінолеум",val:"Лінолеум",img:"img/linoleum.webp"}
        ]},
        { id: "basement_walls", zone: "ПІДВАЛ", text: "Стіни.", type: "cards", options: [
            {label:"Шпалери",val:"Шпалери",img:"img/spaleri.jpeg"}, {label:"Декоративна штукатурка",val:"Декоративна штукатурка",img:"img/dekorstuk.webp"}, {label:"Фарбування",val:"Фарбування",img:"img/farbuvannya.webp"}
        ]},
        { id: "basement_ceiling", zone: "ПІДВАЛ", text: "Тип стелі.", type: "cards", options: [
            {label:"Натяжна",val:"Натяжна"}, {label:"Гіпсокартон",val:"Гіпсокартон"}, {label:"Побілка",val:"Побілка"}
        ]},
        { id: "basement_light", zone: "ПІДВАЛ", text: "Освітлення", type: "cards_multiselect", options: [
            {label:"Точкове світло",val:"Точкове світло",img:"img/tochkove.webp"}, {label:"Люстра",val:"Люстра",img:"img/lustra.webp"}
        ]}
    ];

    const blockAttic = [
        { id: "attic_walls", zone: "ГОРИЩЕ", text: "Стіни.", type: "cards", options: [
            {label:"Шпалери",val:"Шпалери",img:"img/spaleri.jpeg"}, {label:"Обшивка деревʼяними рейками",val:"Обшивка деревʼяними рейками",img:"img/obshivkaderev.webp"}, {label:"Фарбування",val:"Фарбування",img:"img/farbzgipso.webp"}, {label:"ні",val:"ні"}
        ]},
        { id: "attic_floor", zone: "ГОРИЩЕ", text: "Тип підлоги.", type: "cards", options: [
            {label:"Керамограніт",val:"Керамограніт",img:"img/keramogranit.webp"}, {label:"Кварц вініл",val:"Кварц вініл",img:"img/kvarzvinil.webp"}, {label:"Ламінат",val:"Ламінат",img:"img/laminat.jpeg"}, {label:"Інше",val:"Інше"}
        ]},
        { id: "attic_light", zone: "ГОРИЩЕ", text: "Освітлення", type: "cards_multiselect", options: [
            {label:"Точкове світло",val:"Точкове світло",img:"img/tochkove.webp"}, {label:"Люстра",val:"Люстра",img:"img/lustra.webp"}, {label:"LED підсвітка",val:"LED підсвітка",img:"img/ledpidsv.webp"}
        ]}
    ];

    function getBathQuestions(index) {
        return [
            { id: `bath_${index}_floor`, zone: `САНВУЗОЛ ${index}`, text: "Плитка на підлогу (формат)", type: "cards", options: [
                { label: "Мозаїка", val: "Мозаїка" }, { label: "Плитка до 120*60", val: "Керамограніт/Плитка до 120*60" }, { label: "Великоформатний", val: "Великоформатний керамограніт" }
            ]},
            { id: `bath_${index}_wall_tile`, zone: `САНВУЗОЛ ${index}`, text: "Плитка на стіни (формат)", type: "cards", options: [
                { label: "Мозаїка", val: "Мозаїка" }, { label: "Плитка до 120*60", val: "Керамограніт/Плитка до 120*60" }, { label: "Великоформатний", val: "Великоформатний керамограніт" }
            ]},
            { id: `bath_${index}_shower`, zone: `САНВУЗОЛ ${index}`, text: "Душ.", type: "cards_multiselect", options: [
                {label:"Піддон (акрил/камінь)",val:"Піддон (акрил/камінь)",img:"img/piddon.webp"}, {label:"Душовий трап (з плитки)",val:"Душовий трап (з плитки)",img:"img/plitkadush.webp"}, 
                {label:"Скляна перегородка",val:"Скляна перегородка",img:"img/sklodush.webp"}, {label:"Скляна конструкція з дверима",val:"Скляна конструкція з дверима",img:"img/sklodveridush.webp"}, {label:"Не обладнувати",val:"Не обладнувати"}
            ]},
            { id: `bath_${index}_tub`, zone: `САНВУЗОЛ ${index}`, text: "Ванна", type: "cards_with_tier", options: [
                {label:"Акрил",val:"Акрил",img:"img/vanaakril.webp", variants: ['Standard', 'Comfort', 'Premium']}, 
                {label:"Гідро масаж",val:"Гідро масаж",img:"img/vanagidromas.webp", variants: ['Standard', 'Comfort', 'Premium']}, 
                {label:"Окремостояча",val:"Окремостояча",img:"img/okremavana.webp", variants: ['Standard', 'Comfort', 'Premium']}, 
                {label:"Не обладнувати",val:"Не обладнувати"}
            ]},
            { id: `bath_${index}_toilet`, zone: `САНВУЗОЛ ${index}`, text: "Унітаз.", type: "cards_with_tier", options: [
                {label:"Окремостоячий",val:"Окремостоячий",img:"img/unitazokremo.webp", variants: ['Standard', 'Comfort', 'Premium']}, 
                {label:"Інсталяція",val:"Інсталяція",img:"img/installunitaz.webp", variants: ['Standard', 'Comfort', 'Premium']}
            ]},
            { id: `bath_${index}_mixer_std`, zone: `САНВУЗОЛ ${index}`, text: "Кількість звичайних змішувачів (шт)", type: "input_number", placeholder: "0" },
            { id: `bath_${index}_mixer_hidden`, zone: `САНВУЗОЛ ${index}`, text: "Змішувачі прихованого монтажу (шт)", type: "input_number", placeholder: "0" },
            { id: `bath_${index}_other`, zone: `САНВУЗОЛ ${index}`, text: "Інші потреби", type: "multiselect_complex", hasOther:false, options: [
                {label:"Гігієнічний душ",tier:true}, {label:"Пральна машина",tier:true}, {label:"Сушильна машина",tier:true}, 
                {label:"Дзеркало з підігрівом",tier:true}, {label:"Бойлер",tier:true}, {label:"Умивальник з тумбою",tier:true}, 
                {label:"Дзеркало",tier:true}, {label:"Рушникосушка",tier:true}
            ]}
        ];
    }

    function getRoomQuestions(index) {
        return [
            { id: `room_${index}_floor`, zone: `КІМНАТА ${index}`, text: "Тип підлоги.", type: "cards", options: [
                { label: "Ламінат", val: "Ламінат", img: "img/laminat.jpeg" }, { label: "Паркет", val: "Паркет", img: "img/parket.jpeg" },
                { label: "Кварц вініл", val: "Кварц вініл", img: "img/kvarzvinil.webp" }, { label: "Керамограніт", val: "Керамограніт", img: "img/keramogranit.webp" }
            ]},
            { id: `room_${index}_walls`, zone: `КІМНАТА ${index}`, text: "Стіни.", type: "cards", options: [
                {label:"Шпалери",val:"Шпалери",img:"img/spaleri.jpeg"}, {label:"Декоративна штукатурка",val:"Декоративна штукатурка",img:"img/dekorstuk.webp"}, {label:"Фарбування",val:"Фарбування",img:"img/farbuvannya.webp"}
            ]},
            { id: `room_${index}_light`, zone: `КІМНАТА ${index}`, text: "Освітлення.", type: "cards_multiselect", options: [
                {label:"Точкове світло",val:"Точкове світло",img:"img/tochkove.webp"}, {label:"Люстра",val:"Люстра",img:"img/lustra.webp"}, 
                {label:"Трек / Лінія",val:"Трек / Лінія"}, {label:"LED підсвітка",val:"LED підсвітка",img:"img/ledpidsv.webp"}
            ]},
            { id: `room_${index}_sills`, zone: `КІМНАТА ${index}`, text: "Підвіконня.", type: "cards", options: [
                {label:"Пластик",val:"Пластик"}, {label:"Дерево",val:"Дерево"}, 
                {label:"Штучний камінь",val:"Штучний камінь"}, {label:"Не потребується",val:"Не потребується"}
            ]},
            { id: `room_${index}_decor`, zone: `КІМНАТА ${index}`, text: "Декор.", type: "cards", options: [
                {label:"Панелі гіпсові",val:"Панелі гіпсові",img:"img/gipspankimn.webp"}, {label:"Панелі ДСП",val:"Панелі ДСП",img:"img/dsppaneli.webp"}, {label:"ні",val:"ні"}
            ]},
            { id: `room_${index}_other`, zone: `КІМНАТА ${index}`, text: "Інші потреби", type: "multiselect_complex", hasOther:false, options: [
                {label:"Радіатор",tier:true}, {label:"Кондиціонер", tier:true}, {label:"Звукоізоляція", tier:false}
            ]}
        ];
    }

    function cleanupOldAnswers() {
        const rooms = parseInt(answers['rooms_count']) || 0;
        const baths = parseInt(answers['baths_count']) || 0;
        const aux = answers['aux_rooms'] || [];
        Object.keys(answers).forEach(key => {
            if (key.startsWith('room_')) { const num = parseInt(key.split('_')[1]); if (num > rooms) delete answers[key]; }
            if (key.startsWith('bath_')) { const num = parseInt(key.split('_')[1]); if (num > baths) delete answers[key]; }
        });
        const zonePrefixes = { 'hallway': 'Передпокій', 'kitchen': 'Кухня', 'balcony': 'Балкон', 'wardrobe': 'Гардероб', 'basement': 'Підвал', 'attic': 'Горище' };
        Object.keys(answers).forEach(key => {
            for (let [prefix, zone] of Object.entries(zonePrefixes)) {
                if (key.startsWith(prefix + '_') && !aux.includes(zone)) { delete answers[key]; break; }
            }
        });
    }

    function shouldSkip(q) {
        if (!q) return false;
        if (q.id === 'ceiling_shadow' && answers['ceiling'] === 'Ні') return true;
        const aux = answers['aux_rooms'] || [];
        if (q.id.startsWith('hallway_') && !aux.includes('Передпокій')) return true;
        if (q.id.startsWith('kitchen_') && !aux.includes('Кухня')) return true;
        if (q.id.startsWith('balcony_') && !aux.includes('Балкон')) return true;
        if (q.id.startsWith('wardrobe_') && !aux.includes('Гардероб')) return true;
        if (q.id.startsWith('basement_') && !aux.includes('Підвал')) return true;
        if (q.id.startsWith('attic_') && !aux.includes('Горище')) return true;
        return false;
    }

    function buildFullQuestionsList() {
        const bathsCount = answers['baths_count'] || 0; 
        const roomsCount = answers['rooms_count'] || 0;
        const bathsQ = []; 
        for(let i=1; i<=bathsCount; i++) bathsQ.push(...getBathQuestions(i));
        const roomsQ = []; 
        for(let i=1; i<=roomsCount; i++) roomsQ.push(...getRoomQuestions(i));
        finalQuestions = [...blockGeneral, ...blockHallway, ...bathsQ, ...blockKitchen, ...roomsQ, ...blockBalcony, ...blockWardrobe, ...blockBasement, ...blockAttic];
        updateMenu();
    }

    function updateMenu() {
        const list = document.getElementById('menu-list'); 
        list.innerHTML = ''; 
        let lastZone = "";
        finalQuestions.forEach((q, index) => {
            if (shouldSkip(q)) return;
            if (q.zone && q.zone !== lastZone) {
                lastZone = q.zone; 
                const item = document.createElement('div'); 
                item.className = 'menu-item'; 
                item.innerText = q.zone;
                item.onclick = () => { 
                    currentStep = index; 
                    toggleMenu(); 
                    document.getElementById('section-measurements').classList.add('hidden'); 
                    document.getElementById('section-questions').classList.remove('hidden'); 
                    document.getElementById('navBar').style.display = 'flex'; 
                    renderQuestion(); 
                };
                list.appendChild(item);
            }
        });
        const measItem = document.createElement('div'); 
        measItem.className = 'menu-item'; 
        measItem.innerHTML = '<b>📏 ЗАМІРИ</b>'; 
        measItem.onclick = () => { toggleMenu(); renderMeasurements(); }; 
        list.appendChild(measItem);
    }

    function toggleMenu() { document.getElementById('side-menu').classList.toggle('open'); document.getElementById('menu-overlay').classList.toggle('open'); }
    function openImage(src, event) { event.stopPropagation(); document.getElementById('modalImg').src = src; document.getElementById('imageModal').style.display = 'flex'; }
    function closeImage() { document.getElementById('imageModal').style.display = 'none'; }
    function triggerLiveCalc() { clearTimeout(calcTimeout); calcTimeout = setTimeout(fetchLiveCalc, 500); }

    async function fetchLiveCalc() {
        if(!navigator.onLine) return; 
        const area = parseFloat(document.getElementById('clientArea').value) || 0;
        if(area <= 0) return;
        const data = { client: { area: area, floor: document.getElementById('clientFloor').value, elevator: document.getElementById('clientElevator').value }, answers: answers };
        try {
            const res = await fetch(`${BACKEND_URL}/api/live_calc`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data) });
            if (!res.ok) throw new Error(`HTTP error ${res.status}`);
            const p = await res.json();
            if(p.work !== undefined) {
                document.getElementById('cart-work').innerText = p.work.toLocaleString() + ' ₴';
                document.getElementById('cart-mat').innerText = p.mat_min.toLocaleString() + ' ₴';
            }
        } catch(e) {}
    }

    function openCartModal() {
        const list = document.getElementById('cart-list-content'); list.innerHTML = ''; let grouped = {};
        for(const [key, val] of Object.entries(answers)) {
            if(key === 'measurements' || key === 'aux_rooms' || key === 'rooms_count' || key === 'baths_count' || !val || val === "Ні" || (Array.isArray(val) && val.length===0) || val === 0 || val === "0") continue;
            let q = finalQuestions.find(x => x.id === key); let zone = q ? q.zone : "ЗАГАЛЬНІ"; let label = q ? q.text.replace('.', '') : key;
            let valStr = "";
            if (typeof val === 'object' && val !== null) {
                if (val.type) valStr = `${val.type} ${val.tier && val.tier !== '-' ? '('+val.tier+')' : ''}`;
                else { let parts = []; for(let [k, v] of Object.entries(val)) parts.push(`${k} ${v && v!=='Так' ? '('+v+')' : ''}`); valStr = parts.join(', '); }
            } else if (Array.isArray(val)) { valStr = val.join(', '); } else { valStr = val; }
            if(!grouped[zone]) grouped[zone] = []; grouped[zone].push(`• <b>${label}:</b> ${valStr}`);
        }
        let html = '';
        for(const [zone, items] of Object.entries(grouped)) { html += `<div class="cart-zone-title">${zone}</div>`; html += items.join('<br>') + '<br>'; }
        list.innerHTML = html || '<i>Поки що нічого не вибрано...</i>'; document.getElementById('cart-modal').style.display = 'flex';
    }
    function closeCartModal() { document.getElementById('cart-modal').style.display = 'none'; }

    function renderQuestion() {
        const q = finalQuestions[currentStep]; document.getElementById('step-count').innerText = `${currentStep + 1}/${finalQuestions.length}`;
        const container = document.getElementById('question-container'); container.innerHTML = '';
        document.getElementById('live-cart').classList.add('visible');

        if(q.zone) { const badge = document.createElement('div'); badge.className = 'zone-badge'; badge.innerText = q.zone; container.appendChild(badge); }
        const title = document.createElement('h3'); title.innerText = q.text; container.appendChild(title);

        if (q.type === 'cards') {
            const grid = document.createElement('div'); grid.className = 'card-grid';
            q.options.forEach(opt => {
                const card = document.createElement('div'); card.className = `card ${answers[q.id] === opt.val ? 'selected' : ''}`;
                if(opt.img) {
                    const imgWrapper = document.createElement('div'); imgWrapper.className = 'img-wrapper'; const img = document.createElement('img'); img.src = opt.img;
                    img.onerror = () => { imgWrapper.style.display = 'none'; }; imgWrapper.appendChild(img); const zoom = document.createElement('div'); zoom.className = 'zoom-icon';
                    zoom.innerText = '🔍'; zoom.onclick = (e) => openImage(opt.img, e); imgWrapper.appendChild(zoom); card.appendChild(imgWrapper);
                }
                const labelDiv = document.createElement('div'); labelDiv.innerText = opt.label; card.appendChild(labelDiv);
                card.onclick = (e) => {
                    e.preventDefault(); answers[q.id] = opt.val; 
                    if (q.id === 'rooms_count' || q.id === 'baths_count') { cleanupOldAnswers(); buildFullQuestionsList(); }
                    triggerLiveCalc(); renderQuestion(); 
                }; grid.appendChild(card);
            }); container.appendChild(grid);
        } 
        else if (q.type === 'input_number') {
            const inp = document.createElement('input'); inp.type = "number"; inp.placeholder = q.placeholder; inp.value = answers[q.id] || '';
            inp.oninput = (e) => { 
                e.preventDefault(); answers[q.id] = parseInt(e.target.value) || 0; 
                if (q.id === 'rooms_count' || q.id === 'baths_count') { cleanupOldAnswers(); buildFullQuestionsList(); }
                triggerLiveCalc(); 
            }; container.appendChild(inp);
        } 
        else if (q.type === 'multiselect_dynamic') {
            const opts = []; const aux = answers['aux_rooms'] || []; aux.forEach(a => opts.push(a));
            const rooms = parseInt(answers['rooms_count']) || 0; const baths = parseInt(answers['baths_count']) || 0;
            for(let i=1; i<=rooms; i++) opts.push(`Кімната ${i}`); for(let i=1; i<=baths; i++) opts.push(`Санвузол ${i}`); opts.push("Не потребується");
            if(!Array.isArray(answers[q.id])) answers[q.id] = []; answers[q.id] = answers[q.id].filter(val => opts.includes(val));
            opts.forEach(o => {
                const btn = document.createElement('div'); const isSel = answers[q.id].includes(o);
                btn.className = `card ${isSel ? 'selected' : ''}`; btn.style.padding = "10px"; btn.style.marginBottom="5px"; btn.innerText = o;
                btn.onclick = (e) => {
                    e.preventDefault(); if(o === "Не потребується") answers[q.id] = ["Не потребується"]; else { answers[q.id] = answers[q.id].filter(x => x!=="Не потребується"); if(answers[q.id].includes(o)) answers[q.id] = answers[q.id].filter(x=>x!==o); else answers[q.id].push(o); } 
                    triggerLiveCalc(); renderQuestion(); 
                }; container.appendChild(btn);
            });
        } 
        else if (q.type === 'cards_with_tier') {
            const grid = document.createElement('div'); grid.className = 'card-grid';
            if(typeof answers[q.id] !== 'object' || answers[q.id] === null) answers[q.id] = {type:"", tier:""};
            q.options.forEach(opt => {
                const card = document.createElement('div'); const isSel = answers[q.id].type === opt.val; card.className = `card ${isSel ? 'selected' : ''}`;
                if(opt.img) {
                    const imgWrapper = document.createElement('div'); imgWrapper.className = 'img-wrapper'; const img = document.createElement('img'); img.src = opt.img;
                    img.onerror = () => { imgWrapper.style.display = 'none'; }; imgWrapper.appendChild(img); const zoom = document.createElement('div'); zoom.className = 'zoom-icon';
                    zoom.innerText = '🔍'; zoom.onclick = (e) => openImage(opt.img, e); imgWrapper.appendChild(zoom); card.appendChild(imgWrapper);
                }
                const labelDiv = document.createElement('div'); labelDiv.innerText = opt.label; card.appendChild(labelDiv);
                if(isSel && opt.val !== "Не обладнувати" && opt.val !== "Не потребується" && opt.val !== "Ні" && opt.val !== "Ні / Залишаються") {
                    const tiers = document.createElement('div'); tiers.className = "tier-container"; const variantsList = opt.variants ? opt.variants : ['Standard', 'Comfort', 'Premium'];
                    variantsList.forEach(v => { 
                        const btn = document.createElement('div'); let btnText = v; if(v==='Standard') btnText='S'; if(v==='Comfort') btnText='C'; if(v==='Premium') btnText='P'; 
                        btn.className = `tier-btn ${answers[q.id].tier === v ? 'active' : ''}`; btn.innerText = btnText; 
                        btn.onclick = (e) => { e.stopPropagation(); answers[q.id].tier = v; triggerLiveCalc(); renderQuestion(); }; tiers.appendChild(btn); 
                    }); card.appendChild(tiers);
                }
                card.onclick = (e) => { e.preventDefault(); if(answers[q.id].type !== opt.val) { const firstVar = opt.variants ? opt.variants[0] : "Standard"; answers[q.id] = {type: opt.val, tier: (opt.val.startsWith("Не") || opt.val.startsWith("Ні") ? "-" : firstVar)}; triggerLiveCalc(); renderQuestion(); } }; grid.appendChild(card);
            }); container.appendChild(grid);
        } 
        else if (q.type === 'multiselect_complex') {
            if(typeof answers[q.id] !== 'object' || answers[q.id] === null) answers[q.id] = {};
            const list = document.createElement('div');
            q.options.forEach(opt => {
                const item = document.createElement('div'); item.className = 'list-item'; const left = document.createElement('div'); left.className = 'list-left';
                const chk = document.createElement('input'); chk.type = "checkbox"; chk.checked = !!answers[q.id][opt.label];
                chk.onchange = (e) => { e.preventDefault(); if(e.target.checked) answers[q.id][opt.label] = opt.tier ? (opt.customTiers ? opt.customTiers[0] : "Standard") : "Так"; else delete answers[q.id][opt.label]; triggerLiveCalc(); renderQuestion(); }; 
                left.appendChild(chk); left.appendChild(document.createTextNode(opt.label)); item.appendChild(left);
                if(answers[q.id][opt.label] && opt.tier) { 
                    const sel = document.createElement('select'); sel.className = 'tier-select'; const variants = opt.customTiers ? opt.customTiers : ['Standard', 'Comfort', 'Premium']; 
                    variants.forEach(t => { const op = document.createElement('option'); op.value=t; op.innerText=t; if(answers[q.id][opt.label]===t) op.selected=true; sel.appendChild(op); }); 
                    sel.onchange = (e) => { e.preventDefault(); answers[q.id][opt.label] = e.target.value; triggerLiveCalc(); }; item.appendChild(sel); 
                } list.appendChild(item);
            });
            if (q.hasOther) { 
                const otherInput = document.createElement('input'); otherInput.placeholder = "Інше (впишіть вручну)..."; otherInput.style.marginTop = "10px"; otherInput.value = answers[q.id]['Other'] || ''; 
                otherInput.oninput = (e) => { e.preventDefault(); if(e.target.value) answers[q.id]['Other'] = e.target.value; else delete answers[q.id]['Other']; triggerLiveCalc(); }; list.appendChild(otherInput); 
            } container.appendChild(list);
        } 
        else if (q.type === 'cards_multiselect') {
            const grid = document.createElement('div'); grid.className = 'card-grid'; if (!Array.isArray(answers[q.id])) answers[q.id] = [];
            q.options.forEach(opt => {
                const card = document.createElement('div'); const isSel = answers[q.id].includes(opt.val); card.className = `card ${isSel ? 'selected' : ''}`;
                if(opt.img) {
                    const imgWrapper = document.createElement('div'); imgWrapper.className = 'img-wrapper'; const img = document.createElement('img'); img.src = opt.img;
                    img.onerror = () => { imgWrapper.style.display = 'none'; }; imgWrapper.appendChild(img); const zoom = document.createElement('div'); zoom.className = 'zoom-icon';
                    zoom.innerText = '🔍'; zoom.onclick = (e) => openImage(opt.img, e); imgWrapper.appendChild(zoom); card.appendChild(imgWrapper);
                }
                const labelDiv = document.createElement('div'); labelDiv.innerText = opt.label; card.appendChild(labelDiv);
                card.onclick = (e) => {
                    e.preventDefault(); 
                    if(opt.val.startsWith("Не") || opt.val === "ні") { 
                        answers[q.id] = [opt.val]; 
                    } else { 
                        answers[q.id] = answers[q.id].filter(v => !v.startsWith("Не") && v !== "ні"); 
                        if(answers[q.id].includes(opt.val)) answers[q.id] = answers[q.id].filter(v=>v!==opt.val); 
                        else answers[q.id].push(opt.val); 
                    } 
                    if (q.id === 'aux_rooms') { cleanupOldAnswers(); buildFullQuestionsList(); } else { updateMenu(); }
                    triggerLiveCalc(); renderQuestion(); 
                }; grid.appendChild(card);
            }); container.appendChild(grid);
        }
        document.getElementById('btnNext').innerText = 'Далі';
    }

    function renderMeasurements() {
        document.getElementById('live-cart').classList.add('visible'); document.getElementById('section-questions').classList.add('hidden'); document.getElementById('section-measurements').classList.remove('hidden');
        const btnNext = document.getElementById('btnNext'); btnNext.innerText = editId ? '✅ Зберегти зміни' : '✅ Завершити'; btnNext.onclick = finishSurvey;
        const btnBack = document.getElementById('btnBack'); btnBack.onclick = () => { document.getElementById('section-measurements').classList.add('hidden'); document.getElementById('section-questions').classList.remove('hidden'); btnNext.innerText = 'Далі'; btnNext.onclick = goNext; btnBack.onclick = goBack; renderQuestion(); };
        const container = document.getElementById('measurements-container'); container.innerHTML = ''; answers.measurements = answers.measurements || {}; const zones = []; const aux = answers['aux_rooms'] || [];
        if (aux.includes('Передпокій')) zones.push({id: 'hallway', name: 'Передпокій'}); if (aux.includes('Кухня')) zones.push({id: 'kitchen', name: 'Кухня'}); if (aux.includes('Балкон')) zones.push({id: 'balcony', name: 'Балкон'}); if (aux.includes('Гардероб')) zones.push({id: 'wardrobe', name: 'Гардероб'}); if (aux.includes('Підвал')) zones.push({id: 'basement', name: 'Підвал'}); if (aux.includes('Горище')) zones.push({id: 'attic', name: 'Горище'});
        const roomsCount = answers['rooms_count'] || 0; for(let i=1; i<=roomsCount; i++) zones.push({id: `room_${i}`, name: `Кімната ${i}`});
        const bathsCount = answers['baths_count'] || 0; for(let i=1; i<=bathsCount; i++) zones.push({id: `bath_${i}`, name: `Санвузол ${i}`});
        if (zones.length === 0) { container.innerHTML = '<p>Немає вибраних приміщень для замірів.</p>'; return; }
        zones.forEach(zone => {
            if(!answers.measurements[zone.id]) answers.measurements[zone.id] = {floor: '', walls: ''};
            const box = document.createElement('div'); box.className = 'measurement-box';
            box.innerHTML = `<h4>${zone.name}</h4><label>Площа підлоги (м²)</label><input type="number" placeholder="0" value="${answers.measurements[zone.id].floor}" class="inp-floor"><label>Площа стін (м²)</label><input type="number" placeholder="0" value="${answers.measurements[zone.id].walls}" class="inp-walls">`;
            const inpFloor = box.querySelector('.inp-floor'); const inpWalls = box.querySelector('.inp-walls');
            inpFloor.oninput = (e) => { e.preventDefault(); const val = parseFloat(e.target.value) || 0; answers.measurements[zone.id].floor = val; if (inpWalls.value === '' || parseFloat(inpWalls.value) === 0) { const calculatedWalls = (val * 2.5).toFixed(1); inpWalls.value = calculatedWalls; answers.measurements[zone.id].walls = parseFloat(calculatedWalls) || 0; } triggerLiveCalc(); };
            inpWalls.oninput = (e) => { e.preventDefault(); answers.measurements[zone.id].walls = parseFloat(e.target.value) || 0; triggerLiveCalc(); }; container.appendChild(box);
        }); triggerLiveCalc(); 
    }

    function goNext() {
        if (currentStep === -1) {
            const name = document.getElementById('clientName').value; const area = document.getElementById('clientArea').value;
            if (!name) return tg.showAlert("Введіть ім'я!"); if (!area) return tg.showAlert("Введіть загальну площу!");
            buildFullQuestionsList(); triggerLiveCalc(); document.getElementById('section-client').classList.add('hidden'); document.getElementById('section-questions').classList.remove('hidden'); document.getElementById('btnBack').style.display = 'block'; currentStep = 0; renderQuestion(); return;
        }
        let nextStep = currentStep + 1; while(nextStep < finalQuestions.length && shouldSkip(finalQuestions[nextStep])) { nextStep++; }
        if (nextStep < finalQuestions.length) { currentStep = nextStep; renderQuestion(); } else { renderMeasurements(); }
    }

    function goBack() {
        let prevStep = currentStep - 1; while(prevStep >= 0 && shouldSkip(finalQuestions[prevStep])) { prevStep--; }
        if (prevStep >= 0) { currentStep = prevStep; renderQuestion(); } else { currentStep = -1; document.getElementById('live-cart').classList.remove('visible'); document.getElementById('section-client').classList.remove('hidden'); document.getElementById('section-questions').classList.add('hidden'); document.getElementById('btnBack').style.display = 'none'; }
    }

    function finishSurvey() {
        localStorage.removeItem('remont_draft_' + (editId || 'new')); document.getElementById('section-measurements').classList.add('hidden'); document.getElementById('navBar').style.display = 'none'; document.getElementById('live-cart').style.display = 'none';
        const thx = document.getElementById('section-thankyou'); thx.innerHTML = '<div class="check-icon">⏳</div><h3>Зберігаємо...</h3><p>Зачекайте мить.</p>'; thx.classList.remove('hidden');
        const data = { edit_id: editId, client: { name: document.getElementById('clientName').value, phone: document.getElementById('clientPhone').value, object_type: document.getElementById('objectType').value, address: document.getElementById('clientAddress').value, area: document.getElementById('clientArea').value, floor: document.getElementById('clientFloor').value, elevator: document.getElementById('clientElevator').value }, answers: answers };
        if (editId) { if (navigator.onLine) { fetch(`${BACKEND_URL}/api/save_order`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Telegram-Init-Data': tg.initData }, body: JSON.stringify(data) }).then(res => { if (!res.ok) throw new Error("Помилка сервера"); tg.close(); }).catch(() => queueOffline(data)); } else { queueOffline(data); } } else { tg.sendData(JSON.stringify(data)); }
    }

    function queueOffline(data) { let q = JSON.parse(localStorage.getItem('remont_offline_q') || '[]'); q.push({ data: data, initData: tg.initData }); localStorage.setItem('remont_offline_q', JSON.stringify(q)); tg.showAlert("📡 Немає інтернету! Дані збережено на телефоні. Вони відправляться автоматично, коли з'явиться мережа."); tg.close(); }

    async function syncOfflineQueue() {
        if (!navigator.onLine) return; let q = JSON.parse(localStorage.getItem('remont_offline_q') || '[]'); if (q.length === 0) return; let remaining = [];
        for (let item of q) { try { const res = await fetch(`${BACKEND_URL}/api/save_order`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Telegram-Init-Data': item.initData }, body: JSON.stringify(item.data) }); if (!res.ok) throw new Error("Fail"); } catch(e) { remaining.push(item); } }
        if (remaining.length < q.length) { tg.showAlert("✅ Зв'язок відновлено. Відкладені заявки відправлені!"); } localStorage.setItem('remont_offline_q', JSON.stringify(remaining));
    }
    window.addEventListener('online', syncOfflineQueue);

    function saveDraft() {
        if (!document.getElementById('section-thankyou').classList.contains('hidden')) return;
        const draftData = { currentStep: currentStep, answers: answers, client: { name: document.getElementById('clientName').value, phone: document.getElementById('clientPhone').value, object_type: document.getElementById('objectType').value, address: document.getElementById('clientAddress').value, area: document.getElementById('clientArea').value, floor: document.getElementById('clientFloor').value, elevator: document.getElementById('clientElevator').value } };
        localStorage.setItem('remont_draft_' + (editId || 'new'), JSON.stringify(draftData));
    }
    setInterval(saveDraft, 2000);

    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.has('edit_id')) {
        editId = urlParams.get('edit_id'); const savedDraft = localStorage.getItem('remont_draft_' + editId);
        if (savedDraft) { tg.showConfirm("У вас є незбережена чернетка. Відновити?", function(restored) { if(restored) applyDraft(savedDraft); else { localStorage.removeItem('remont_draft_' + editId); loadEditData(); } }); } else { loadEditData(); }
    } else { syncOfflineQueue(); const savedDraft = localStorage.getItem('remont_draft_new'); if (savedDraft) { tg.showConfirm("У вас є незбережена чернетка. Відновити?", function(restored) { if(restored) applyDraft(savedDraft); else localStorage.removeItem('remont_draft_new'); }); } }

    function applyDraft(savedString) {
        try {
            const parsed = JSON.parse(savedString); document.getElementById('clientName').value = parsed.client.name || ""; document.getElementById('clientPhone').value = parsed.client.phone || ""; document.getElementById('objectType').value = parsed.client.object_type || "Квартира (Новобудова)"; document.getElementById('clientAddress').value = parsed.client.address || ""; document.getElementById('clientArea').value = parsed.client.area || ""; document.getElementById('clientFloor').value = parsed.client.floor || "1"; document.getElementById('clientElevator').value = parsed.client.elevator || "Немає";
            answers = parsed.answers || {}; currentStep = parsed.currentStep || -1; buildFullQuestionsList();
            if (currentStep > -1) { document.getElementById('section-client').classList.add('hidden'); document.getElementById('section-questions').classList.remove('hidden'); document.getElementById('btnBack').style.display = 'block'; triggerLiveCalc(); if (currentStep >= finalQuestions.length) renderMeasurements(); else renderQuestion(); }
        } catch(e) {}
    }

    async function loadEditData() {
        document.getElementById('section-client').classList.add('hidden'); document.getElementById('loading-screen').classList.remove('hidden');
        try {
            const res = await fetch(`${BACKEND_URL}/api/get_order?id=${editId}`, { headers: { 'X-Telegram-Init-Data': tg.initData } });
            if (res.status === 423) { const err = await res.json(); tg.showAlert(err.error); tg.close(); return; }
            if (!res.ok) throw new Error("Server error");
            const data = await res.json(); document.getElementById('clientName').value = data.client.name || ""; document.getElementById('clientPhone').value = data.client.phone || ""; document.getElementById('objectType').value = data.client.object_type || "Квартира (Новобудова)"; document.getElementById('clientAddress').value = data.client.address || ""; document.getElementById('clientArea').value = data.client.area || ""; document.getElementById('clientFloor').value = data.client.floor || "1"; document.getElementById('clientElevator').value = data.client.elevator || "Немає";
            answers = data.answers || {}; buildFullQuestionsList(); currentStep = 0; document.getElementById('loading-screen').classList.add('hidden'); document.getElementById('section-questions').classList.remove('hidden'); document.getElementById('btnBack').style.display = 'block'; triggerLiveCalc(); renderQuestion();
        } catch(e) { alert("Помилка з'єднання."); document.getElementById('loading-screen').classList.add('hidden'); document.getElementById('section-client').classList.remove('hidden'); }
    }
</script>
</body>
</html>
