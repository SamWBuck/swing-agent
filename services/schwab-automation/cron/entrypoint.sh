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

echo "$(date -Iseconds) [INFO] schwab-automation cron container starting" >&1
echo "$(date -Iseconds) [INFO] installed schedule: hourly reconcile and automation run" >&1
echo "$(date -Iseconds) [INFO] token path inside container: ${SCHWAB_TOKEN_PATH:-token.json}" >&1

echo "$(date -Iseconds) [INFO] running startup automation cycle" >&1
/app/cron/run_hourly.sh
echo "$(date -Iseconds) [INFO] startup automation cycle finished" >&1

exec "$@"