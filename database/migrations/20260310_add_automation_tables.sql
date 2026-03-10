CREATE TABLE IF NOT EXISTS public.automation_runs (
    id bigserial PRIMARY KEY,
    service_name text NOT NULL,
    run_type text NOT NULL,
    status text NOT NULL,
    dry_run boolean NOT NULL DEFAULT true,
    started_at timestamp with time zone NOT NULL DEFAULT now(),
    completed_at timestamp with time zone,
    account_hash text,
    prompt_version text,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_text text,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT automation_runs_status_check CHECK (status IN ('running', 'completed', 'failed', 'skipped'))
);


CREATE INDEX IF NOT EXISTS automation_runs_started_idx
    ON public.automation_runs (started_at DESC);


CREATE TABLE IF NOT EXISTS public.broker_accounts (
    id bigserial PRIMARY KEY,
    account_hash text NOT NULL UNIQUE,
    account_number text,
    account_type text,
    display_name text,
    is_active boolean NOT NULL DEFAULT true,
    cash_available numeric(18, 2),
    cash_reserved numeric(18, 2),
    liquidation_value numeric(18, 2),
    balances jsonb NOT NULL DEFAULT '{}'::jsonb,
    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    last_synced_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now()
);


CREATE TABLE IF NOT EXISTS public.broker_positions (
    id bigserial PRIMARY KEY,
    account_hash text NOT NULL REFERENCES public.broker_accounts(account_hash) ON DELETE CASCADE,
    position_key text NOT NULL,
    underlying_symbol character varying(32) NOT NULL,
    asset_type text NOT NULL,
    instrument_type text,
    option_type text,
    expiration_date timestamp with time zone,
    strike_price numeric(18, 4),
    quantity numeric(18, 4) NOT NULL DEFAULT 0,
    long_quantity numeric(18, 4) NOT NULL DEFAULT 0,
    short_quantity numeric(18, 4) NOT NULL DEFAULT 0,
    average_price numeric(18, 4),
    market_value numeric(18, 4),
    cost_basis numeric(18, 4),
    is_active boolean NOT NULL DEFAULT true,
    synced_at timestamp with time zone NOT NULL DEFAULT now(),
    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT broker_positions_option_type_check CHECK (option_type IS NULL OR option_type IN ('CALL', 'PUT')),
    CONSTRAINT broker_positions_unique_key UNIQUE (account_hash, position_key)
);


CREATE INDEX IF NOT EXISTS broker_positions_account_active_idx
    ON public.broker_positions (account_hash, is_active, underlying_symbol);


CREATE TABLE IF NOT EXISTS public.automation_decisions (
    id bigserial PRIMARY KEY,
    run_id bigint NOT NULL REFERENCES public.automation_runs(id) ON DELETE CASCADE,
    action_type text NOT NULL,
    symbol character varying(32),
    status text NOT NULL,
    rationale text,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT automation_decisions_status_check CHECK (status IN ('proposed', 'approved', 'executed', 'rejected', 'skipped', 'failed'))
);


CREATE INDEX IF NOT EXISTS automation_decisions_run_idx
    ON public.automation_decisions (run_id, created_at DESC);