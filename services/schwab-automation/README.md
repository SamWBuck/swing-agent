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

This stage is still shadow mode. It does not place, cancel, or roll orders yet, but it now classifies positions and reports what the bot would do.

Practical runtime note:

- Host execution is the authoritative mode for Copilot-backed structured analysis.
- The automation worker should be run locally on the host, not in Docker.
- Docker remains useful for the MCP services the host worker calls over `localhost`.
- Use `--loop` on the host if you want the LLM-driven automation runner to stay active continuously.

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
- `AUTOMATION_MIN_CSP_RESERVE`
- `AUTOMATION_MIN_ENTRY_CASH`
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

Recommended host values when the MCP services are running from `docker/docker-compose.yml`:

- `SEC_EDGAR_MCP_URL=http://localhost:9870/mcp`
- `YAHOO_FINANCE_MCP_URL=http://localhost:8809/mcp`
- `PRICE_DATA_MCP_URL=http://localhost:8810/mcp`

## Local run

```powershell
docker compose -f docker/docker-compose.yml up -d --build edgar-mcp yahoo-finance-mcp price-data-mcp schwab-price-sync
C:/dev/swing-agent/.venv/Scripts/python.exe -m pip install -e ./database -e ./services/schwab-automation
C:/dev/swing-agent/.venv/Scripts/python.exe -m schwab_automation.main --dry-run
C:/dev/swing-agent/.venv/Scripts/python.exe -m schwab_automation.main --loop --interval-seconds 3600 --dry-run
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