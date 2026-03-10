CREATE TABLE IF NOT EXISTS public.automation_action_intents (
    id bigserial PRIMARY KEY,
    run_id bigint NOT NULL REFERENCES public.automation_runs(id) ON DELETE CASCADE,
    action_index integer NOT NULL,
    action_type text NOT NULL,
    symbol character varying(32),
    strategy_type text,
    status text NOT NULL,
    confidence text,
    quantity integer,
    option_type text,
    expiration_date timestamp with time zone,
    strike_price numeric(18, 4),
    limit_price numeric(18, 4),
    related_position_key text,
    validation_status text NOT NULL DEFAULT 'pending',
    execution_status text NOT NULL DEFAULT 'not_submitted',
    schwab_order_id text,
    rationale jsonb NOT NULL DEFAULT '[]'::jsonb,
    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    validation_errors jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT automation_action_intents_option_type_check CHECK (option_type IS NULL OR option_type IN ('CALL', 'PUT')),
    CONSTRAINT automation_action_intents_validation_status_check CHECK (validation_status IN ('pending', 'valid', 'invalid', 'skipped')),
    CONSTRAINT automation_action_intents_execution_status_check CHECK (execution_status IN ('not_submitted', 'submitted', 'filled', 'rejected', 'failed', 'skipped'))
);


CREATE INDEX IF NOT EXISTS automation_action_intents_run_idx
    ON public.automation_action_intents (run_id, action_index);


CREATE INDEX IF NOT EXISTS automation_action_intents_execution_idx
    ON public.automation_action_intents (execution_status, created_at DESC);