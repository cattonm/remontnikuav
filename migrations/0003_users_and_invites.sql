-- 0003 · Користувачі та інвайти переїжджають із Google-таблиці в БД.
--
-- НАВІЩО ЗАРАЗ. Авторизація — єдине, що ще трималось на Google API. Коли
-- ключ сервіс-акаунта помер, кабінет ліг для всіх, окрім майстер-адміна
-- (він захардкоджений і перевіряється до звернення в Google). Після цієї
-- міграції Google не потрібен для входу взагалі.
--
-- Колонки email / password_hash додаємо порожніми наперед — на Етапі B
-- туди ляже вхід поштою, і ще одна міграція не знадобиться.

-- ── Користувачі ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL   PRIMARY KEY,
    tenant_id     BIGINT      NOT NULL DEFAULT 1 REFERENCES tenants(id) ON DELETE CASCADE,
    tg_id         TEXT,                                  -- Telegram id (текстом, як усюди в проєкті)
    email         TEXT,                                  -- Етап B
    password_hash TEXT,                                  -- Етап B
    name          TEXT        NOT NULL DEFAULT '',
    username      TEXT        NOT NULL DEFAULT '',
    role          TEXT        NOT NULL DEFAULT 'manager',
    status        TEXT        NOT NULL DEFAULT 'active', -- active | revoked
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ,
    CONSTRAINT users_role_valid   CHECK (role IN ('owner', 'admin', 'manager')),
    CONSTRAINT users_status_valid CHECK (status IN ('active', 'revoked')),
    -- Порожній рядок — не ідентифікатор. Хоча б один спосіб входу мусить бути.
    CONSTRAINT users_has_identity CHECK (
        (tg_id IS NOT NULL AND tg_id <> '') OR (email IS NOT NULL AND email <> '')
    )
);

-- Один Telegram-акаунт = один користувач у межах тенанта.
-- Часткові індекси (WHERE ... IS NOT NULL) дозволяють мати користувачів
-- лише з поштою або лише з телеграмом, без конфліктів на NULL.
CREATE UNIQUE INDEX IF NOT EXISTS users_tenant_tg_idx
    ON users (tenant_id, tg_id) WHERE tg_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS users_tenant_email_idx
    ON users (tenant_id, lower(email)) WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS users_tenant_role_idx ON users (tenant_id, role);

-- ── Інвайти ───────────────────────────────────────────────
-- Одноразовий код на конкретну людину: гаситься при першому використанні,
-- протухає за INVITE_TTL_DAYS.
CREATE TABLE IF NOT EXISTS invites (
    id         BIGSERIAL   PRIMARY KEY,
    tenant_id  BIGINT      NOT NULL DEFAULT 1 REFERENCES tenants(id) ON DELETE CASCADE,
    code       TEXT        NOT NULL,
    role       TEXT        NOT NULL DEFAULT 'manager',
    created_by TEXT        NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    used_by    TEXT,
    used_at    TIMESTAMPTZ,
    CONSTRAINT invites_role_valid CHECK (role IN ('owner', 'admin', 'manager'))
);

-- Код унікальний глобально: його диктують голосом, і колізія між тенантами
-- призвела б до входу не в ту компанію.
CREATE UNIQUE INDEX IF NOT EXISTS invites_code_idx ON invites (code);
CREATE INDEX IF NOT EXISTS invites_tenant_open_idx
    ON invites (tenant_id, expires_at) WHERE used_by IS NULL;
