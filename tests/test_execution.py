import os
import unittest
from unittest.mock import patch

from app.backend.execution import (
    ExecutionModeFactory,
    SimulationExecutionMode,
    TestnetExecutionMode,
)
from app.backend.strategy import (
    MarketSnapshot,
    NoStrategy,
    SignalAction,
    StrategySignal,
)
from app.backend.trade_statistics import (
    MANUAL_TRADE_STRATEGY,
    StrategyTradeStatistics,
)


class FakeBinanceClient:
    def __init__(self):
        self.active_positions = []
        self.orders = []
        self.pending_orders = []
        self.cancelled_symbols = []

    def get_open_positions(self):
        return list(self.active_positions)

    def get_open_orders(self, symbol=None):
        return [
            order for order in self.pending_orders
            if symbol is None or order["symbol"] == symbol
        ]

    def get_account_summary(self):
        return {
            "wallet_balance": 1000.0,
            "available_balance": 900.0,
            "unrealized_pnl": 5.0,
        }

    def get_price(self, symbol):
        return 100.0

    def enter_position(self, symbol, side, price, notional):
        self.orders.append((symbol, side, notional))
        amount = notional / price * (1 if side == "LONG" else -1)
        self.active_positions.append(
            {
                "symbol": symbol,
                "positionAmt": str(amount),
                "entryPrice": str(price),
                "markPrice": str(price),
            }
        )

    def enter_limit_position(self, symbol, side, price, notional):
        self.orders.append((symbol, side, notional))
        order = {
            "symbol": symbol,
            "side": side,
            "price": str(price),
            "orderId": 123,
            "status": "NEW",
            "reduceOnly": False,
        }
        self.pending_orders.append(order)
        return order

    def cancel_order(self, symbol, order_id):
        self.pending_orders = [
            order for order in self.pending_orders
            if order["symbol"] != symbol or order["orderId"] != order_id
        ]
        return {"status": "CANCELED"}

    def cancel_all_orders(self, symbol):
        self.cancelled_symbols.append(symbol)
        self.pending_orders = [
            order for order in self.pending_orders if order["symbol"] != symbol
        ]
        return {"status": "CANCELED"}

    def close_position(self, symbol):
        self.active_positions = [
            item for item in self.active_positions if item["symbol"] != symbol
        ]


class ExecutionModeTests(unittest.TestCase):
    def setUp(self):
        self.long = StrategySignal("LONG", "test")
        self.short = StrategySignal("SHORT", "test")
        self.flat = StrategySignal(None, "test")
        self.hold = StrategySignal(None, "hold", SignalAction.HOLD)

    def test_simulation_does_not_trade_without_a_strategy_signal(self):
        mode = SimulationExecutionMode(order_margin=50, max_open_positions=2)
        market = MarketSnapshot("BTCUSDT", 100, 1_000_000_000)

        mode.update(market, NoStrategy().evaluate(market))

        self.assertEqual(mode.positions(), [])

    def test_simulation_enforces_open_position_limit_and_closes_flat_signal(self):
        mode = SimulationExecutionMode(order_margin=50, max_open_positions=1)
        btc = MarketSnapshot("BTCUSDT", 100, 1_000_000_000)
        eth = MarketSnapshot("ETHUSDT", 200, 1_000_000_000)

        self.assertTrue(mode.update(btc, self.long))
        limit_message = mode.update(eth, self.long)
        self.assertIn("đã đạt giới hạn", limit_message[0])
        self.assertEqual([item.symbol for item in mode.positions()], ["BTCUSDT"])
        self.assertTrue(mode.update(btc, self.flat))
        self.assertEqual(mode.positions(), [])

    def test_simulation_closes_at_take_profit_and_records_net_pnl(self):
        mode = SimulationExecutionMode(
            order_margin=50,
            max_open_positions=1,
            take_profit_pct=1.0,
            stop_loss_pct=1.0,
            fee_rate=0,
        )
        market = MarketSnapshot("BTCUSDT", 100, 1_000_000_000)
        mode.update(market, self.long)

        events = mode.update(
            MarketSnapshot("BTCUSDT", 101.1, 1_000_000_000), self.hold
        )

        self.assertIn("chốt lời", events[0])
        self.assertEqual(mode.positions(), [])
        self.assertAlmostEqual(mode.realized_pnl, 1.1)

    def test_simulation_records_strategy_and_realized_result(self):
        statistics = StrategyTradeStatistics()
        mode = SimulationExecutionMode(
            order_margin=50,
            max_open_positions=1,
            fee_rate=0,
            trade_statistics=statistics,
        )
        market = MarketSnapshot("BTCUSDT", 100, 1_000_000_000)
        strategy_signal = StrategySignal(
            "LONG", "long pattern", strategy_name="long_p", timeframe="15m"
        )

        mode.update(market, strategy_signal)
        self.assertEqual(mode.positions()[0].strategy_name, "long_p")
        self.assertEqual(mode.positions()[0].leverage, 2)
        self.assertEqual(mode.positions()[0].margin_usdt, 50)
        self.assertEqual(mode.positions()[0].notional_usdt, 100)
        mode.update(
            MarketSnapshot("BTCUSDT", 110, 1_000_000_000),
            StrategySignal(None, "close"),
        )

        row = statistics.snapshot()[0]
        self.assertEqual((row.opened, row.closed, row.wins), (1, 1, 1))
        self.assertEqual(row.timeframe, "15m")
        self.assertAlmostEqual(row.winning_pnl, 10)

    def test_simulation_telegram_trade_is_grouped_as_manual(self):
        statistics = StrategyTradeStatistics()
        mode = SimulationExecutionMode(
            order_margin=50,
            max_open_positions=2,
            trade_statistics=statistics,
        )
        mode.update(
            MarketSnapshot("BTCUSDT", 100, 1_000_000_000),
            StrategySignal("SHORT", "short pattern", strategy_name="short_p"),
        )
        mode.update(
            MarketSnapshot("ETHUSDT", 100, 1_000_000_000),
            StrategySignal(None, "hold", SignalAction.HOLD),
        )
        mode.execute_telegram_command(
            "open", ["ETHUSDT", "LONG", "MARKET"], 123
        )

        rows = {row.strategy: row for row in statistics.snapshot()}
        self.assertEqual(rows["short_p"].opened, 1)
        self.assertEqual(rows[MANUAL_TRADE_STRATEGY].opened, 1)

    def test_simulation_closes_at_stop_loss(self):
        mode = SimulationExecutionMode(
            order_margin=50,
            max_open_positions=1,
            take_profit_pct=5.0,
            stop_loss_pct=1.0,
            fee_rate=0,
        )
        mode.update(MarketSnapshot("BTCUSDT", 100, 1_000_000_000), self.long)

        events = mode.update(
            MarketSnapshot("BTCUSDT", 98.9, 1_000_000_000), self.hold
        )

        self.assertIn("dừng lỗ", events[0])
        self.assertEqual(mode.positions(), [])
        self.assertAlmostEqual(mode.realized_pnl, -1.1)

    def test_reverse_signal_closes_without_opening_opposite_position(self):
        mode = SimulationExecutionMode(
            order_margin=50,
            max_open_positions=1,
            fee_rate=0,
        )
        market = MarketSnapshot("BTCUSDT", 100, 1_000_000_000)
        mode.update(market, self.long)

        events = mode.update(market, self.short)

        self.assertIn("tín hiệu ngược chiều", events[0])
        self.assertEqual(mode.positions(), [])

    def test_testnet_enforces_cap_and_closes_only_its_managed_position(self):
        client = FakeBinanceClient()
        mode = TestnetExecutionMode(client, order_margin=50, max_open_positions=1)
        btc = MarketSnapshot("BTCUSDT", 100, 1_000_000_000)
        eth = MarketSnapshot("ETHUSDT", 200, 1_000_000_000)

        self.assertTrue(mode.update(btc, self.long))
        self.assertEqual(len(client.orders), 1)
        self.assertEqual(client.orders[0][2], 100)
        self.assertIn("đã đạt giới hạn", mode.update(eth, self.long)[0])
        position = mode.positions()[0]
        self.assertEqual(position.symbol, "BTCUSDT")
        self.assertEqual(position.leverage, 2)
        self.assertEqual(position.margin_usdt, 50)
        self.assertEqual(position.notional_usdt, 100)
        self.assertTrue(mode.update(btc, self.flat))
        self.assertEqual(mode.positions(), [])
        self.assertEqual(len(client.orders), 1)

    def test_testnet_records_strategy_on_open_and_close(self):
        client = FakeBinanceClient()
        statistics = StrategyTradeStatistics()
        mode = TestnetExecutionMode(
            client,
            order_margin=50,
            max_open_positions=1,
            fee_rate=0,
            trade_statistics=statistics,
        )
        signal = StrategySignal(
            "LONG", "long pattern", strategy_name="long_p", timeframe="1h"
        )

        mode.update(MarketSnapshot("BTCUSDT", 100, 1_000_000_000), signal)
        self.assertEqual(mode.positions()[0].timeframe, "1h")
        client.active_positions[0]["markPrice"] = "101"
        mode._telegram_close("BTCUSDT")

        row = statistics.snapshot()[0]
        self.assertEqual(row.mode, "Testnet")
        self.assertEqual(row.strategy, "long_p")
        self.assertEqual(row.timeframe, "1h")
        self.assertEqual((row.opened, row.closed, row.wins), (1, 1, 1))
        self.assertAlmostEqual(row.winning_pnl, 1.0)

    def test_testnet_account_summary_includes_wallet_available_and_equity(self):
        mode = TestnetExecutionMode(
            FakeBinanceClient(),
            order_margin=50,
            max_open_positions=1,
        )

        self.assertEqual(
            mode.account_summary(),
            {
                "wallet_balance": 1000.0,
                "available_balance": 900.0,
                "unrealized_pnl": 5.0,
                "equity": 1005.0,
            },
        )

    def test_testnet_does_not_manage_preexisting_external_position(self):
        client = FakeBinanceClient()
        client.active_positions.append(
            {
                "symbol": "BTCUSDT",
                "positionAmt": "0.5",
                "entryPrice": "100",
                "markPrice": "101",
            }
        )
        mode = TestnetExecutionMode(client, order_margin=50, max_open_positions=2)

        messages = mode.update(
            MarketSnapshot("BTCUSDT", 101, 1_000_000_000), self.long
        )

        self.assertIn("ngoài phiên này", messages[0])
        self.assertEqual(client.orders, [])

    def test_testnet_closes_managed_position_at_take_profit(self):
        client = FakeBinanceClient()
        mode = TestnetExecutionMode(
            client,
            order_margin=50,
            max_open_positions=1,
            take_profit_pct=1.0,
            stop_loss_pct=1.0,
            fee_rate=0,
        )
        market = MarketSnapshot("BTCUSDT", 100, 1_000_000_000)
        mode.update(market, self.long)
        client.active_positions[0]["markPrice"] = "101.1"

        events = mode.update(market, self.hold)

        self.assertIn("chốt lời", events[0])
        self.assertEqual(client.active_positions, [])
        self.assertAlmostEqual(mode.realized_pnl, 1.1)

    def test_testnet_limit_order_is_managed_and_respects_position_cap(self):
        client = FakeBinanceClient()
        mode = TestnetExecutionMode(client, order_margin=50, max_open_positions=1)

        response = mode.execute_telegram_command(
            "open", ["BTCUSDT", "LONG", "LIMIT", "100"], 123
        )

        self.assertIn("LIMIT LONG BTCUSDT", response)
        self.assertEqual(mode._managed_order_ids, {"BTCUSDT": 123})
        with self.assertRaisesRegex(RuntimeError, "giới hạn"):
            mode.execute_telegram_command(
                "open", ["ETHUSDT", "SHORT", "MARKET"], 123
            )

    def test_close_all_requires_confirmation_and_cancels_orders(self):
        client = FakeBinanceClient()
        client.active_positions.append(
            {
                "symbol": "BTCUSDT",
                "positionAmt": "0.5",
                "entryPrice": "100",
                "markPrice": "101",
            }
        )
        client.pending_orders.append(
            {
                "symbol": "ETHUSDT",
                "orderId": 456,
                "reduceOnly": False,
                "closePosition": False,
            }
        )
        mode = TestnetExecutionMode(client, order_margin=50, max_open_positions=2)

        mode.execute_telegram_command("close_all", [], 123)
        with self.assertRaisesRegex(ValueError, "Mã xác nhận"):
            mode.execute_telegram_command("confirm", ["WRONG1"], 123)
        self.assertEqual(len(client.active_positions), 1)

        # A fresh confirmation is required after an invalid code.
        request = mode.execute_telegram_command("close_all", [], 123)
        code = request.split("/confirm ", 1)[1].split()[0]
        result = mode.execute_telegram_command("confirm", [code], 123)

        self.assertIn("đóng 1 vị thế", result)
        self.assertEqual(client.active_positions, [])
        self.assertEqual(client.pending_orders, [])
        self.assertEqual(client.cancelled_symbols, ["ETHUSDT"])

    def test_execution_limits_reject_invalid_values(self):
        for notional, count in (
            (0, 1),
            (float("inf"), 1),
            (51, 1),
            (50, 0),
            (50, 1.5),
            (50, 21),
        ):
            with self.subTest(notional=notional, count=count):
                with self.assertRaises(ValueError):
                    SimulationExecutionMode(notional, count)

    def test_testnet_notional_cap_applies_to_margin_times_leverage(self):
        testnet_environment = {
            "BINANCE_TESTNET_TRADING_ENABLED": "true",
            "BINANCE_TESTNET_API_KEY": "test-key",
            "BINANCE_TESTNET_API_SECRET": "test-secret",
            "BINANCE_TESTNET_MAX_NOTIONAL_USDT": "100",
        }
        with patch.dict(os.environ, testnet_environment, clear=True):
            with patch("app.backend.execution.BinanceClient"):
                mode = ExecutionModeFactory.create("testnet", 50, 1)

        self.assertEqual(mode.order_margin, 50)
        self.assertEqual(mode.order_notional, 100)

        testnet_environment["BINANCE_TESTNET_MAX_NOTIONAL_USDT"] = "99"
        with patch.dict(os.environ, testnet_environment, clear=True):
            with patch("app.backend.execution.BinanceClient"):
                with self.assertRaisesRegex(ValueError, "100.00 USDT vượt"):
                    ExecutionModeFactory.create("testnet", 50, 1)

    def test_simulation_telegram_market_open_close_and_pnl_commands(self):
        mode = SimulationExecutionMode(
            order_margin=50,
            max_open_positions=20,
            take_profit_pct=10,
            stop_loss_pct=10,
            fee_rate=0,
        )
        market = MarketSnapshot("BTCUSDT", 100, 1_000_000_000)
        mode.update(market, self.hold)

        opened = mode.execute_telegram_command(
            "open", ["BTCUSDT", "LONG", "MARKET"], 123
        )
        self.assertIn("mở LONG BTCUSDT", opened)
        self.assertEqual(mode.available_balance, 950)

        mode.update(MarketSnapshot("BTCUSDT", 102, 1_000_000_000), self.hold)
        pnl = mode.execute_telegram_command("pnl", [], 123)
        self.assertIn("Chưa chốt (ước tính ròng): +2.0000 USDT", pnl)
        account = mode.execute_telegram_command("account", [], 123)
        self.assertIn("Vốn ban đầu: 1000.00 USDT", account)
        self.assertIn("Vị thế mở: 1/20", account)
        self.assertEqual(mode.account_summary()["wallet_balance"], 1000)
        positions = mode.execute_telegram_command("positions", [], 123)
        self.assertIn("BTCUSDT LONG", positions)

        closed = mode.execute_telegram_command("close", ["BTCUSDT"], 123)
        self.assertIn("PnL ròng +2.0000 USDT", closed)
        self.assertEqual(mode.available_balance, 1002)

    def test_simulation_telegram_rejects_orders_over_available_balance(self):
        mode = SimulationExecutionMode(
            order_margin=50,
            max_open_positions=20,
            starting_balance=75,
            fee_rate=0,
        )
        btc = MarketSnapshot("BTCUSDT", 100, 1_000_000_000)
        eth = MarketSnapshot("ETHUSDT", 100, 1_000_000_000)
        mode.update(btc, self.hold)
        mode.update(eth, self.hold)
        mode.execute_telegram_command(
            "open", ["BTCUSDT", "LONG", "MARKET"], 123
        )

        with self.assertRaisesRegex(RuntimeError, "Vượt số tiền mô phỏng"):
            mode.execute_telegram_command(
                "open", ["ETHUSDT", "SHORT", "MARKET"], 123
            )
        events = mode.update(eth, self.long)
        self.assertIn("Vượt số tiền mô phỏng", events[0])
        self.assertEqual(mode.available_balance, 25)
        self.assertEqual([position.symbol for position in mode.positions()], ["BTCUSDT"])

    def test_simulation_allows_twenty_default_orders_but_not_more_than_capital(self):
        mode = SimulationExecutionMode(
            order_margin=50,
            max_open_positions=20,
            fee_rate=0,
        )
        symbols = [f"T{index:02d}USDT" for index in range(21)]
        for symbol in symbols[:20]:
            mode.update(
                MarketSnapshot(symbol, 100, 1_000_000_000),
                self.hold,
            )
            mode.execute_telegram_command(
                "open", [symbol, "LONG", "MARKET"], 123
            )

        self.assertEqual(mode.open_margin, 1000)
        self.assertEqual(mode.open_notional, 2000)
        self.assertEqual(mode.available_balance, 0)
        mode.update(
            MarketSnapshot(symbols[20], 100, 1_000_000_000),
            self.hold,
        )
        with self.assertRaisesRegex(RuntimeError, "Vượt số tiền mô phỏng"):
            mode.execute_telegram_command(
                "open", [symbols[20], "LONG", "MARKET"], 123
            )

    def test_simulation_telegram_closes_all_positions_and_rejects_limit_orders(self):
        mode = SimulationExecutionMode(
            order_margin=50,
            max_open_positions=20,
            fee_rate=0,
        )
        for symbol in ("BTCUSDT", "ETHUSDT"):
            mode.update(
                MarketSnapshot(symbol, 100, 1_000_000_000),
                self.hold,
            )
            mode.execute_telegram_command(
                "open", [symbol, "LONG", "MARKET"], 123
            )

        with self.assertRaisesRegex(RuntimeError, "lệnh LIMIT"):
            mode.execute_telegram_command(
                "open", ["BTCUSDT", "LONG", "LIMIT", "99"], 123
            )
        response = mode.execute_telegram_command("close_all", [], 123)

        self.assertIn("Đã đóng 2 vị thế Simulation", response)
        self.assertEqual(mode.positions(), [])


if __name__ == "__main__":
    unittest.main()
