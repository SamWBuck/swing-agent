#!/bin/sh
set -eu

. /app/.cron_env

LOCK_DIR=/tmp/schwab-price-sync.lock
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "$(date -Iseconds) [INFO] minute missing-only sync skipped because another sync is already running" >> /proc/1/fd/1
    exit 0
fi

trap 'rmdir "$LOCK_DIR"' EXIT INT TERM

cd /app
echo "$(date -Iseconds) [INFO] starting minute missing-only sync across all intervals" >> /proc/1/fd/1
python -m schwab_price_sync.main \
    --missing-only \
    --log-level INFO >> /proc/1/fd/1 2>> /proc/1/fd/2
echo "$(date -Iseconds) [INFO] finished minute missing-only sync across all intervals" >> /proc/1/fd/1