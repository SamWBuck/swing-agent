#!/bin/sh
set -eu

python - <<'PY'
import os
import shlex
from pathlib import Path

lines = []
for key, value in os.environ.items():
    lines.append(f"export {key}={shlex.quote(value)}")

Path('/app/.cron_env').write_text("\n".join(sorted(lines)) + "\n", encoding='utf-8')
PY

echo "$(date -Iseconds) [INFO] schwab-price-sync cron container starting" >&1
echo "$(date -Iseconds) [INFO] installed schedule: every 30 minutes for 1m/5m; hourly for 10m/15m/30m/1d/1w" >&1
echo "$(date -Iseconds) [INFO] token path inside container: ${SCHWAB_TOKEN_PATH:-token.json}" >&1

echo "$(date -Iseconds) [INFO] running startup 30-minute sync" >&1
/app/cron/run_missing_sync.sh
echo "$(date -Iseconds) [INFO] startup 30-minute sync finished" >&1

echo "$(date -Iseconds) [INFO] running startup hourly sync" >&1
/app/cron/run_hourly_sync.sh
echo "$(date -Iseconds) [INFO] startup hourly sync finished" >&1

exec "$@"