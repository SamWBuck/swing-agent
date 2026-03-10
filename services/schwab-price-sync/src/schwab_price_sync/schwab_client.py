from __future__ import annotations

from typing import Any

from schwab.auth import client_from_token_file, easy_client

from .config import Settings


def create_async_client(settings: Settings) -> Any:
    if settings.schwab_token_path.exists():
        client = client_from_token_file(
            str(settings.schwab_token_path),
            settings.schwab_api_key,
            settings.schwab_app_secret,
            asyncio=True,
        )
    else:
        client = easy_client(
            api_key=settings.schwab_api_key,
            app_secret=settings.schwab_app_secret,
            callback_url=settings.schwab_callback_url,
            token_path=str(settings.schwab_token_path),
            asyncio=True,
            interactive=settings.interactive_login,
        )

    if hasattr(client, "set_timeout"):
        client.set_timeout(settings.request_timeout_seconds)
    return client