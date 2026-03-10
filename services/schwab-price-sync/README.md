# Schwab Price Sync

One-shot worker that reads symbols from `symbol_availability`, fetches price history from Schwab, and upserts candles into `price_candles`.

## Behavior

- Reads all symbols from `symbol_availability` by default.
- For `1m`, bootstraps 30 days of history in one-day windows when no latest timestamp exists.
- For `5m`, `10m`, `15m`, `30m`, `1d`, and `1w`, bootstraps six months of history when no latest timestamp exists.
- For existing symbols, fetches from `latest_*_ts - 1 day` through now.
- Upserts by `(symbol, interval, ts)` and only updates rows whose OHLCV values changed.
- `--stale-only` skips intervals whose latest timestamp is still within the expected freshness window.

## Run

Local:

```powershell
cd services/schwab-price-sync
python -m schwab_price_sync.main
```

Single symbol:

```powershell
python -m schwab_price_sync.main --symbol SPY
```

Only symbols with missing interval coverage:

```powershell
python -m schwab_price_sync.main --missing-only
```

Only sync intervals that are actually due for new data:

```powershell
python -m schwab_price_sync.main --interval 1m --interval 5m --stale-only
```

## Notes

- The worker loads the repo-root `.env` automatically when run from source.
- `SCHWAB_TOKEN_PATH` is resolved relative to the repo root when it is not absolute.
- The first run may require browser-based Schwab authentication if no token file exists yet.
- The Dockerfile expects to be built from the repository root so it can copy `token.json` into `/app/token.json` during build.
- The cron container runs three schedules: every minute for symbols with missing interval timestamps, every 30 minutes for stale `1m` and `5m` data, and every hour for stale `10m`, `15m`, `30m`, `1d`, and `1w` data.