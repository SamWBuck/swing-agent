from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import unittest

from schwab_automation.analysis import analyze_portfolio
from schwab_automation.main import _filter_supported_symbols


class SupportedSymbolFilterTests(unittest.TestCase):
    def test_filters_futures_and_deduplicates_symbols(self) -> None:
        filtered = _filter_supported_symbols(["spy", "/es", "QQQ", "SPY", "  /nq  ", "IWM"])

        self.assertEqual(filtered, ["SPY", "QQQ", "IWM"])


class AnalysisUniverseTests(unittest.TestCase):
    def test_new_position_scan_uses_all_supported_symbols(self) -> None:
        analysis = analyze_portfolio(
            cash_balance=Decimal("10000"),
            positions=[
                {
                    "underlying_symbol": "SPY",
                    "option_type": "PUT",
                    "expiration_date": datetime(2026, 3, 12, tzinfo=UTC),
                    "strike_price": Decimal("50"),
                    "quantity": Decimal("-1"),
                    "long_quantity": Decimal("0"),
                    "short_quantity": Decimal("1"),
                }
            ],
            as_of=datetime(2026, 3, 10, tzinfo=UTC),
            supported_symbols=["SPY", "QQQ", "IWM", "DIA"],
            min_csp_reserve=Decimal("2500"),
            min_entry_cash=Decimal("1000"),
            roll_dte_threshold_days=7,
            close_dte_threshold_days=1,
        )

        csp_candidates = [
            decision.symbol
            for decision in analysis.decisions
            if decision.action_type == "csp_entry_candidate"
        ]
        scan_decision = next(
            decision for decision in analysis.decisions if decision.action_type == "new_position_scan"
        )

        self.assertEqual(csp_candidates, ["SPY", "QQQ", "IWM", "DIA"])
        self.assertEqual(scan_decision.details["candidate_symbols"], ["SPY", "QQQ", "IWM", "DIA"])


if __name__ == "__main__":
    unittest.main()