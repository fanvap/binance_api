import unittest

from app.backend.market_data import Candle
from app.backend.simulator import Simulator
from app.backend.strategy import (
    MarketSnapshot,
    NoStrategy,
    SignalAction,
    StrategySignal,
    StrategyFactory,
    SUPPORTED_CANDLE_INTERVALS,
)


class StrategyTests(unittest.TestCase):
    def test_placeholder_strategy_never_generates_a_trade_signal(self):
        strategy = NoStrategy()
        signal = strategy.evaluate(MarketSnapshot("BTCUSDT", 100.0, 1_000_000_000.0))

        self.assertEqual(strategy.name, "Chưa cấu hình")
        self.assertIsNone(signal.side)
        self.assertEqual(signal.action, SignalAction.HOLD)

    def test_short_p_detects_upper_wick_on_current_candle_and_emits_once(self):
        previous = Candle(1, 98.0, 100.0, 96.0, 98.0)
        current = Candle(2, 98.3, 100.9, 97.0, 97.1)
        market = MarketSnapshot(
            "BTCUSDT",
            97.1,
            1_000_000_000,
            {"1m": (previous, current)},
        )
        strategy = StrategyFactory.create("short_p", ("1m",))

        signal = strategy.evaluate(market)
        repeated_signal = strategy.evaluate(market)

        self.assertEqual(strategy.name, "short_p")
        self.assertEqual(strategy.candle_intervals, ("1m",))
        self.assertEqual(signal.side, "SHORT")
        self.assertEqual(signal.action, SignalAction.SET_POSITION)
        self.assertEqual(signal.timeframe, "1m")
        self.assertEqual(repeated_signal.action, SignalAction.HOLD)

    def test_short_p_rechecks_current_candle_until_pattern_forms(self):
        previous = Candle(1, 98.0, 100.0, 96.0, 98.0)
        forming = Candle(2, 98.3, 100.5, 98.0, 99.9)
        strategy = StrategyFactory.create("short_p", ("1m",))

        initial_signal = strategy.evaluate(
            MarketSnapshot(
                "BTCUSDT", 99.9, 1_000_000_000, {"1m": (previous, forming)}
            )
        )
        updated_signal = strategy.evaluate(
            MarketSnapshot(
                "BTCUSDT",
                97.1,
                1_000_000_000,
                {
                    "1m": (
                        previous,
                        Candle(2, 98.3, 100.9, 97.0, 97.1),
                    )
                },
            )
        )

        self.assertEqual(initial_signal.action, SignalAction.HOLD)
        self.assertEqual(updated_signal.side, "SHORT")

    def test_short_p_rejects_high_more_than_one_percent_above_previous_high(self):
        previous = Candle(1, 98.0, 100.0, 96.0, 98.0)
        current = Candle(2, 98.3, 101.01, 97.0, 97.1)

        signal = StrategyFactory.create("short_p", ("1m",)).evaluate(
            MarketSnapshot(
                "BTCUSDT",
                97.1,
                1_000_000_000,
                {"1m": (previous, current)},
            )
        )

        self.assertEqual(signal.action, SignalAction.HOLD)
        self.assertIsNone(signal.side)

    def test_long_p_requires_sweeping_previous_low_and_closing_near_high(self):
        previous = Candle(1, 100.5, 102.0, 100.0, 101.0)
        current = Candle(2, 100.5, 101.0, 98.5, 100.9)
        strategy = StrategyFactory.create("long_p", ("5m",))

        signal = strategy.evaluate(
            MarketSnapshot(
                "BTCUSDT",
                100.9,
                1_000_000_000,
                {"5m": (previous, current)},
            )
        )

        self.assertEqual(strategy.name, "long_p")
        self.assertEqual(signal.side, "LONG")
        self.assertEqual(signal.timeframe, "5m")

    def test_all_matching_timeframes_emit_only_one_signal_per_candle(self):
        previous = Candle(1, 98.0, 100.0, 96.0, 98.0)
        current = Candle(2, 98.3, 100.9, 97.0, 97.1)
        market = MarketSnapshot(
            "BTCUSDT",
            97.1,
            1_000_000_000,
            {"1m": (previous, current), "3m": (previous, current)},
        )
        strategy = StrategyFactory.create("short_p", SUPPORTED_CANDLE_INTERVALS)

        signal = strategy.evaluate(market)
        repeated_signal = strategy.evaluate(market)

        self.assertEqual(signal.timeframe, "1m")
        self.assertEqual(repeated_signal.action, SignalAction.HOLD)


class SimulatorTests(unittest.TestCase):
    def test_long_position_closes_and_records_fees_and_profit(self):
        simulator = Simulator(starting_balance=1000, order_notional=100, fee_rate=0)
        long_signal = StrategySignal("LONG", "long")
        flat_signal = StrategySignal(None, "flat")

        simulator.update(long_signal, 100)
        position = simulator.position
        self.assertIsNotNone(position)
        assert position is not None
        self.assertEqual(position.side, "LONG")
        simulator.update(flat_signal, 110)

        self.assertIsNone(simulator.position)
        self.assertAlmostEqual(simulator.realized_pnl, 10.0)
        self.assertAlmostEqual(simulator.balance, 1010.0)

    def test_short_unrealized_profit_moves_opposite_to_price(self):
        simulator = Simulator(order_notional=100, fee_rate=0)
        short_signal = StrategySignal("SHORT", "short")

        simulator.update(short_signal, 100)

        self.assertAlmostEqual(simulator.unrealized_pnl(90), 10.0)


if __name__ == "__main__":
    unittest.main()
