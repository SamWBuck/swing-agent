# Live Readiness Summary

Date: 2026-03-10

## What changed

### Automation safety and execution policy

- Added explicit account selection gating for non-dry-run execution in `schwab_automation.sync`.
- Added live guardrails for minimum cash floor, per-run entry cap, per-symbol contract cap, and maximum position size as a percentage of liquidation value.
- Added kill-switch support and a loop-mode circuit breaker for repeated failures.
- Added live revalidation immediately before order submission.

### Automation structure and maintainability

- Split the automation worker into explicit reconcile, discovery, analysis, execution, and reporting phases.
- Added typed run-state dataclasses to make control flow easier to trace.
- Introduced a typed `MatchedPosition` contract for execution-safe position handling.
- Moved shared env/path helpers into `database/src/swing_agent_database/env_helpers.py` and reused them across services.

### Market context improvements

- Expanded the price-data MCP with `atr_pct`, `hv_20`, and `return_20d`.
- Updated prompt guidance to evaluate 10-45 DTE holds using trend, volatility, RSI regime, ADX, volume, and support/resistance context.

### Discord cleanup and output

- Removed the obsolete portfolio UI and portfolio command flow from `discord_bot.py`.
- Stopped injecting saved portfolio DB context into Discord recommendation requests.
- Extracted Discord session management into `discord_sessions.py`.
- Extracted Discord response formatting into `discord_response.py`.
- Switched both Discord bot replies and automation notifications to embed-based delivery.

### Testing and verification coverage

- Added `services/schwab-automation/tests/test_live_guardrails.py` for account selection and live risk-rule coverage.
- Updated existing automation tests for the new validation inputs and settings fields.
- Fixed a startup regression in `schwab_automation.config` by restoring the missing `Path` import used by launch/dry-run startup.

## Files added

- `database/src/swing_agent_database/env_helpers.py`
- `discord_response.py`
- `discord_sessions.py`
- `services/schwab-automation/tests/test_live_guardrails.py`

## Verification completed

- `python -m unittest discover -s services/schwab-automation/tests -p "test_*.py"`
- `python -m schwab_automation.main --dry-run --log-level INFO`
- VS Code dry-run launch path reproduced and fixed after the startup regression was identified.

## Remaining caution before live enablement

- Dry-run behavior and startup are verified, but no live order placement was executed in this session.
- Keep `AUTOMATION_EXECUTION_ENABLED=false` until you have reviewed the generated trade intents and Discord embeds in your own environment.
- Confirm `.env` contains an explicit target account via `SCHWAB_ACCOUNT_HASH` or `SCHWAB_ACCOUNT_NUMBER` before the first live run.
- Make sure Docker-hosted MCP services are reachable on `localhost` from the host automation worker.