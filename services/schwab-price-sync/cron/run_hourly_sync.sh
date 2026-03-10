#!/bin/sh
set -eu

. /app/.cron_env

LOCK_DIR=/tmp/schwab-price-sync.lock
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "$(date -Iseconds) [INFO] Hourly sync skipped because another sync is already running" >> /proc/1/fd/1
    exit 0
fi

trap 'rmdir "$LOCK_DIR"' EXIT INT TERM

cd /app
echo "$(date -Iseconds) [INFO] starting hourly stale-only sync for 10m, 15m, 30m, 1d, and 1w" >> /proc/1/fd/1
python -m schwab_price_sync.main \
    --interval 10m \
    --interval 15m \
    --interval 30m \
    --interval 1d \
    --interval 1w \
    --stale-only \
    --log-level INFO >> /proc/1/fd/1 2>> /proc/1/fd/2
echo "$(date -Iseconds) [INFO] finished hourly stale-only sync for 10m, 15m, 30m, 1d, and 1w" >> /proc/1/fd/1