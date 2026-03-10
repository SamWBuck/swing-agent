from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from swing_agent_database import AutomationStoreSettings, load_automation_store_settings


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    return value


def _find_project_root() -> Path:
    candidates = [Path.cwd(), Path(__file__).resolve().parent]
    for candidate in candidates:
        for current in (candidate, *candidate.parents):
            if (current / ".env").exists() or (current / ".git").exists():
                return current
    return Path.cwd()


def _resolve_path(value: str, *, base_dir: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


@dataclass(frozen=True)
class Settings:
    automation_store: AutomationStoreSettings
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


def load_settings() -> Settings:
    workspace_root = _find_project_root()
    token_path = _resolve_path(
        os.getenv("SCHWAB_TOKEN_PATH", "token.json"),
        base_dir=workspace_root,
    )
    return Settings(
        automation_store=load_automation_store_settings(consumer_name="schwab-automation"),
        schwab_api_key=_required_env("SCHWAB_API_KEY"),
        schwab_app_secret=_required_env("SCHWAB_APP_SECRET"),
        schwab_callback_url=_required_env("SCHWAB_CALLBACK_URL"),
        schwab_token_path=token_path,
        preferred_account_number=_optional_env("SCHWAB_ACCOUNT_NUMBER"),
        preferred_account_hash=_optional_env("SCHWAB_ACCOUNT_HASH"),
        discord_bot_token=_optional_env("DISCORD_BOT_TOKEN"),
        discord_channel_id=_env_int("DISCORD_AUTOMATION_CHANNEL_ID", 0)
        or _env_int("DISCORD_INCOME_AGENT_CHANNEL_ID", 0)
        or None,
        discord_webhook_url=_optional_env("DISCORD_AUTOMATION_WEBHOOK_URL"),
        service_name=os.getenv("AUTOMATION_SERVICE_NAME", "schwab-automation"),
        run_type=os.getenv("AUTOMATION_RUN_TYPE", "hourly"),
        prompt_version=os.getenv("AUTOMATION_PROMPT_VERSION", "reconcile-v1"),
        dry_run=_env_bool("AUTOMATION_DRY_RUN", True),
        request_timeout_seconds=_env_int("SCHWAB_REQUEST_TIMEOUT_SECONDS", 60),
        webhook_timeout_seconds=_env_int("AUTOMATION_WEBHOOK_TIMEOUT_SECONDS", 15),
        interactive_login=_env_bool("SCHWAB_INTERACTIVE_LOGIN", True),
    )