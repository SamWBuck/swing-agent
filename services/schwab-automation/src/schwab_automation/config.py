from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta
from importlib.resources import files
from pathlib import Path

from swing_agent_database import (
    AutomationStoreSettings,
    SymbolAvailabilitySettings,
    env_bool,
    env_int,
    find_project_root,
    load_automation_store_settings,
    load_symbol_availability_settings,
    optional_env,
    required_env,
    resolve_path,
)


@dataclass(frozen=True)
class Settings:
    automation_store: AutomationStoreSettings
    symbol_availability_store: SymbolAvailabilitySettings
    schwab_api_key: str
    schwab_app_secret: str
    schwab_callback_url: str
    schwab_token_path: Path
    preferred_account_number: str | None
    preferred_account_hash: str | None
    discord_bot_token: str | None
    discord_channel_id: int | None
    discord_webhook_url: str | None
    service_name: str
    run_type: str
    prompt_version: str
    dry_run: bool
    request_timeout_seconds: int
    webhook_timeout_seconds: int
    interactive_login: bool
    min_csp_reserve: int
    min_entry_cash: int
    min_account_cash_floor: int
    max_position_pct_of_portfolio: int
    max_new_entries_per_run: int
    max_contracts_per_symbol: int
    roll_dte_threshold_days: int
    close_dte_threshold_days: int
    analysis_enabled: bool
    analysis_timeout_seconds: int
    execution_enabled: bool
    kill_switch_enabled: bool
    enable_new_entries: bool
    enable_management: bool
    require_explicit_account: bool
    max_consecutive_failures: int
    enforce_trading_window: bool
    trading_timezone: str
    trading_start_hour: int
    trading_start_minute: int
    trading_end_hour: int
    trading_end_minute: int
    entry_candidate_prompt_path: Path
    entry_chain_min_dte: int
    entry_chain_max_dte: int
    entry_chain_strike_count: int
    entry_chain_contract_limit: int
    sec_edgar_mcp_url: str
    yahoo_finance_mcp_url: str
    price_data_mcp_url: str
    automation_prompt_path: Path


def load_settings() -> Settings:
    workspace_root = find_project_root()
    token_path = resolve_path(
        os.getenv("SCHWAB_TOKEN_PATH", "token.json"),
        base_dir=workspace_root,
    )
    preferred_account_number = optional_env("SCHWAB_ACCOUNT_NUMBER")
    preferred_account_hash = optional_env("SCHWAB_ACCOUNT_HASH")
    if preferred_account_number and preferred_account_hash:
        raise RuntimeError("Set only one of SCHWAB_ACCOUNT_NUMBER or SCHWAB_ACCOUNT_HASH")
    return Settings(
        automation_store=load_automation_store_settings(consumer_name="schwab-automation"),
        symbol_availability_store=load_symbol_availability_settings(consumer_name="schwab-automation"),
        schwab_api_key=required_env("SCHWAB_API_KEY"),
        schwab_app_secret=required_env("SCHWAB_APP_SECRET"),
        schwab_callback_url=required_env("SCHWAB_CALLBACK_URL"),
        schwab_token_path=token_path,
        preferred_account_number=preferred_account_number,
        preferred_account_hash=preferred_account_hash,
        discord_bot_token=optional_env("DISCORD_BOT_TOKEN"),
        discord_channel_id=env_int("DISCORD_AUTOMATION_CHANNEL_ID", 0)
        or env_int("DISCORD_INCOME_AGENT_CHANNEL_ID", 0)
        or None,
        discord_webhook_url=optional_env("DISCORD_AUTOMATION_WEBHOOK_URL"),
        service_name=os.getenv("AUTOMATION_SERVICE_NAME", "schwab-automation"),
        run_type=os.getenv("AUTOMATION_RUN_TYPE", "hourly"),
        prompt_version=os.getenv("AUTOMATION_PROMPT_VERSION", "reconcile-v1"),
        dry_run=env_bool("AUTOMATION_DRY_RUN", True),
        request_timeout_seconds=env_int("SCHWAB_REQUEST_TIMEOUT_SECONDS", 60),
        webhook_timeout_seconds=env_int("AUTOMATION_WEBHOOK_TIMEOUT_SECONDS", 15),
        interactive_login=env_bool("SCHWAB_INTERACTIVE_LOGIN", True),
        min_csp_reserve=env_int("AUTOMATION_MIN_CSP_RESERVE", 2500),
        min_entry_cash=env_int("AUTOMATION_MIN_ENTRY_CASH", 1000),
        min_account_cash_floor=env_int("AUTOMATION_MIN_ACCOUNT_CASH_FLOOR", 1000),
        max_position_pct_of_portfolio=env_int("AUTOMATION_MAX_POSITION_PCT_OF_PORTFOLIO", 50),
        max_new_entries_per_run=env_int("AUTOMATION_MAX_NEW_ENTRIES_PER_RUN", 1),
        max_contracts_per_symbol=env_int("AUTOMATION_MAX_CONTRACTS_PER_SYMBOL", 1),
        roll_dte_threshold_days=env_int("AUTOMATION_ROLL_DTE_THRESHOLD_DAYS", 7),
        close_dte_threshold_days=env_int("AUTOMATION_CLOSE_DTE_THRESHOLD_DAYS", 1),
        analysis_enabled=env_bool("AUTOMATION_ANALYSIS_ENABLED", True),
        analysis_timeout_seconds=env_int("AUTOMATION_ANALYSIS_TIMEOUT_SECONDS", 120),
        execution_enabled=env_bool("AUTOMATION_EXECUTION_ENABLED", False),
        kill_switch_enabled=env_bool("AUTOMATION_KILL_SWITCH", False),
        enable_new_entries=env_bool("AUTOMATION_ENABLE_NEW_ENTRIES", False),
        enable_management=env_bool("AUTOMATION_ENABLE_MANAGEMENT", True),
        require_explicit_account=env_bool("AUTOMATION_REQUIRE_EXPLICIT_ACCOUNT", True),
        max_consecutive_failures=env_int("AUTOMATION_MAX_CONSECUTIVE_FAILURES", 3),
        enforce_trading_window=env_bool("AUTOMATION_ENFORCE_TRADING_WINDOW", True),
        trading_timezone=os.getenv("AUTOMATION_TRADING_TIMEZONE", "America/New_York"),
        trading_start_hour=env_int("AUTOMATION_TRADING_START_HOUR", 10),
        trading_start_minute=env_int("AUTOMATION_TRADING_START_MINUTE", 0),
        trading_end_hour=env_int("AUTOMATION_TRADING_END_HOUR", 15),
        trading_end_minute=env_int("AUTOMATION_TRADING_END_MINUTE", 0),
        entry_candidate_prompt_path=Path(
            os.getenv(
                "AUTOMATION_ENTRY_CANDIDATE_PROMPT_PATH",
                str(files("schwab_automation").joinpath("prompts/options-income-candidate.prompt.md")),
            )
        ),
        entry_chain_min_dte=env_int("AUTOMATION_ENTRY_CHAIN_MIN_DTE", 7),
        entry_chain_max_dte=env_int("AUTOMATION_ENTRY_CHAIN_MAX_DTE", 45),
        entry_chain_strike_count=env_int("AUTOMATION_ENTRY_CHAIN_STRIKE_COUNT", 12),
        entry_chain_contract_limit=env_int("AUTOMATION_ENTRY_CHAIN_CONTRACT_LIMIT", 12),
        sec_edgar_mcp_url=os.getenv("SEC_EDGAR_MCP_URL", "http://localhost:9870/mcp"),
        yahoo_finance_mcp_url=os.getenv("YAHOO_FINANCE_MCP_URL", "http://localhost:8809/mcp"),
        price_data_mcp_url=os.getenv("PRICE_DATA_MCP_URL", "http://localhost:8810/mcp"),
        automation_prompt_path=Path(
            os.getenv(
                "AUTOMATION_PROMPT_PATH",
                str(files("schwab_automation").joinpath("prompts/options-income-automation.prompt.md")),
            )
        ),
    )