from __future__ import annotations

from datetime import UTC, datetime, date
from decimal import Decimal
from pathlib import Path
import unittest

from schwab_automation.analysis import PortfolioAnalysis, ShadowDecision, SymbolAnalysis
from schwab_automation.config import Settings
from schwab_automation.llm_actions import ProposedAction, validate_actions
from schwab_automation.sync import _select_account


def _settings(*, dry_run: bool) -> Settings:
    return Settings(
        automation_store=None,
        symbol_availability_store=None,
        schwab_api_key="key",
        schwab_app_secret="secret",
        schwab_callback_url="http://localhost",
        schwab_token_path=Path("token.json"),
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
        enable_new_entries=True,
        enable_management=True,
        require_explicit_account=True,
        max_consecutive_failures=3,
        enforce_trading_window=True,
        trading_timezone="America/New_York",
        trading_start_hour=10,
        trading_start_minute=0,
        trading_end_hour=15,
        trading_end_minute=0,
        entry_candidate_prompt_path=Path("candidate.prompt.md"),
        entry_chain_min_dte=7,
        entry_chain_max_dte=45,
        entry_chain_strike_count=12,
        entry_chain_contract_limit=12,
        sec_edgar_mcp_url="http://localhost:9870/mcp",
        yahoo_finance_mcp_url="http://localhost:8809/mcp",
        price_data_mcp_url="http://localhost:8810/mcp",
        automation_prompt_path=Path("automation.prompt.md"),
    )


def _analysis(*, deployable_cash: str = "10000", short_put_contracts: int = 0) -> PortfolioAnalysis:
    return PortfolioAnalysis(
        cash_balance=Decimal("10000"),
        reserved_cash=Decimal("0"),
        deployable_cash=Decimal(deployable_cash),
        symbols=[
            SymbolAnalysis(
                symbol="SPY",
                share_quantity=Decimal("100"),
                share_lots=1,
                short_call_contracts=0,
                long_call_contracts=0,
                long_dated_long_call_contracts=0,
                short_put_contracts=short_put_contracts,
                short_put_reserve=Decimal("5000") if short_put_contracts else Decimal("0"),
                covered_call_contracts_available=1,
                pmcc_contracts_available=0,
            )
        ],
        decisions=[ShadowDecision(action_type="capital_summary", status="proposed", symbol=None, rationale="test", details={})],
        supported_symbols=["SPY"],
    )


def _entry_action(*, strike: str = "50") -> ProposedAction:
    return ProposedAction(
        action_type="sell_cash_secured_put",
        symbol="SPY",
        strategy_type="cash_secured_put",
        confidence="high",
        rationale=["test entry"],
        quantity=1,
        option_type="PUT",
        expiration=date(2026, 4, 17),
        strike=Decimal(strike),
        limit_price=Decimal("1.25"),
        related_position_key=None,
        current_expiration=None,
        current_strike=None,
        target_expiration=None,
        target_strike=None,
        raw_payload={},
    )


class ExplicitAccountSelectionTests(unittest.TestCase):
    def test_live_mode_requires_explicit_account_selection(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Explicit Schwab account selection is required"):
            _select_account(
                [{"accountNumber": "123", "hashValue": "abc"}],
                _settings(dry_run=False),
            )

    def test_dry_run_can_use_first_account_without_explicit_selection(self) -> None:
        account = _select_account(
            [{"accountNumber": "123", "hashValue": "abc"}],
            _settings(dry_run=True),
        )

        self.assertEqual(account["hashValue"], "abc")


class LiveGuardrailValidationTests(unittest.TestCase):
    def test_max_new_entries_per_run_rejects_second_entry(self) -> None:
        results = validate_actions(
            actions=[_entry_action(strike="40"), _entry_action(strike="41")],
            analysis=_analysis(deployable_cash="10000"),
            positions=[],
            supported_symbols=["SPY"],
            enable_new_entries=True,
            enable_management=True,
            max_new_entries_per_run=1,
            max_contracts_per_symbol=3,
            min_account_cash_floor=1000,
            liquidation_value=Decimal("30000"),
            max_position_pct_of_portfolio=50,
        )

        self.assertEqual(results[0].validation_status, "valid")
        self.assertEqual(results[1].validation_status, "invalid")
        self.assertIn("New entry limit per run has been reached", results[1].validation_errors)

    def test_max_contracts_per_symbol_blocks_new_short_exposure(self) -> None:
        result = validate_actions(
            actions=[_entry_action()],
            analysis=_analysis(short_put_contracts=1),
            positions=[],
            supported_symbols=["SPY"],
            enable_new_entries=True,
            enable_management=True,
            max_new_entries_per_run=3,
            max_contracts_per_symbol=1,
            min_account_cash_floor=1000,
            liquidation_value=Decimal("30000"),
            max_position_pct_of_portfolio=50,
        )[0]

        self.assertEqual(result.validation_status, "invalid")
        self.assertIn("Requested entry would exceed the max contracts per symbol limit", result.validation_errors)

    def test_minimum_cash_floor_blocks_overdeployment(self) -> None:
        result = validate_actions(
            actions=[_entry_action()],
            analysis=_analysis(deployable_cash="6000"),
            positions=[],
            supported_symbols=["SPY"],
            enable_new_entries=True,
            enable_management=True,
            max_new_entries_per_run=3,
            max_contracts_per_symbol=3,
            min_account_cash_floor=1500,
            liquidation_value=Decimal("30000"),
            max_position_pct_of_portfolio=50,
        )[0]

        self.assertEqual(result.validation_status, "invalid")
        self.assertIn("Requested CSP would breach the minimum account cash floor", result.validation_errors)

    def test_position_size_cap_blocks_large_csp_relative_to_portfolio(self) -> None:
        result = validate_actions(
            actions=[_entry_action(strike="200")],
            analysis=_analysis(deployable_cash="50000"),
            positions=[],
            supported_symbols=["SPY"],
            enable_new_entries=True,
            enable_management=True,
            max_new_entries_per_run=3,
            max_contracts_per_symbol=3,
            min_account_cash_floor=1000,
            liquidation_value=Decimal("30000"),
            max_position_pct_of_portfolio=50,
        )[0]

        self.assertEqual(result.validation_status, "invalid")
        self.assertIn("Requested CSP would exceed the max position percentage of portfolio", result.validation_errors)