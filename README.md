# swing-agent

Python monorepo for a Schwab-backed options automation worker, Discord chat interface, shared database access, and MCP services used for market context.

## Workspace layout

- `discord_bot.py`: Discord chat entry point.
- `database/`: shared database package, migrations, and store/config helpers.
- `services/schwab-automation/`: host-run automation worker for reconciliation, analysis, and guarded execution.
- `services/schwab-price-sync/`: Schwab candle sync service.
- `mcp-services/`: supporting MCP servers used by the automation prompts.
- `docker/docker-compose.yml`: local MCP/support service stack.

## Prerequisites

- Python 3.11+
- Local virtual environment at `.venv`
- Valid `.env` with Schwab API credentials and database settings
- Docker Desktop or compatible Docker engine for MCP services
- Database migrations applied from `database/migrations/`

## Install

```powershell
C:/dev/swing-agent/.venv/Scripts/python.exe -m pip install -r requirements.txt
```

If you want focused editable installs only:

```powershell
C:/dev/swing-agent/.venv/Scripts/python.exe -m pip install -e ./database -e ./services/schwab-automation -e ./services/schwab-price-sync
```

## Start supporting services

Run MCP and support services from Docker. The automation worker itself stays on the host.

```powershell
docker compose -f docker/docker-compose.yml up -d --build
```

## Common runs

Load or refresh the Schwab token:

```powershell
C:/dev/swing-agent/.venv/Scripts/python.exe -m schwab_price_sync.load_token --symbol SPY
```

Run price sync:

```powershell
C:/dev/swing-agent/.venv/Scripts/python.exe -m schwab_price_sync.main
```

Run automation dry-run once:

```powershell
$env:PYTHONPATH='C:/dev/swing-agent/services/schwab-automation/src;C:/dev/swing-agent/database/src'
C:/dev/swing-agent/.venv/Scripts/python.exe -m schwab_automation.main --dry-run --log-level INFO
```

Run automation dry-run loop:

```powershell
$env:PYTHONPATH='C:/dev/swing-agent/services/schwab-automation/src;C:/dev/swing-agent/database/src'
C:/dev/swing-agent/.venv/Scripts/python.exe -m schwab_automation.main --dry-run --loop --interval-seconds 3600 --log-level INFO
```

Run Discord bot:

```powershell
C:/dev/swing-agent/.venv/Scripts/python.exe discord_bot.py
```

## VS Code launch configs

Available launch configs in `.vscode/launch.json`:

- `Discord Bot`
- `Schwab Load Token`
- `Schwab Price Sync`
- `Schwab Automation Dry Run`
- `Schwab Automation Dry Run Loop`

## Live automation notes

- Run `schwab_automation.main` on the host, not in Docker.
- Keep Docker for MCP/support services only.
- Set `SCHWAB_ACCOUNT_HASH` or `SCHWAB_ACCOUNT_NUMBER` before any non-dry-run execution.
- Leave `AUTOMATION_EXECUTION_ENABLED=false` until you have validated dry-run behavior and Discord output in your environment.
- `AUTOMATION_KILL_SWITCH=true` preserves reporting while blocking live order placement.

See [services/schwab-automation/README.md](services/schwab-automation/README.md) for service-specific configuration and run details, and [docs/2026-03-10-live-readiness-summary.md](docs/2026-03-10-live-readiness-summary.md) for the current change summary.