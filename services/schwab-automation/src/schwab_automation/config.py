from __future__ import annotations

import os
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from swing_agent_database import (
    env_bool,
    env_int,
    find_project_root,
    optional_env,
    required_env,
    resolve_path,
)


@dataclass(frozen=True)
class Settings:
    schwab_api_key: str
    schwab_app_secret: str
    schwab_callback_url: str
    schwab_token_path: Path
    preferred_account_number: str | None
    preferred_account_hash: str | None
    require_explicit_account: bool
    discord_bot_token: str | None
    discord_channel_id: int | None
    discord_webhook_url: str | None
    service_name: str
    run_type: str
    dry_run: bool
    request_timeout_seconds: int
    webhook_timeout_seconds: int
    interactive_login: bool
    analysis_timeout_seconds: int
    execution_enabled: bool
    max_consecutive_failures: int
    enforce_trading_window: bool
    trading_timezone: str
    trading_start_hour: int
    trading_start_minute: int
    trading_end_hour: int
    trading_end_minute: int
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
        schwab_api_key=required_env("SCHWAB_API_KEY"),
        schwab_app_secret=required_env("SCHWAB_APP_SECRET"),
        schwab_callback_url=required_env("SCHWAB_CALLBACK_URL"),
        schwab_token_path=token_path,
        preferred_account_number=preferred_account_number,
        preferred_account_hash=preferred_account_hash,
        require_explicit_account=env_bool("AUTOMATION_REQUIRE_EXPLICIT_ACCOUNT", True),
        discord_bot_token=optional_env("DISCORD_BOT_TOKEN"),
        discord_channel_id=env_int("DISCORD_AUTOMATION_CHANNEL_ID", 0)
        or env_int("DISCORD_INCOME_AGENT_CHANNEL_ID", 0)
        or None,
        discord_webhook_url=optional_env("DISCORD_AUTOMATION_WEBHOOK_URL"),
        service_name=os.getenv("AUTOMATION_SERVICE_NAME", "schwab-automation"),
        run_type=os.getenv("AUTOMATION_RUN_TYPE", "hourly"),
        dry_run=env_bool("AUTOMATION_DRY_RUN", True),
        request_timeout_seconds=env_int("SCHWAB_REQUEST_TIMEOUT_SECONDS", 60),
        webhook_timeout_seconds=env_int("AUTOMATION_WEBHOOK_TIMEOUT_SECONDS", 15),
        interactive_login=env_bool("SCHWAB_INTERACTIVE_LOGIN", True),
        analysis_timeout_seconds=env_int("AUTOMATION_ANALYSIS_TIMEOUT_SECONDS", 120),
        execution_enabled=env_bool("AUTOMATION_EXECUTION_ENABLED", False),
        max_consecutive_failures=env_int("AUTOMATION_MAX_CONSECUTIVE_FAILURES", 3),
        enforce_trading_window=env_bool("AUTOMATION_ENFORCE_TRADING_WINDOW", True),
        trading_timezone=os.getenv("AUTOMATION_TRADING_TIMEZONE", "America/New_York"),
        trading_start_hour=env_int("AUTOMATION_TRADING_START_HOUR", 10),
        trading_start_minute=env_int("AUTOMATION_TRADING_START_MINUTE", 0),
        trading_end_hour=env_int("AUTOMATION_TRADING_END_HOUR", 15),
        trading_end_minute=env_int("AUTOMATION_TRADING_END_MINUTE", 0),
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