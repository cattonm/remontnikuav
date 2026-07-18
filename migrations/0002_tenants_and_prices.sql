-- 0002 · Фундамент SaaS: тенанти + прайс у базі.
--
-- ЩО РОБИТЬ:
--   1. Заводить таблицю tenants і створює тенанта №1 — поточну компанію.
--      Далі кожна нова компанія = новий рядок тут.
--   2. Переносить прайс із Google-таблиці в БД (структуру; самі дані
--      заливає скрипт import_prices.py).
--   3. Дописує tenant_id до вже наявних таблиць зі значенням 1.
--
-- ЧОМУ tenant_id ДОДАЄМО ЗАРАЗ, ХОЧА МУЛЬТИТЕНАНТНІСТЬ — ЕТАП B:
--   ALTER TABLE ... ADD COLUMN ... DEFAULT 1 у Postgres 11+ не переписує
--   таблицю — це миттєва операція на метаданих. Зробити це на 40 рядках
--   зараз коштує нуль; зробити на 40 000 і з десятком залежних запитів
--   пізніше — це вечір роботи й ризик даунтайму. Код поки що колонку
--   не читає: вона просто чекає Етапу B.

-- ── Тенанти (компанії-клієнти платформи) ──────────────────
CREATE TABLE IF NOT EXISTS tenants (
    id         BIGSERIAL   PRIMARY KEY,
    slug       TEXT        NOT NULL UNIQUE,   -- для піддомену/віджета: ?tenant=<slug>
    name       TEXT        NOT NULL DEFAULT '',
    plan       TEXT        NOT NULL DEFAULT 'free',   -- free | starter | pro
    currency   TEXT        NOT NULL DEFAULT 'UAH',
    settings   JSONB       NOT NULL DEFAULT '{}'::jsonb,  -- брендинг, ліміти, прапорці фіч
    status     TEXT        NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Тенант №1 — компанія, що вже працює. Усі наявні дані належать їй.
INSERT INTO tenants (id, slug, name)
VALUES (1, 'remontnikuav', 'Ремонтник UA')
ON CONFLICT (id) DO NOTHING;

-- Зсуваємо лічильник, щоб наступний тенант не спробував зайняти id=1.
SELECT setval(pg_get_serial_sequence('tenants', 'id'),
              GREATEST((SELECT MAX(id) FROM tenants), 1));

-- ── Прайс ─────────────────────────────────────────────────
-- Один рядок = одна позиція кошторису для конкретного тенанта.
-- work / mat_min / mat_max — те саме тріо, що зараз лежить у аркуші «Ціни».
CREATE TABLE IF NOT EXISTS prices (
    id         BIGSERIAL   PRIMARY KEY,
    tenant_id  BIGINT      NOT NULL DEFAULT 1 REFERENCES tenants(id) ON DELETE CASCADE,
    key        TEXT        NOT NULL,                 -- price_key з calculator.PRICE_META
    label      TEXT        NOT NULL DEFAULT '',      -- людська назва (можна змінювати під себе)
    unit       TEXT        NOT NULL DEFAULT '',      -- м² | шт | точок — для UI редактора
    work       NUMERIC(12, 2) NOT NULL DEFAULT 0,    -- вартість роботи
    mat_min    NUMERIC(12, 2) NOT NULL DEFAULT 0,    -- матеріал: мінімум
    mat_max    NUMERIC(12, 2) NOT NULL DEFAULT 0,    -- матеріал: максимум
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by TEXT        NOT NULL DEFAULT '',      -- хто останній правив (id менеджера)
    CONSTRAINT prices_non_negative CHECK (work >= 0 AND mat_min >= 0 AND mat_max >= 0),
    CONSTRAINT prices_mat_range    CHECK (mat_max >= mat_min)
);

-- Один ключ на тенанта. Це також ціль для ON CONFLICT в upsert.
CREATE UNIQUE INDEX IF NOT EXISTS prices_tenant_key_idx ON prices (tenant_id, key);

-- ── tenant_id на наявних таблицях ─────────────────────────
ALTER TABLE orders     ADD COLUMN IF NOT EXISTS tenant_id BIGINT NOT NULL DEFAULT 1;
ALTER TABLE drafts     ADD COLUMN IF NOT EXISTS tenant_id BIGINT NOT NULL DEFAULT 1;
ALTER TABLE action_log ADD COLUMN IF NOT EXISTS tenant_id BIGINT NOT NULL DEFAULT 1;

CREATE INDEX IF NOT EXISTS orders_tenant_idx ON orders (tenant_id);
