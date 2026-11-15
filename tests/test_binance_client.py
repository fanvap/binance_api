import unittest
from decimal import Decimal
from unittest.mock import patch

from app.backend.binance_client import BinanceClient


class BinanceLimitOrderTests(unittest.TestCase):
    def setUp(self):
        self.client = BinanceClient()
        self.client._exchange_info = {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "status": "TRADING",
                    "contractType": "PERPETUAL",
                    "quoteAsset": "USDT",
                    "filters": [
                        {
                            "filterType": "LOT_SIZE",
                            "stepSize": "0.001",
                            "minQty": "0.001",
                        },
                        {
                            "filterType": "PRICE_FILTER",
                            "tickSize": "0.01",
                        },
                        {
                            "filterType": "MIN_NOTIONAL",
                            "notional": "5",
                        },
                    ],
                }
            ]
        }

    def test_limit_order_uses_tick_and_quantity_steps_with_notional_cap(self):
        with patch.object(
            self.client,
            "_signed_request",
            return_value={"orderId": 10},
        ) as signed_request:
            order = self.client.enter_limit_position(
                "BTCUSDT", "LONG", 12.5, 100
            )

        self.assertEqual(order["orderId"], 10)
        self.assertEqual(signed_request.call_count, 2)
        leverage_call = signed_request.call_args_list[0]
        self.assertEqual(leverage_call.args[1], "/fapi/v1/leverage")
        self.assertEqual(leverage_call.args[2], {"symbol": "BTCUSDT", "leverage": 2})
        params = signed_request.call_args.args[2]
        self.assertEqual(params["type"], "LIMIT")
        self.assertEqual(params["price"], "12.5")
        self.assertEqual(Decimal(params["quantity"]), Decimal("8.000"))

    def test_market_order_sets_leverage_before_placing_order(self):
        with patch.object(
            self.client,
            "_signed_request",
            return_value={"orderId": 10},
        ) as signed_request:
            order = self.client.enter_position("BTCUSDT", "LONG", 100, 100)

        self.assertEqual(order["orderId"], 10)
        self.assertEqual(signed_request.call_count, 2)
        self.assertEqual(
            signed_request.call_args_list[0].args[1], "/fapi/v1/leverage"
        )
        self.assertEqual(
            signed_request.call_args_list[1].args[1], "/fapi/v1/order"
        )

    def test_limit_price_must_match_exchange_tick_size(self):
        with self.assertRaisesRegex(ValueError, "tick size"):
            self.client.enter_limit_position(
                "BTCUSDT", "SHORT", 12.501, 100
            )


if __name__ == "__main__":
    unittest.main()
