from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
import unittest

from schwab_automation.analysis import PortfolioAnalysis, ShadowDecision, SymbolAnalysis
from schwab_automation.execution import build_order_spec
from schwab_automation.llm_actions import ProposedAction, validate_actions


def _analysis(*, deployable_cash: str = "1000") -> PortfolioAnalysis:
    return PortfolioAnalysis(
        cash_balance=Decimal("1000"),
        reserved_cash=Decimal("5000"),
        deployable_cash=Decimal(deployable_cash),
        symbols=[
            SymbolAnalysis(
                symbol="SPY",
                share_quantity=Decimal("100"),
                share_lots=1,
                short_call_contracts=1,
                long_call_contracts=0,
                long_dated_long_call_contracts=0,
                short_put_contracts=1,
                short_put_reserve=Decimal("5000"),
                covered_call_contracts_available=0,
                pmcc_contracts_available=0,
            )
        ],
        decisions=[ShadowDecision(action_type="capital_summary", status="proposed", symbol=None, rationale="test", details={})],
        supported_symbols=["SPY"],
    )


def _short_put_position() -> dict:
    return {
        "position_key": "SPY|OPTION|SPY   260417P00500000",
        "underlying_symbol": "SPY",
        "asset_type": "OPTION",
        "option_type": "PUT",
        "expiration_date": datetime(2026, 4, 17, tzinfo=UTC),
        "strike_price": Decimal("50"),
        "quantity": Decimal("-1"),
        "long_quantity": Decimal("0"),
        "short_quantity": Decimal("1"),
        "raw_payload": {"instrument": {"symbol": "SPY   260417P00500000"}},
    }


class RollValidationTests(unittest.TestCase):
    def test_roll_put_uses_current_fields_and_released_reserve(self) -> None:
        action = ProposedAction(
            action_type="roll_option",
            symbol="SPY",
            strategy_type="option_management",
            confidence="high",
            rationale=["roll forward for more duration"],
            quantity=1,
            option_type="PUT",
            expiration=None,
            strike=None,
            limit_price=Decimal("0.25"),
            related_position_key=None,
            current_expiration=date(2026, 4, 17),
            current_strike=Decimal("50"),
            target_expiration=date(2026, 5, 15),
            target_strike=Decimal("51"),
            raw_payload={},
        )

        result = validate_actions(
            actions=[action],
            analysis=_analysis(deployable_cash="100"),
            positions=[_short_put_position()],
            supported_symbols=["SPY"],
            enable_new_entries=False,
            enable_management=True,
        )[0]

        self.assertEqual(result.validation_status, "valid")
        self.assertTrue(result.execution_supported)

    def test_roll_put_rejects_when_post_roll_cash_is_insufficient(self) -> None:
        action = ProposedAction(
            action_type="roll_option",
            symbol="SPY",
            strategy_type="option_management",
            confidence="high",
            rationale=["attempt richer put strike"],
            quantity=1,
            option_type="PUT",
            expiration=None,
            strike=None,
            limit_price=Decimal("0.20"),
            related_position_key=None,
            current_expiration=date(2026, 4, 17),
            current_strike=Decimal("50"),
            target_expiration=date(2026, 5, 15),
            target_strike=Decimal("70"),
            raw_payload={},
        )

        result = validate_actions(
            actions=[action],
            analysis=_analysis(deployable_cash="100"),
            positions=[_short_put_position()],
            supported_symbols=["SPY"],
            enable_new_entries=False,
            enable_management=True,
        )[0]

        self.assertEqual(result.validation_status, "invalid")
        self.assertIn("Post-roll cash would not cover the replacement CSP reserve", result.validation_errors)


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    class Options:
        class ContractType:
            CALL = "CALL"
            PUT = "PUT"

    async def get_option_chain(self, symbol, *, contract_type, strike, from_date, to_date):
        strike_key = str(float(strike))
        exp_key = f"{from_date.isoformat()}:35"
        payload_key = "callExpDateMap" if str(contract_type).endswith("CALL") else "putExpDateMap"
        return _FakeResponse(
            {
                payload_key: {
                    exp_key: {
                        strike_key: [
                            {"symbol": f"{symbol}   260515C00620000"}
                        ]
                    }
                }
            }
        )


class RollExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_roll_order_spec_creates_two_leg_combo_order(self) -> None:
        action = ProposedAction(
            action_type="roll_option",
            symbol="SPY",
            strategy_type="option_management",
            confidence="high",
            rationale=["roll short call out in time"],
            quantity=1,
            option_type="CALL",
            expiration=None,
            strike=None,
            limit_price=Decimal("-0.15"),
            related_position_key=None,
            current_expiration=date(2026, 4, 17),
            current_strike=Decimal("600"),
            target_expiration=date(2026, 5, 15),
            target_strike=Decimal("620"),
            raw_payload={},
        )
        validated = validate_actions(
            actions=[action],
            analysis=_analysis(),
            positions=[
                {
                    "position_key": "SPY|OPTION|SPY   260417C00600000",
                    "underlying_symbol": "SPY",
                    "asset_type": "OPTION",
                    "option_type": "CALL",
                    "expiration_date": datetime(2026, 4, 17, tzinfo=UTC),
                    "strike_price": Decimal("600"),
                    "quantity": Decimal("-1"),
                    "long_quantity": Decimal("0"),
                    "short_quantity": Decimal("1"),
                    "raw_payload": {"instrument": {"symbol": "SPY   260417C00600000"}},
                }
            ],
            supported_symbols=["SPY"],
            enable_new_entries=False,
            enable_management=True,
        )[0]

        order = await build_order_spec(_FakeClient(), validated)

        self.assertEqual(order["orderType"], "NET_DEBIT")
        self.assertEqual(order["complexOrderStrategyType"], "DIAGONAL")
        self.assertEqual(order["price"], "0.15")
        self.assertEqual(len(order["orderLegCollection"]), 2)
        self.assertEqual(order["orderLegCollection"][0]["instruction"], "BUY_TO_CLOSE")
        self.assertEqual(order["orderLegCollection"][1]["instruction"], "SELL_TO_OPEN")


if __name__ == "__main__":
    unittest.main()