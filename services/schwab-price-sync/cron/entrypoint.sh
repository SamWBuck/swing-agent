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
echo "$(date -Iseconds) [INFO] installed schedule: every minute missing-only sync; hourly full interval sync" >&1
echo "$(date -Iseconds) [INFO] token path inside container: ${SCHWAB_TOKEN_PATH:-token.json}" >&1

echo "$(date -Iseconds) [INFO] running startup missing-only sync" >&1
/app/cron/run_missing_sync.sh
echo "$(date -Iseconds) [INFO] startup missing-only sync finished" >&1

echo "$(date -Iseconds) [INFO] running startup hourly full sync" >&1
/app/cron/run_hourly_sync.sh
echo "$(date -Iseconds) [INFO] startup hourly full sync finished" >&1

exec "$@"