from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from schwab_automation.analysis import PortfolioAnalysis, ShadowDecision, SymbolAnalysis
from schwab_automation.llm_actions import ProposedAction, ValidatedAction
from schwab_automation.main import (
    AnalysisState,
    DiscoveryState,
    ExecutionPolicy,
    ReconciledState,
    TradingWindowStatus,
    _analysis_phase,
    _build_report,
    _candidate_discovery_phase,
)
from schwab_automation.sync import AccountSnapshot


class _FakeStore:
    def __init__(self) -> None:
        self.decisions: list[dict[str, object]] = []

    def record_decision(self, **kwargs) -> None:
        self.decisions.append(kwargs)


def _snapshot() -> AccountSnapshot:
    return AccountSnapshot(
        account_hash="hash-1",
        account_number="35525199",
        account_type="MARGIN",
        display_name="Test Account",
        cash_available=Decimal("35272.83"),
        cash_reserved=Decimal("0"),
        liquidation_value=Decimal("35272.83"),
        balances={},
        raw_payload={},
        positions=[],
        synced_at=datetime(2026, 3, 10, 22, 55, tzinfo=UTC),
    )


def _analysis(*, with_position: bool = False) -> PortfolioAnalysis:
    symbols = []
    if with_position:
        symbols = [
            SymbolAnalysis(
                symbol="SPY",
                share_quantity=Decimal("100"),
                share_lots=1,
                short_call_contracts=1,
                long_call_contracts=0,
                long_dated_long_call_contracts=0,
                short_put_contracts=0,
                short_put_reserve=Decimal("0"),
                covered_call_contracts_available=0,
                pmcc_contracts_available=0,
            )
        ]
    return PortfolioAnalysis(
        cash_balance=Decimal("35272.83"),
        reserved_cash=Decimal("0"),
        deployable_cash=Decimal("35272.83"),
        symbols=symbols,
        decisions=[
            ShadowDecision(
                action_type="capital_summary",
                status="proposed",
                symbol=None,
                rationale="Computed deployable cash.",
                details={},
            )
        ],
        supported_symbols=["AMD", "SPY"],
    )


def _state(*, with_position: bool = False) -> ReconciledState:
    return ReconciledState(
        snapshot=_snapshot(),
        analysis=_analysis(with_position=with_position),
        positions=[],
        trading_window=TradingWindowStatus(
            is_open=False,
            local_timestamp=datetime(2026, 3, 10, 22, 55, tzinfo=UTC),
            reason="Outside trading window 10:00 to 15:00 America/New_York",
        ),
        policy=ExecutionPolicy(
            analysis_supported_symbols=["AMD", "SPY"],
            analysis_enabled=True,
            enable_new_entries=True,
            enable_management=True,
            execution_enabled=False,
            recommendation_mode=True,
            execution_block_reason="Outside trading window",
        ),
        supported_symbols=["AMD", "SPY"],
    )


def _analysis_state() -> AnalysisState:
    proposed = ProposedAction(
        action_type="skip",
        symbol=None,
        strategy_type=None,
        confidence="high",
        rationale=[
            "There are no existing positions, so no hold, close, or roll action applies.",
            "No new entry is proposed for this session.",
        ],
        quantity=0,
        option_type=None,
        expiration=None,
        strike=None,
        limit_price=None,
        related_position_key=None,
        current_expiration=None,
        current_strike=None,
        target_expiration=None,
        target_strike=None,
        raw_payload={},
    )
    return AnalysisState(
        llm_actions=[
            ValidatedAction(
                proposed=proposed,
                validation_status="valid",
                execution_supported=False,
                validation_errors=[],
                matched_position=None,
            )
        ]
    )


class DiscordReportTests(unittest.TestCase):
    def test_report_is_compact_and_reasoning_focused(self) -> None:
        report = _build_report(
            settings=SimpleNamespace(service_name="schwab-automation", run_type="hourly", dry_run=True),
            state=_state(),
            discovery=DiscoveryState(),
            analysis_state=_analysis_state(),
        )

        self.assertIn("Positions:\n- none", report)
        self.assertIn("Reasoning:", report)
        self.assertIn("- portfolio: skip", report)
        self.assertIn("There are no existing positions", report)
        self.assertNotIn("Candidate discovery:", report)
        self.assertNotIn("Shadow actions:", report)
        self.assertNotIn("Eligibility:", report)
        self.assertNotIn("Tracked universe:", report)
        self.assertNotIn("Cash balance:", report)


class CandidateDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_llm_candidate_result_does_not_fall_back(self) -> None:
        settings = SimpleNamespace(
            entry_candidate_prompt_path=None,
            min_entry_cash=1000,
            min_csp_reserve=2500,
        )
        store = _FakeStore()
        state = _state()

        with patch(
            "schwab_automation.main.run_structured_analysis",
            new=AsyncMock(return_value=({"candidates": []}, "{}", True)),
        ), patch(
            "schwab_automation.main.fetch_broker_option_candidates",
            new=AsyncMock(return_value={}),
        ) as fetch_options:
            discovery = await _candidate_discovery_phase(
                settings,
                store=store,
                run_id=123,
                client=object(),
                state=state,
            )

        self.assertEqual(discovery.candidate_ideas, [])
        self.assertEqual(discovery.source, "none")
        self.assertIsNone(discovery.error)
        fetch_options.assert_not_called()
        self.assertTrue(store.decisions)
        self.assertEqual(store.decisions[-1]["status"], "skipped")
        self.assertIn("without any structured entry ideas", store.decisions[-1]["rationale"])

    async def test_candidate_discovery_discards_results_without_tool_events(self) -> None:
        settings = SimpleNamespace(
            entry_candidate_prompt_path=None,
            min_entry_cash=1000,
            min_csp_reserve=2500,
        )
        store = _FakeStore()
        state = _state()

        with patch(
            "schwab_automation.main.run_structured_analysis",
            new=AsyncMock(
                return_value=(
                    {
                        "candidates": [
                            {
                                "symbol": "AMD",
                                "strategy_type": "cash_secured_put",
                                "option_type": "PUT",
                                "confidence": "high",
                                "rationale": ["Test candidate"],
                            }
                        ]
                    },
                    "{}",
                    False,
                )
            ),
        ), patch(
            "schwab_automation.main.fetch_broker_option_candidates",
            new=AsyncMock(return_value={}),
        ) as fetch_options:
            discovery = await _candidate_discovery_phase(
                settings,
                store=store,
                run_id=123,
                client=object(),
                state=state,
            )

        self.assertEqual(discovery.candidate_ideas, [])
        self.assertEqual(discovery.source, "none")
        fetch_options.assert_not_called()
        self.assertEqual(store.decisions[-1]["status"], "skipped")
        self.assertIn("without MCP tool confirmation", store.decisions[-1]["rationale"])


class AnalysisGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_tool_events_filters_all_actionable_decisions(self) -> None:
        settings = SimpleNamespace(
            min_entry_cash=1000,
            min_csp_reserve=2500,
            roll_dte_threshold_days=14,
            close_dte_threshold_days=5,
            max_new_entries_per_run=2,
            max_contracts_per_symbol=2,
            min_account_cash_floor=Decimal("1000"),
            max_position_pct_of_portfolio=25,
        )
        store = _FakeStore()
        state = _state(with_position=True)
        payload = {
            "actions": [
                {
                    "action_type": "hold",
                    "symbol": "SPY",
                    "strategy_type": "stock",
                    "confidence": "high",
                    "rationale": ["Keep the existing stock position unchanged."],
                    "quantity": 0,
                    "option_type": None,
                    "expiration": None,
                    "strike": None,
                    "limit_price": None,
                    "related_position_key": None,
                    "current_expiration": None,
                    "current_strike": None,
                    "target_expiration": None,
                    "target_strike": None,
                },
                {
                    "action_type": "close_option",
                    "symbol": "SPY",
                    "strategy_type": "option_management",
                    "confidence": "medium",
                    "rationale": ["Close the short call."],
                    "quantity": 1,
                    "option_type": "CALL",
                    "expiration": "2026-03-20",
                    "strike": "580",
                    "limit_price": "1.25",
                    "related_position_key": "pos-1",
                    "current_expiration": None,
                    "current_strike": None,
                    "target_expiration": None,
                    "target_strike": None,
                },
                {
                    "action_type": "roll_option",
                    "symbol": "SPY",
                    "strategy_type": "option_management",
                    "confidence": "medium",
                    "rationale": ["Roll the short call out in time."],
                    "quantity": 1,
                    "option_type": "CALL",
                    "expiration": None,
                    "strike": None,
                    "limit_price": "0.45",
                    "related_position_key": "pos-1",
                    "current_expiration": "2026-03-20",
                    "current_strike": "580",
                    "target_expiration": "2026-04-17",
                    "target_strike": "585",
                },
                {
                    "action_type": "sell_cash_secured_put",
                    "symbol": "AMD",
                    "strategy_type": "cash_secured_put",
                    "confidence": "high",
                    "rationale": ["Enter a new CSP."],
                    "quantity": 1,
                    "option_type": "PUT",
                    "expiration": "2026-04-17",
                    "strike": "140",
                    "limit_price": "2.10",
                    "related_position_key": None,
                    "current_expiration": None,
                    "current_strike": None,
                    "target_expiration": None,
                    "target_strike": None,
                },
                {
                    "action_type": "skip",
                    "symbol": None,
                    "strategy_type": "none",
                    "confidence": "medium",
                    "rationale": ["Do not add more trades right now."],
                    "quantity": 0,
                    "option_type": None,
                    "expiration": None,
                    "strike": None,
                    "limit_price": None,
                    "related_position_key": None,
                    "current_expiration": None,
                    "current_strike": None,
                    "target_expiration": None,
                    "target_strike": None,
                },
            ]
        }

        with patch(
            "schwab_automation.main.run_structured_analysis",
            new=AsyncMock(return_value=(payload, "{}", False)),
        ):
            analysis = await _analysis_phase(
                settings,
                store=store,
                run_id=456,
                state=state,
                discovery=DiscoveryState(),
            )

        self.assertIsNone(analysis.llm_error)
        self.assertEqual([item.proposed.action_type for item in analysis.llm_actions], ["hold", "skip"])
        self.assertTrue(store.decisions)
        self.assertEqual(store.decisions[-1]["status"], "skipped")
        self.assertIn("Filtered 3 actionable decisions", store.decisions[-1]["rationale"])


if __name__ == "__main__":
    unittest.main()