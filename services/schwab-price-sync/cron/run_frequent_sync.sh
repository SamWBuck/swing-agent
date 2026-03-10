#!/bin/sh
set -eu

. /app/.cron_env

LOCK_DIR=/tmp/schwab-price-sync.lock
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "$(date -Iseconds) [INFO] 30-minute 1m/5m sync skipped because another sync is already running" >> /proc/1/fd/1
    exit 0
fi

trap 'rmdir "$LOCK_DIR"' EXIT INT TERM

cd /app
echo "$(date -Iseconds) [INFO] starting 30-minute stale-only sync for 1m and 5m" >> /proc/1/fd/1
python -m schwab_price_sync.main \
    --interval 1m \
    --interval 5m \
    --stale-only \
    --log-level INFO >> /proc/1/fd/1 2>> /proc/1/fd/2
echo "$(date -Iseconds) [INFO] finished 30-minute stale-only sync for 1m and 5m" >> /proc/1/fd/1