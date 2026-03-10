# schwab-automation

Dedicated hourly worker for live Schwab account reconciliation and, in later iterations, automated options trade management.

## Current scope

- Runs as a separate service from the Discord bot
- Pulls the linked Schwab account using the token configured in `.env`
- Resolves the target account by `SCHWAB_ACCOUNT_HASH`, `SCHWAB_ACCOUNT_NUMBER`, or the first linked account
- Persists the latest broker account snapshot into `broker_accounts`
- Replaces the active broker position set in `broker_positions`
- Records an `automation_runs` row and a `reconcile` decision log for each cycle
- Optionally posts a short run summary to Discord through the bot token plus `DISCORD_AUTOMATION_CHANNEL_ID` / `DISCORD_INCOME_AGENT_CHANNEL_ID`, or through `DISCORD_AUTOMATION_WEBHOOK_URL`

This first cut is reconcile-only. It does not place, cancel, or roll orders yet.

## Required database migration

Apply [database/migrations/20260310_add_automation_tables.sql](../../database/migrations/20260310_add_automation_tables.sql) before running the service against a database.

## Environment

Required:

- `SCHWAB_API_KEY`
- `SCHWAB_APP_SECRET`
- `SCHWAB_CALLBACK_URL`
- Database connection via `DATABASE_URL` or `DB_USER` / `DB_PASSWORD` / `DB_HOST` / `DB_PORT` / `DB_NAME`

Optional:

- `SCHWAB_TOKEN_PATH`
- `SCHWAB_ACCOUNT_NUMBER`
- `SCHWAB_ACCOUNT_HASH`
- `DISCORD_AUTOMATION_CHANNEL_ID`
- `DISCORD_AUTOMATION_WEBHOOK_URL`
- `AUTOMATION_SERVICE_NAME`
- `AUTOMATION_RUN_TYPE`
- `AUTOMATION_PROMPT_VERSION`
- `AUTOMATION_DRY_RUN`
- `AUTOMATION_WEBHOOK_TIMEOUT_SECONDS`
- `SCHWAB_REQUEST_TIMEOUT_SECONDS`

## Local run

```powershell
C:/dev/swing-agent/.venv/Scripts/python.exe -m pip install -e ./database -e ./services/schwab-automation
C:/dev/swing-agent/.venv/Scripts/python.exe -m schwab_automation.main --dry-run
```

## Docker run model

- Docker service name: `schwab-automation`
- Startup behavior: execute one immediate cycle
- Recurrence: hourly via cron at minute `5`
- Log output: stdout/stderr from the container

## Next implementation steps

1. Persist broker orders and fills alongside positions.
2. Add policy assembly and decision logging for open/close/roll candidates.
3. Add execution guardrails before any live order placement.
4. Add order construction and reconciliation for single-leg options first.