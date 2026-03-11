from __future__ import annotations

from datetime import UTC, datetime
import unittest

from schwab_automation.config import Settings
from schwab_automation.main import _get_trading_window_status


class TradingWindowTests(unittest.TestCase):
    def _settings(self, *, dry_run: bool = False, enforce_trading_window: bool = True) -> Settings:
        return Settings(
            automation_store=None,
            symbol_availability_store=None,
            schwab_api_key="key",
            schwab_app_secret="secret",
            schwab_callback_url="http://localhost",
            schwab_token_path=None,
            preferred_account_number=None,
            preferred_account_hash=None,
            discord_bot_token=None,
            discord_channel_id=None,
            discord_webhook_url=None,
            service_name="schwab-automation",
            run_type="hourly",
            prompt_version="test",
            dry_run=dry_run,
            request_timeout_seconds=60,
            webhook_timeout_seconds=15,
            interactive_login=False,
            min_csp_reserve=2500,
            min_entry_cash=1000,
            min_account_cash_floor=1000,
            max_position_pct_of_portfolio=50,
            max_new_entries_per_run=1,
            max_contracts_per_symbol=1,
            roll_dte_threshold_days=7,
            close_dte_threshold_days=1,
            analysis_enabled=True,
            analysis_timeout_seconds=120,
            execution_enabled=False,
            kill_switch_enabled=False,
            enable_new_entries=False,
            enable_management=True,
            require_explicit_account=True,
            max_consecutive_failures=3,
            enforce_trading_window=enforce_trading_window,
            trading_timezone="America/New_York",
            trading_start_hour=10,
            trading_start_minute=0,
            trading_end_hour=15,
            trading_end_minute=0,
            entry_candidate_prompt_path=None,
            sec_edgar_mcp_url="http://localhost:9870/mcp",
            yahoo_finance_mcp_url="http://localhost:8809/mcp",
            price_data_mcp_url="http://localhost:8810/mcp",
            automation_prompt_path=None,
            entry_chain_min_dte=7,
            entry_chain_max_dte=45,
            entry_chain_strike_count=12,
            entry_chain_contract_limit=12,
        )

    def test_window_open_during_allowed_hours(self) -> None:
        settings = self._settings()
        status = _get_trading_window_status(datetime(2026, 3, 10, 15, 0, tzinfo=UTC), settings)

        self.assertTrue(status.is_open)

    def test_window_closed_after_hours(self) -> None:
        settings = self._settings()
        status = _get_trading_window_status(datetime(2026, 3, 10, 20, 1, tzinfo=UTC), settings)

        self.assertFalse(status.is_open)
        self.assertIn("Outside trading window", status.reason)

    def test_window_closed_on_weekend(self) -> None:
        settings = self._settings()
        status = _get_trading_window_status(datetime(2026, 3, 14, 15, 0, tzinfo=UTC), settings)

        self.assertFalse(status.is_open)
        self.assertEqual(status.reason, "Weekend trading is not allowed")

    def test_window_can_be_disabled(self) -> None:
        settings = self._settings(enforce_trading_window=False)
        status = _get_trading_window_status(datetime(2026, 3, 14, 15, 0, tzinfo=UTC), settings)

        self.assertTrue(status.is_open)
        self.assertEqual(status.reason, "Trading window enforcement disabled")


if __name__ == "__main__":
    unittest.main()