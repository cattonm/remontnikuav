-- 0004 · Ціна рівня «Комфорт» стає редагованою.
--
-- ЯК ЦЕ ПРАЦЮЄ ЗАРАЗ. Три рівні беруться з тих самих трьох чисел:
--     Стандарт → mat_min
--     Преміум  → mat_max
--     Комфорт  → середнє між ними
-- Але для 18 позицій середнє давало неправильну ціну, тому в calculator.py
-- зʼявився словник overrides_c із «правильними» числами прямо в коді.
-- Наприклад ванна: стандарт 15 000, преміум 100 000, середнє 57 500 —
-- а реальний комфорт 40 000.
--
-- НАСЛІДОК: ці 18 цін не можна було змінити ні в Google-таблиці, ні в
-- кабінеті — лише правкою коду і деплоєм. Тепер вони живуть у БД.
--
-- mat_mid = NULL означає «рахувати як середнє», тобто поведінка тих позицій,
-- де окрема ціна комфорту не потрібна, лишається незмінною.

ALTER TABLE prices ADD COLUMN IF NOT EXISTS mat_mid NUMERIC(12, 2);

-- Переносимо в БД саме ті числа, що зараз у коді, — щоб після міграції
-- кошториси не змінились ні на копійку. Далі їх можна правити в кабінеті.
UPDATE prices AS p SET mat_mid = v.value
FROM (VALUES
    ('radiator',            6000),
    ('ac',                 27000),
    ('bath_tub',           40000),
    ('toilet_okrem',       10000),
    ('toilet_install',     22000),
    ('sink_cabinet',       20000),
    ('boiler_100',         13800),
    ('boiler_300',         13800),
    ('towel_dryer',         7500),
    ('hygienic_shower',     6000),
    ('mirror_led',          5500),
    ('mixer_std',           6000),
    ('mixer_hidden',       10000),
    ('tech_washer',        25000),
    ('tech_kitchen',       18000),
    ('tech_osmos',         15000),
    ('door_entrance_mdf',  30000),
    ('door_entrance_armor',30000)
) AS v(key, value)
WHERE p.key = v.key AND p.mat_mid IS NULL;

-- Комфорт мусить лежати між стандартом і преміумом — інакше рівні
-- переплутаються місцями і кошторис «Комфорт» вийде дорожчим за «Преміум».
ALTER TABLE prices DROP CONSTRAINT IF EXISTS prices_mid_in_range;
ALTER TABLE prices ADD CONSTRAINT prices_mid_in_range
    CHECK (mat_mid IS NULL OR (mat_mid >= 0 AND mat_mid >= mat_min AND mat_mid <= mat_max));
