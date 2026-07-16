-- Схема Postgres для remontnikuav (Етап 3).
-- Запусти цей SQL один раз у Supabase → SQL Editor.
-- Ідемпотентний: повторний запуск нічого не зламає.

-- ── Заявки ────────────────────────────────────────────────
-- Відповідає колонкам старої Google-таблиці (SHEET_HEADER), плюс поля
-- для захисту від подвійного збереження і м'якого видалення.
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
    manager_id    TEXT        DEFAULT '',          -- автор / хто взяв лід
    source        TEXT        NOT NULL DEFAULT 'web',        -- web | manager
    deal          TEXT        NOT NULL DEFAULT 'new',        -- new|sent|won|lost
    submission_id TEXT,                            -- ключ ідемпотентності (з фронта)
    deleted_by    TEXT,
    deleted_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ЗАХИСТ ВІД ПОДВІЙНОГО ЗБЕРЕЖЕННЯ:
-- якщо фронт двічі надішле ту саму заявку — обидва запити несуть один
-- submission_id, і друга вставка фізично неможлива на рівні БД (гонка теж
-- виключена). Індекс частковий: заявки без submission_id (старі/сервісні)
-- обмеженню не підлягають — NULL-и в Postgres унікальність не порушують.
CREATE UNIQUE INDEX IF NOT EXISTS orders_submission_id_key
    ON orders (submission_id) WHERE submission_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS orders_status_idx     ON orders (status);
CREATE INDEX IF NOT EXISTS orders_manager_idx    ON orders (manager_id);

-- ── Чернетки ──────────────────────────────────────────────
-- Одна чернетка на користувача (upsert по user_id).
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
