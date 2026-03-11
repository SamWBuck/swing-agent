# schwab-automation

Dedicated hourly worker for live Schwab account reconciliation and, in later iterations, automated options trade management.

## Current scope

- Runs as a separate service from the Discord bot
- Pulls the linked Schwab account using the token configured in `.env`
- Resolves the target account by `SCHWAB_ACCOUNT_HASH`, `SCHWAB_ACCOUNT_NUMBER`, or the first linked account
- Persists the latest broker account snapshot into `broker_accounts`
- Replaces the active broker position set in `broker_positions`
- Records an `automation_runs` row plus shadow-mode decision logs for capital, covered-call eligibility, PMCC eligibility, and new-position scan state
- Runs a structured LLM analysis step using the existing MCP tool stack and persists normalized action intents to `automation_action_intents`
- Optionally posts a short run summary to Discord through the bot token plus `DISCORD_AUTOMATION_CHANNEL_ID` / `DISCORD_INCOME_AGENT_CHANNEL_ID`, or through `DISCORD_AUTOMATION_WEBHOOK_URL`

This service now supports guarded execution paths, but the default operating mode should still be shadow mode until you explicitly enable live execution in `.env`.

Practical runtime note:

- Host execution is the authoritative mode for Copilot-backed structured analysis.
- The automation worker should be run locally on the host, not in Docker.
- Docker remains useful for the MCP services the host worker calls over `localhost`.
- Use `--loop` on the host if you want the LLM-driven automation runner to stay active continuously.

## Required database migration

Apply [database/migrations/20260310_add_automation_tables.sql](../../database/migrations/20260310_add_automation_tables.sql) before running the service against a database.

If you still use any local portfolio-tracking tables from earlier Discord workflows, they are no longer required for the current live-Schwab automation path.

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
- `AUTOMATION_KILL_SWITCH`
- `AUTOMATION_WEBHOOK_TIMEOUT_SECONDS`
- `SCHWAB_REQUEST_TIMEOUT_SECONDS`
- `AUTOMATION_MIN_CSP_RESERVE`
- `AUTOMATION_MIN_ENTRY_CASH`
- `AUTOMATION_MIN_ACCOUNT_CASH_FLOOR`
- `AUTOMATION_MAX_POSITION_PCT_OF_PORTFOLIO`
- `AUTOMATION_MAX_NEW_ENTRIES_PER_RUN`
- `AUTOMATION_MAX_CONTRACTS_PER_SYMBOL`
- `AUTOMATION_REQUIRE_EXPLICIT_ACCOUNT`
- `AUTOMATION_MAX_CONSECUTIVE_FAILURES`
- `AUTOMATION_ROLL_DTE_THRESHOLD_DAYS`
- `AUTOMATION_CLOSE_DTE_THRESHOLD_DAYS`
- `AUTOMATION_ANALYSIS_ENABLED`
- `AUTOMATION_EXECUTION_ENABLED`
- `AUTOMATION_ENABLE_NEW_ENTRIES`
- `AUTOMATION_ENABLE_MANAGEMENT`
- `SEC_EDGAR_MCP_URL`
- `YAHOO_FINANCE_MCP_URL`
- `PRICE_DATA_MCP_URL`
- `AUTOMATION_PROMPT_PATH`
- `AUTOMATION_ENTRY_CANDIDATE_PROMPT_PATH`
- `AUTOMATION_ENTRY_CHAIN_MIN_DTE`
- `AUTOMATION_ENTRY_CHAIN_MAX_DTE`
- `AUTOMATION_ENTRY_CHAIN_STRIKE_COUNT`
- `AUTOMATION_ENTRY_CHAIN_CONTRACT_LIMIT`

Recommended host values when the MCP services are running from `docker/docker-compose.yml`:

- `SEC_EDGAR_MCP_URL=http://localhost:9870/mcp`
- `YAHOO_FINANCE_MCP_URL=http://localhost:8809/mcp`
- `PRICE_DATA_MCP_URL=http://localhost:8810/mcp`

Live execution safety notes:

- Outside dry run, the worker now fails closed unless `SCHWAB_ACCOUNT_HASH` or `SCHWAB_ACCOUNT_NUMBER` is set explicitly.
- `AUTOMATION_KILL_SWITCH=true` leaves analysis/reporting on but blocks live order placement.
- New-entry validation now enforces a minimum remaining cash floor plus configurable per-run and per-symbol caps before any order is submitted.
- New CSP entries and replacement short puts are rejected when their reserved notional would exceed `AUTOMATION_MAX_POSITION_PCT_OF_PORTFOLIO` of portfolio liquidation value.
- In loop mode, repeated run failures trip a simple circuit breaker after `AUTOMATION_MAX_CONSECUTIVE_FAILURES` failures and stop the process.

## Local run

```powershell
docker compose -f docker/docker-compose.yml up -d --build edgar-mcp yahoo-finance-mcp price-data-mcp schwab-price-sync
C:/dev/swing-agent/.venv/Scripts/python.exe -m pip install -e ./database -e ./services/schwab-automation
C:/dev/swing-agent/.venv/Scripts/python.exe -m schwab_price_sync.load_token --symbol SPY
C:/dev/swing-agent/.venv/Scripts/python.exe -m schwab_automation.main --dry-run
C:/dev/swing-agent/.venv/Scripts/python.exe -m schwab_automation.main --loop --interval-seconds 3600 --dry-run
```

If you launch from a shell rather than VS Code, set the source-package path first:

```powershell
$env:PYTHONPATH='C:/dev/swing-agent/services/schwab-automation/src;C:/dev/swing-agent/database/src'
```

## VS Code run targets

The checked-in launch configs are the preferred local entry points:

- `Schwab Automation Dry Run`
- `Schwab Automation Dry Run Loop`

These launch configs already set the local package `PYTHONPATH` and dry-run environment overrides.

## Verification commands

Run the current automation tests:

```powershell
C:/dev/swing-agent/.venv/Scripts/python.exe -m unittest discover -s services/schwab-automation/tests -p "test_*.py"
```

Verify a one-shot dry-run manually:

```powershell
$env:PYTHONPATH='C:/dev/swing-agent/services/schwab-automation/src;C:/dev/swing-agent/database/src'
$env:AUTOMATION_DRY_RUN='true'
$env:AUTOMATION_ANALYSIS_ENABLED='true'
$env:AUTOMATION_EXECUTION_ENABLED='false'
$env:AUTOMATION_ENABLE_NEW_ENTRIES='true'
$env:AUTOMATION_ENABLE_MANAGEMENT='true'
$env:AUTOMATION_ENFORCE_TRADING_WINDOW='true'
C:/dev/swing-agent/.venv/Scripts/python.exe -m schwab_automation.main --dry-run --log-level INFO
```

## Live run checklist

1. Start MCP/support services with Docker and confirm they are reachable on `localhost`.
2. Refresh the Schwab token with `schwab_price_sync.load_token` if needed.
3. Apply required database migrations.
4. Set `SCHWAB_ACCOUNT_HASH` or `SCHWAB_ACCOUNT_NUMBER` explicitly.
5. Keep `AUTOMATION_KILL_SWITCH=true` for the first end-to-end live connectivity checks if you want reporting without order submission.
6. Turn on `AUTOMATION_EXECUTION_ENABLED=true` only after reviewing dry-run behavior, trade intents, and Discord notifications.

Example first live shell run:

```powershell
$env:PYTHONPATH='C:/dev/swing-agent/services/schwab-automation/src;C:/dev/swing-agent/database/src'
$env:AUTOMATION_DRY_RUN='false'
$env:AUTOMATION_ANALYSIS_ENABLED='true'
$env:AUTOMATION_EXECUTION_ENABLED='false'
$env:AUTOMATION_KILL_SWITCH='true'
C:/dev/swing-agent/.venv/Scripts/python.exe -m schwab_automation.main --log-level INFO
```

## Docker model

- Run only the MCP services and any supporting sync services in Docker.
- Run `schwab_automation.main` locally from the host or from VS Code launch configs.
- The local worker reaches the Docker-hosted MCP endpoints through `localhost`.

## Next implementation steps

1. Persist broker orders and fills alongside positions.
2. Add policy assembly and deterministic open/close/roll candidate generation.
3. Add execution guardrails before any live order placement.
4. Add order construction and reconciliation for single-leg options first.