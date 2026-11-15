import unittest

from app.backend.market_data import Candle
from app.backend.simulator import Simulator
from app.backend.technical_indicators import BOLLINGER_PERIOD
from app.backend.strategy import (
    MarketSnapshot,
    NoStrategy,
    SignalAction,
    StrategySignal,
    StrategyFactory,
    SUPPORTED_CANDLE_INTERVALS,
)


def _with_indicator_history(previous, current):
    padding_count = BOLLINGER_PERIOD - 1
    padding = tuple(
        Candle(previous.open_time - padding_count + index, 100, 100, 100, 100)
        for index in range(padding_count)
    )
    return padding + (previous, current)


class StrategyTests(unittest.TestCase):
    def test_placeholder_strategy_never_generates_a_trade_signal(self):
        strategy = NoStrategy()
        signal = strategy.evaluate(MarketSnapshot("BTCUSDT", 100.0, 1_000_000_000.0))

        self.assertEqual(strategy.name, "Chưa cấu hình")
        self.assertIsNone(signal.side)
        self.assertEqual(signal.action, SignalAction.HOLD)

    def test_short_p_detects_upper_wick_on_current_candle_and_emits_once(self):
        previous = Candle(1, 98.0, 100.0, 96.0, 99.5)
        current = Candle(2, 100.0, 106.0, 98.0, 99.0)
        market = MarketSnapshot(
            "BTCUSDT",
            99.0,
            1_000_000_000,
            {"1m": _with_indicator_history(previous, current)},
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
                "BTCUSDT",
                99.9,
                1_000_000_000,
                {"1m": _with_indicator_history(previous, forming)},
            )
        )
        updated_signal = strategy.evaluate(
            MarketSnapshot(
                "BTCUSDT",
                97.1,
                1_000_000_000,
                {
                    "1m": _with_indicator_history(
                        previous, Candle(2, 98.3, 104.0, 97.0, 97.1)
                    )
                },
            )
        )

        self.assertEqual(initial_signal.action, SignalAction.HOLD)
        self.assertEqual(updated_signal.side, "SHORT")

    def test_short_p_requires_current_candle_to_be_red(self):
        previous = Candle(1, 98.0, 100.0, 96.0, 98.0)
        current = Candle(2, 95.8, 100.0, 95.7, 96.0)

        signal = StrategyFactory.create("short_p", ("1m",)).evaluate(
            MarketSnapshot(
                "BTCUSDT",
                96.0,
                1_000_000_000,
                {"1m": _with_indicator_history(previous, current)},
            )
        )

        self.assertEqual(signal.action, SignalAction.HOLD)
        self.assertIsNone(signal.side)

    def test_short_p_allows_high_more_than_one_percent_above_previous_high(self):
        previous = Candle(1, 98.0, 100.0, 96.0, 98.0)
        current = Candle(2, 98.3, 102.0, 97.0, 97.1)

        signal = StrategyFactory.create("short_p", ("1m",)).evaluate(
            MarketSnapshot(
                "BTCUSDT",
                97.1,
                1_000_000_000,
                {"1m": _with_indicator_history(previous, current)},
            )
        )

        self.assertEqual(signal.action, SignalAction.SET_POSITION)
        self.assertEqual(signal.side, "SHORT")

    def test_short_p_requires_lower_wick_not_to_exceed_body(self):
        previous = Candle(1, 98.0, 100.0, 96.0, 100.0)
        current = Candle(2, 100.0, 106.0, 97.0, 99.0)

        signal = StrategyFactory.create("short_p", ("1m",)).evaluate(
            MarketSnapshot(
                "BTCUSDT",
                99.0,
                1_000_000_000,
                {"1m": _with_indicator_history(previous, current)},
            )
        )

        self.assertEqual(signal.action, SignalAction.HOLD)
        self.assertIsNone(signal.side)

    def test_short_p_requires_sweeping_previous_high(self):
        previous = Candle(1, 98.0, 106.0, 96.0, 100.0)
        current = Candle(2, 100.0, 106.0, 98.0, 99.0)

        signal = StrategyFactory.create("short_p", ("1m",)).evaluate(
            MarketSnapshot(
                "BTCUSDT",
                99.0,
                1_000_000_000,
                {"1m": _with_indicator_history(previous, current)},
            )
        )

        self.assertEqual(signal.action, SignalAction.HOLD)
        self.assertIsNone(signal.side)

    def test_short_p_requires_current_price_below_previous_close(self):
        previous = Candle(1, 98.0, 100.0, 96.0, 99.0)
        current = Candle(2, 100.0, 106.0, 98.0, 99.0)

        signal = StrategyFactory.create("short_p", ("1m",)).evaluate(
            MarketSnapshot(
                "BTCUSDT",
                99.0,
                1_000_000_000,
                {"1m": _with_indicator_history(previous, current)},
            )
        )

        self.assertEqual(signal.action, SignalAction.HOLD)
        self.assertIsNone(signal.side)

    def test_short_p_requires_price_to_rise_above_open_before_turning_red(self):
        previous = Candle(1, 98.0, 100.0, 96.0, 98.0)
        current = Candle(2, 100.0, 100.0, 99.0, 99.0)

        signal = StrategyFactory.create("short_p", ("1m",)).evaluate(
            MarketSnapshot(
                "BTCUSDT",
                99.0,
                1_000_000_000,
                {"1m": _with_indicator_history(previous, current)},
            )
        )

        self.assertEqual(signal.action, SignalAction.HOLD)
        self.assertIsNone(signal.side)

    def test_short_p_requires_current_or_previous_candle_to_touch_upper_band(self):
        previous = Candle(1, 99.5, 99.9, 99.0, 99.8)
        current = Candle(2, 99.9, 100.0, 99.0, 99.0)

        signal = StrategyFactory.create("short_p", ("1m",)).evaluate(
            MarketSnapshot(
                "BTCUSDT",
                99.0,
                1_000_000_000,
                {"1m": _with_indicator_history(previous, current)},
            )
        )

        self.assertEqual(signal.action, SignalAction.HOLD)
        self.assertIsNone(signal.side)

    def test_short_p_accepts_upper_band_touch_by_previous_closed_candle(self):
        previous = Candle(1, 99.5, 100.1, 99.0, 99.8)
        current = Candle(2, 100.1, 100.15, 99.6, 99.6)

        signal = StrategyFactory.create("short_p", ("1m",)).evaluate(
            MarketSnapshot(
                "BTCUSDT",
                99.6,
                1_000_000_000,
                {"1m": _with_indicator_history(previous, current)},
            )
        )

        self.assertEqual(signal.side, "SHORT")

    def test_long_p_requires_sweeping_previous_low_and_closing_near_high(self):
        previous = Candle(1, 100.5, 102.0, 100.0, 100.8)
        current = Candle(2, 100.5, 101.0, 98.5, 100.9)
        strategy = StrategyFactory.create("long_p", ("5m",))

        signal = strategy.evaluate(
            MarketSnapshot(
                "BTCUSDT",
                100.9,
                1_000_000_000,
                {"5m": _with_indicator_history(previous, current)},
            )
        )

        self.assertEqual(strategy.name, "long_p")
        self.assertEqual(signal.side, "LONG")
        self.assertEqual(signal.timeframe, "5m")

    def test_long_p_requires_current_price_above_previous_close(self):
        previous = Candle(1, 100.5, 102.0, 100.0, 101.0)
        current = Candle(2, 100.5, 101.0, 98.5, 100.9)

        signal = StrategyFactory.create("long_p", ("5m",)).evaluate(
            MarketSnapshot(
                "BTCUSDT",
                100.9,
                1_000_000_000,
                {"5m": _with_indicator_history(previous, current)},
            )
        )

        self.assertEqual(signal.action, SignalAction.HOLD)
        self.assertIsNone(signal.side)

    def test_long_p_requires_sweeping_previous_low(self):
        previous = Candle(1, 100.5, 102.0, 98.0, 100.8)
        current = Candle(2, 100.5, 101.0, 98.5, 100.9)

        signal = StrategyFactory.create("long_p", ("5m",)).evaluate(
            MarketSnapshot(
                "BTCUSDT",
                100.9,
                1_000_000_000,
                {"5m": _with_indicator_history(previous, current)},
            )
        )

        self.assertEqual(signal.action, SignalAction.HOLD)
        self.assertIsNone(signal.side)

    def test_long_p_requires_upper_wick_not_to_exceed_body(self):
        previous = Candle(1, 100.5, 102.0, 100.0, 100.8)
        current = Candle(2, 100.5, 101.5, 98.5, 100.9)

        signal = StrategyFactory.create("long_p", ("5m",)).evaluate(
            MarketSnapshot(
                "BTCUSDT",
                100.9,
                1_000_000_000,
                {"5m": _with_indicator_history(previous, current)},
            )
        )

        self.assertEqual(signal.action, SignalAction.HOLD)
        self.assertIsNone(signal.side)

    def test_long_p_requires_current_or_previous_candle_to_touch_lower_band(self):
        previous = Candle(1, 100.2, 101.0, 100.1, 100.2)
        current = Candle(2, 100.1, 100.9, 100.0, 100.9)

        signal = StrategyFactory.create("long_p", ("5m",)).evaluate(
            MarketSnapshot(
                "BTCUSDT",
                100.9,
                1_000_000_000,
                {"5m": _with_indicator_history(previous, current)},
            )
        )

        self.assertEqual(signal.action, SignalAction.HOLD)
        self.assertIsNone(signal.side)

    def test_long_p_rechecks_current_candle_until_pattern_forms(self):
        previous = Candle(1, 100.5, 102.0, 100.0, 101.0)
        strategy = StrategyFactory.create("long_p", ("5m",))

        initial_signal = strategy.evaluate(
            MarketSnapshot(
                "BTCUSDT",
                101.6,
                1_000_000_000,
                {
                    "5m": _with_indicator_history(
                        previous, Candle(2, 100.8, 101.5, 100.2, 101.6)
                    )
                },
            )
        )
        updated_signal = strategy.evaluate(
            MarketSnapshot(
                "BTCUSDT",
                101.9,
                1_000_000_000,
                {
                    "5m": _with_indicator_history(
                        previous, Candle(2, 100.8, 102.0, 98.5, 101.9)
                    )
                },
            )
        )
        repeated_signal = strategy.evaluate(
            MarketSnapshot(
                "BTCUSDT",
                101.9,
                1_000_000_000,
                {
                    "5m": _with_indicator_history(
                        previous, Candle(2, 100.8, 102.0, 98.5, 101.9)
                    )
                },
            )
        )

        self.assertEqual(initial_signal.action, SignalAction.HOLD)
        self.assertEqual(updated_signal.side, "LONG")
        self.assertEqual(updated_signal.timeframe, "5m")
        self.assertEqual(repeated_signal.action, SignalAction.HOLD)

    def test_all_matching_timeframes_emit_only_one_signal_per_candle(self):
        previous = Candle(1, 98.0, 100.0, 96.0, 98.0)
        current = Candle(2, 98.3, 104.0, 97.0, 97.1)
        market = MarketSnapshot(
            "BTCUSDT",
            97.1,
            1_000_000_000,
            {
                "1m": _with_indicator_history(previous, current),
                "3m": _with_indicator_history(previous, current),
            },
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
