DROP TRIGGER IF EXISTS trg_sync_symbol_availability_from_price_candles ON public.price_candles;

ALTER TABLE IF EXISTS public.price_candles
    ALTER COLUMN symbol TYPE character varying(16);

ALTER TABLE IF EXISTS public.symbol_availability
    ALTER COLUMN symbol TYPE character varying(16);

CREATE TRIGGER trg_sync_symbol_availability_from_price_candles
AFTER INSERT OR UPDATE OF ts, interval, symbol
ON public.price_candles
FOR EACH ROW
EXECUTE FUNCTION public.sync_symbol_availability_from_price_candles();