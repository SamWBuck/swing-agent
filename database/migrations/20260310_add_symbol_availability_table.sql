CREATE TABLE IF NOT EXISTS public.symbol_availability (
    symbol character varying(4) COLLATE pg_catalog."default" PRIMARY KEY,
    latest_1m_ts timestamp with time zone,
    latest_5m_ts timestamp with time zone,
    latest_10m_ts timestamp with time zone,
    latest_15m_ts timestamp with time zone,
    latest_30m_ts timestamp with time zone,
    latest_day_ts timestamp with time zone,
    latest_week_ts timestamp with time zone,
    updated_at timestamp with time zone NOT NULL DEFAULT now()
);


CREATE OR REPLACE FUNCTION public.sync_symbol_availability_from_price_candles()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO public.symbol_availability (
        symbol,
        latest_1m_ts,
        latest_5m_ts,
        latest_10m_ts,
        latest_15m_ts,
        latest_30m_ts,
        latest_day_ts,
        latest_week_ts,
        updated_at
    )
    VALUES (
        NEW.symbol,
        CASE WHEN NEW.interval IN ('1m', '1min') THEN NEW.ts END,
        CASE WHEN NEW.interval IN ('5m', '5min') THEN NEW.ts END,
        CASE WHEN NEW.interval IN ('10m', '10min') THEN NEW.ts END,
        CASE WHEN NEW.interval IN ('15m', '15min') THEN NEW.ts END,
        CASE WHEN NEW.interval IN ('30m', '30min') THEN NEW.ts END,
        CASE WHEN NEW.interval IN ('1d', 'day', 'daily') THEN NEW.ts END,
        CASE WHEN NEW.interval IN ('1w', 'week', 'weekly') THEN NEW.ts END,
        now()
    )
    ON CONFLICT (symbol) DO UPDATE
    SET latest_1m_ts = CASE
            WHEN EXCLUDED.latest_1m_ts IS NULL THEN symbol_availability.latest_1m_ts
            WHEN symbol_availability.latest_1m_ts IS NULL THEN EXCLUDED.latest_1m_ts
            ELSE GREATEST(symbol_availability.latest_1m_ts, EXCLUDED.latest_1m_ts)
        END,
        latest_5m_ts = CASE
            WHEN EXCLUDED.latest_5m_ts IS NULL THEN symbol_availability.latest_5m_ts
            WHEN symbol_availability.latest_5m_ts IS NULL THEN EXCLUDED.latest_5m_ts
            ELSE GREATEST(symbol_availability.latest_5m_ts, EXCLUDED.latest_5m_ts)
        END,
        latest_10m_ts = CASE
            WHEN EXCLUDED.latest_10m_ts IS NULL THEN symbol_availability.latest_10m_ts
            WHEN symbol_availability.latest_10m_ts IS NULL THEN EXCLUDED.latest_10m_ts
            ELSE GREATEST(symbol_availability.latest_10m_ts, EXCLUDED.latest_10m_ts)
        END,
        latest_15m_ts = CASE
            WHEN EXCLUDED.latest_15m_ts IS NULL THEN symbol_availability.latest_15m_ts
            WHEN symbol_availability.latest_15m_ts IS NULL THEN EXCLUDED.latest_15m_ts
            ELSE GREATEST(symbol_availability.latest_15m_ts, EXCLUDED.latest_15m_ts)
        END,
        latest_30m_ts = CASE
            WHEN EXCLUDED.latest_30m_ts IS NULL THEN symbol_availability.latest_30m_ts
            WHEN symbol_availability.latest_30m_ts IS NULL THEN EXCLUDED.latest_30m_ts
            ELSE GREATEST(symbol_availability.latest_30m_ts, EXCLUDED.latest_30m_ts)
        END,
        latest_day_ts = CASE
            WHEN EXCLUDED.latest_day_ts IS NULL THEN symbol_availability.latest_day_ts
            WHEN symbol_availability.latest_day_ts IS NULL THEN EXCLUDED.latest_day_ts
            ELSE GREATEST(symbol_availability.latest_day_ts, EXCLUDED.latest_day_ts)
        END,
        latest_week_ts = CASE
            WHEN EXCLUDED.latest_week_ts IS NULL THEN symbol_availability.latest_week_ts
            WHEN symbol_availability.latest_week_ts IS NULL THEN EXCLUDED.latest_week_ts
            ELSE GREATEST(symbol_availability.latest_week_ts, EXCLUDED.latest_week_ts)
        END,
        updated_at = now();

    RETURN NEW;
END;
$$;


DROP TRIGGER IF EXISTS trg_sync_symbol_availability_from_price_candles ON public.price_candles;

CREATE TRIGGER trg_sync_symbol_availability_from_price_candles
AFTER INSERT OR UPDATE OF ts, interval, symbol
ON public.price_candles
FOR EACH ROW
EXECUTE FUNCTION public.sync_symbol_availability_from_price_candles();


INSERT INTO public.symbol_availability (
    symbol,
    latest_1m_ts,
    latest_5m_ts,
    latest_10m_ts,
    latest_15m_ts,
    latest_30m_ts,
    latest_day_ts,
    latest_week_ts,
    updated_at
)
SELECT
    pc.symbol,
    MAX(pc.ts) FILTER (WHERE pc.interval IN ('1m', '1min')) AS latest_1m_ts,
    MAX(pc.ts) FILTER (WHERE pc.interval IN ('5m', '5min')) AS latest_5m_ts,
    MAX(pc.ts) FILTER (WHERE pc.interval IN ('10m', '10min')) AS latest_10m_ts,
    MAX(pc.ts) FILTER (WHERE pc.interval IN ('15m', '15min')) AS latest_15m_ts,
    MAX(pc.ts) FILTER (WHERE pc.interval IN ('30m', '30min')) AS latest_30m_ts,
    MAX(pc.ts) FILTER (WHERE pc.interval IN ('1d', 'day', 'daily')) AS latest_day_ts,
    MAX(pc.ts) FILTER (WHERE pc.interval IN ('1w', 'week', 'weekly')) AS latest_week_ts,
    now() AS updated_at
FROM public.price_candles AS pc
GROUP BY pc.symbol
ON CONFLICT (symbol) DO UPDATE
SET latest_1m_ts = EXCLUDED.latest_1m_ts,
    latest_5m_ts = EXCLUDED.latest_5m_ts,
    latest_10m_ts = EXCLUDED.latest_10m_ts,
    latest_15m_ts = EXCLUDED.latest_15m_ts,
    latest_30m_ts = EXCLUDED.latest_30m_ts,
    latest_day_ts = EXCLUDED.latest_day_ts,
    latest_week_ts = EXCLUDED.latest_week_ts,
    updated_at = now();