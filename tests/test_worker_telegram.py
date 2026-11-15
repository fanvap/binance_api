import os
import unittest
from unittest.mock import patch

from app.backend.execution import SimulationExecutionMode
from app.backend.market_data import Candle
from app.backend.positions import PositionSnapshot
from app.backend.technical_indicators import BOLLINGER_PERIOD
from app.backend.strategy import (
    MarketSnapshot,
    SignalAction,
    StrategySignal,
)
from app.backend.telegram import TelegramCommand
from app.frontend.worker import MarketScannerWorker


class TelegramWorkerIntegrationTests(unittest.TestCase):
    def test_simulation_session_enables_configured_telegram_notifications(self):
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": "test-token",
                "TELEGRAM_ALLOWED_USER_IDS": "42",
            },
            clear=True,
        ):
            worker = MarketScannerWorker(mode="simulation")

        self.assertIsNotNone(worker._telegram_worker)
        self.assertEqual(
            worker._telegram_worker.client.allowed_user_ids,
            frozenset({42}),
        )
        if not isinstance(worker._execution_mode, SimulationExecutionMode):
            self.fail("Simulation worker did not create its virtual executor.")
        self.assertEqual(worker._execution_mode.starting_balance, 1000.0)
        self.assertEqual(worker._execution_mode.order_margin, 50.0)
        self.assertEqual(worker._execution_mode.order_notional, 100.0)
        self.assertEqual(worker._execution_mode.max_open_positions, 20)

    def test_simulation_pnl_is_sent_to_telegram(self):
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": "test-token",
                "TELEGRAM_ALLOWED_USER_IDS": "42",
            },
            clear=True,
        ):
            worker = MarketScannerWorker(mode="simulation")

        with patch.object(
            worker._execution_mode,
            "positions",
            return_value=[PositionSnapshot("BTCUSDT", "LONG", 100, 101, 1)],
        ):
            with patch.object(worker._telegram_worker, "broadcast") as broadcast:
                worker._send_periodic_telegram_pnl()

        self.assertIn("Simulation PnL chưa chốt", broadcast.call_args.args[0])

    def test_simulation_telegram_open_command_uses_the_virtual_executor(self):
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": "test-token",
                "TELEGRAM_ALLOWED_USER_IDS": "42",
            },
            clear=True,
        ):
            worker = MarketScannerWorker(mode="simulation")

        telegram_worker = worker._telegram_worker
        if telegram_worker is None:
            self.fail("Telegram worker was not configured.")
        worker._eligible_symbols = {"BTCUSDT"}
        worker._execution_mode.update(
            MarketSnapshot("BTCUSDT", 100, 1_000_000_000),
            StrategySignal(None, "hold", SignalAction.HOLD),
        )
        telegram_worker.commands.put(
            TelegramCommand(42, "/open BTCUSDT LONG MARKET")
        )

        with patch.object(telegram_worker, "broadcast") as broadcast:
            worker._process_telegram_commands()

        self.assertEqual(
            [position.symbol for position in worker._execution_mode.positions()],
            ["BTCUSDT"],
        )
        message = broadcast.call_args.args[0]
        self.assertIn("🟢 LONG", message)
        self.assertIn("SIMULATION: mở LONG BTCUSDT", message)
        self.assertIn("ký quỹ 50.00 USDT", message)
        self.assertIn("vị thế 100.00 USDT", message)

    def test_simulation_account_summary_is_emitted_for_the_dashboard(self):
        with patch.dict(os.environ, {}, clear=True):
            worker = MarketScannerWorker(mode="simulation")
        summaries = []
        worker.account_summary_received.connect(summaries.append)

        worker._send_account_summary()

        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["wallet_balance"], 1000)
        self.assertEqual(summaries[0]["available_balance"], 1000)
        self.assertEqual(summaries[0]["equity"], 1000)

    def test_worker_emits_binance_api_latency_as_network_quality(self):
        with patch.dict(os.environ, {}, clear=True):
            worker = MarketScannerWorker(mode="simulation")
        observed = []
        worker.network_quality_received.connect(observed.append)

        with (
            patch.object(
                worker._market_data, "get_all_prices", return_value={"BTCUSDT": 1}
            ),
            patch("app.frontend.worker.time.monotonic", side_effect=[10.0, 10.245]),
        ):
            prices = worker._get_prices_with_network_quality()

        self.assertEqual(prices, {"BTCUSDT": 1})
        self.assertAlmostEqual(observed[0], 245.0)

    def test_worker_reports_network_failure_when_price_request_fails(self):
        with patch.dict(os.environ, {}, clear=True):
            worker = MarketScannerWorker(mode="simulation")
        observed = []
        worker.network_quality_received.connect(observed.append)

        with patch.object(
            worker._market_data, "get_all_prices", side_effect=RuntimeError("offline")
        ):
            with self.assertRaisesRegex(RuntimeError, "offline"):
                worker._get_prices_with_network_quality()

        self.assertEqual(observed, [None])

    def test_worker_fetches_candles_for_each_selected_interval(self):
        with patch.dict(os.environ, {}, clear=True):
            worker = MarketScannerWorker(
                mode="simulation",
                candle_intervals=("1m", "15m"),
            )
        candles = [
            Candle(1, 100, 101, 99, 100),
            Candle(2, 100, 102, 99, 101),
        ]
        candle_cache = {}

        with patch.object(
            worker._market_data, "get_candles", return_value=candles
        ) as get_candles:
            worker._refresh_candles(
                [{"symbol": "BTCUSDT"}], candle_cache, ("1m", "15m")
            )

        self.assertEqual(get_candles.call_count, 2)
        self.assertTrue(
            all(
                call.args[2] == BOLLINGER_PERIOD + 1
                for call in get_candles.call_args_list
            )
        )
        self.assertEqual(candle_cache["BTCUSDT"]["1m"], candles)
        self.assertEqual(candle_cache["BTCUSDT"]["15m"], candles)

    def test_worker_updates_active_candle_and_ignores_stale_candle_pairs(self):
        with patch.dict(os.environ, {}, clear=True):
            worker = MarketScannerWorker(
                mode="simulation",
                candle_intervals=("1m",),
            )
        previous = Candle(60_000, 99, 100, 98, 99, close_time=119_999)
        active = Candle(120_000, 99, 101, 98, 100, close_time=179_999)
        candle_cache = {"BTCUSDT": {"1m": [previous, active]}}

        worker._update_live_candles(
            candle_cache, {"BTCUSDT": 103}, current_time_ms=125_000
        )

        self.assertEqual(candle_cache["BTCUSDT"]["1m"][0], previous)
        updated = candle_cache["BTCUSDT"]["1m"][-1]
        self.assertEqual((updated.high, updated.low, updated.close), (103, 98, 103))
        self.assertEqual(
            worker._current_candle_pair(
                candle_cache, "BTCUSDT", "1m", current_time_ms=125_000
            ),
            (previous, updated),
        )
        self.assertEqual(
            worker._current_candle_pair(
                candle_cache, "BTCUSDT", "1m", current_time_ms=180_000
            ),
            (),
        )

    def test_worker_checks_opposite_strategy_for_open_position_protection(self):
        with patch.dict(os.environ, {}, clear=True):
            worker = MarketScannerWorker(
                mode="simulation",
                strategy_name="long_p",
                candle_intervals=("1m",),
            )
        market = MarketSnapshot(
            "BTCUSDT",
            98.5,
            1_000_000_000,
            {
                "1m": (
                    *(
                        Candle(index, 100, 100, 100, 100)
                        for index in range(BOLLINGER_PERIOD - 1)
                    ),
                    Candle(60_000, 100, 103, 98, 100),
                    Candle(120_000, 100, 104, 97.5, 98.5),
                )
            },
        )
        position = PositionSnapshot(
            "BTCUSDT",
            "LONG",
            100,
            98.5,
            1,
            strategy_name="long_p",
            timeframe="1m",
        )

        signal = worker._evaluate_market_signal(market, position)

        self.assertEqual(signal.side, "SHORT")
        self.assertEqual(signal.strategy_name, "short_p")
        self.assertEqual(signal.timeframe, "1m")
        self.assertEqual(
            worker._evaluate_market_signal(market).action,
            SignalAction.HOLD,
        )

    def test_worker_calculates_directional_ratio_from_last_closed_candle(self):
        with patch.dict(os.environ, {}, clear=True):
            worker = MarketScannerWorker(
                mode="simulation",
                candle_intervals=("1m",),
            )
        previous = Candle(60_000, 100, 102, 96, 99)
        active = Candle(120_000, 99, 101, 98, 100)
        cache = {"BTCUSDT": {"1m": [previous, active]}}
        long_position = PositionSnapshot(
            "BTCUSDT", "LONG", 100, 100, 1, timeframe="1m"
        )
        short_position = PositionSnapshot(
            "BTCUSDT", "SHORT", 100, 100, 1, timeframe="1m"
        )

        self.assertEqual(
            worker._closed_candle_wick_body_ratio(
                long_position, cache, current_time_ms=130_000
            ),
            3,
        )
        self.assertEqual(
            worker._closed_candle_wick_body_ratio(
                short_position, cache, current_time_ms=130_000
            ),
            2,
        )
        self.assertIsNone(
            worker._closed_candle_wick_body_ratio(
                PositionSnapshot("ETHUSDT", "LONG", 100, 100, 1),
                cache,
                current_time_ms=130_000,
            )
        )

    def test_strategy_trade_telegram_message_names_strategy_and_timeframe(self):
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": "test-token",
                "TELEGRAM_ALLOWED_USER_IDS": "42",
            },
            clear=True,
        ):
            worker = MarketScannerWorker(mode="simulation")

        signal = StrategySignal(
            "SHORT",
            "short_p pattern",
            SignalAction.SET_POSITION,
            "3m",
        )
        with patch.object(worker._telegram_worker, "broadcast") as broadcast:
            worker._broadcast_execution_event(
                "SIMULATION: mở SHORT BTCUSDT với notional 50.00 USDT.",
                signal,
            )

        message = broadcast.call_args.args[0]
        self.assertTrue(message.startswith("🔴 SHORT"))
        self.assertIn("Chiến thuật: short_p", message)
        self.assertIn("khung nến: 3m", message)


if __name__ == "__main__":
    unittest.main()
