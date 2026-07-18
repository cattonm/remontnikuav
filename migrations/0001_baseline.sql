-- 0001 · Базовий зріз схеми (те, що вже стоїть у проді з Етапу 3).
--
-- Ця міграція НЕ вносить змін у робочу базу: усе створюється через
-- IF NOT EXISTS, тож на живому Supabase вона просто зафіксує факт
-- «ці таблиці вже є» і запише себе в schema_migrations. На чистій базі
-- (локальна розробка, staging) — підніме схему з нуля.
--
-- Зміст перенесено один-в-один зі старого schema.sql.

-- ── Заявки ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS orders (
    id            BIGSERIAL PRIMARY KEY,          -- = колишній row_id
    date_text     TEXT        NOT NULL DEFAULT '',-- «Дата» (рядок, як у таблиці)
    name          TEXT        DEFAULT '',
    phone         TEXT        DEFAULT '',
    object_type   TEXT        DEFAULT '',
    address       TEXT        DEFAULT '',          -- адреса + площа/поверх/ліфт одним рядком
    answers       JSONB       NOT NULL DEFAULT '{}'::jsonb,  -- повна анкета (весь data)
    report        TEXT        DEFAULT '',          -- згенерований звіт
    status        TEXT        NOT NULL DEFAULT 'активна',    -- активна | видалена
    manager_id    TEXT        DEFAULT '',          -- автор заявки
    source        TEXT        NOT NULL DEFAULT 'web',        -- web | manager
    deal          TEXT        NOT NULL DEFAULT 'new',        -- історична колонка, більше не використовується
    submission_id TEXT,                            -- ключ ідемпотентності (з фронта)
    deleted_by    TEXT,
    deleted_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ЗАХИСТ ВІД ПОДВІЙНОГО ЗБЕРЕЖЕННЯ: два однакових запити несуть один
-- submission_id, і друга вставка фізично неможлива на рівні БД.
CREATE UNIQUE INDEX IF NOT EXISTS orders_submission_id_key
    ON orders (submission_id) WHERE submission_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS orders_status_idx  ON orders (status);
CREATE INDEX IF NOT EXISTS orders_manager_idx ON orders (manager_id);

-- ── Чернетки ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS drafts (
    user_id    TEXT        PRIMARY KEY,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload    JSONB       NOT NULL,
    reminded   BOOLEAN     NOT NULL DEFAULT FALSE
);

-- ── Журнал дій ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS action_log (
    id        BIGSERIAL   PRIMARY KEY,
    ts        TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_name TEXT,
    action    TEXT
);
