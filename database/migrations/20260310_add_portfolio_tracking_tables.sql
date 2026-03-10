CREATE TABLE IF NOT EXISTS public.users (
    id bigserial PRIMARY KEY,
    discord_user_id bigint NOT NULL UNIQUE,
    username text,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now()
);


CREATE TABLE IF NOT EXISTS public.portfolios (
    id bigserial PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    name text NOT NULL DEFAULT 'default',
    is_default boolean NOT NULL DEFAULT true,
    cash_available numeric(18, 2) NOT NULL DEFAULT 0,
    cash_reserved numeric(18, 2) NOT NULL DEFAULT 0,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT portfolios_user_name_key UNIQUE (user_id, name)
);


CREATE UNIQUE INDEX IF NOT EXISTS portfolios_one_default_per_user_idx
    ON public.portfolios (user_id)
    WHERE is_default;


CREATE TABLE IF NOT EXISTS public.positions (
    id bigserial PRIMARY KEY,
    portfolio_id bigint NOT NULL REFERENCES public.portfolios(id) ON DELETE CASCADE,
    symbol character varying(16) NOT NULL,
    asset_type text NOT NULL,
    strategy_type text NOT NULL,
    status text NOT NULL DEFAULT 'open',
    quantity integer NOT NULL,
    opened_at timestamp with time zone NOT NULL DEFAULT now(),
    closed_at timestamp with time zone,
    notes text,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT positions_asset_type_check CHECK (asset_type IN ('stock', 'option_strategy')),
    CONSTRAINT positions_status_check CHECK (status IN ('open', 'closed', 'expired', 'assigned'))
);


CREATE INDEX IF NOT EXISTS positions_portfolio_status_idx
    ON public.positions (portfolio_id, status, opened_at DESC);


CREATE TABLE IF NOT EXISTS public.position_legs (
    id bigserial PRIMARY KEY,
    position_id bigint NOT NULL REFERENCES public.positions(id) ON DELETE CASCADE,
    leg_type text NOT NULL,
    status text NOT NULL DEFAULT 'open',
    side text NOT NULL,
    quantity integer NOT NULL,
    symbol character varying(16) NOT NULL,
    option_type text,
    strike numeric(18, 4),
    expiration date,
    entry_price numeric(18, 4) NOT NULL,
    opened_at timestamp with time zone NOT NULL DEFAULT now(),
    closed_at timestamp with time zone,
    exit_price numeric(18, 4),
    notes text,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT position_legs_leg_type_check CHECK (leg_type IN ('stock', 'option')),
    CONSTRAINT position_legs_status_check CHECK (status IN ('open', 'closed', 'expired', 'assigned')),
    CONSTRAINT position_legs_side_check CHECK (side IN ('buy', 'sell')),
    CONSTRAINT position_legs_option_type_check CHECK (option_type IS NULL OR option_type IN ('call', 'put')),
    CONSTRAINT position_legs_option_shape_check CHECK (
        (leg_type = 'stock' AND option_type IS NULL AND strike IS NULL AND expiration IS NULL)
        OR (leg_type = 'option' AND option_type IS NOT NULL AND strike IS NOT NULL AND expiration IS NOT NULL)
    )
);


CREATE INDEX IF NOT EXISTS position_legs_position_status_idx
    ON public.position_legs (position_id, status);


CREATE TABLE IF NOT EXISTS public.trade_events (
    id bigserial PRIMARY KEY,
    position_id bigint NOT NULL REFERENCES public.positions(id) ON DELETE CASCADE,
    position_leg_id bigint REFERENCES public.position_legs(id) ON DELETE SET NULL,
    event_type text NOT NULL,
    occurred_at timestamp with time zone NOT NULL DEFAULT now(),
    notes text,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT trade_events_type_check CHECK (event_type IN ('open', 'close', 'roll', 'assign', 'expire', 'note', 'cash_update'))
);


CREATE INDEX IF NOT EXISTS trade_events_position_occurred_idx
    ON public.trade_events (position_id, occurred_at DESC);