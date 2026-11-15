import os
import unittest
from unittest.mock import patch

from app.backend.execution import SimulationExecutionMode
from app.backend.market_data import Candle
from app.backend.positions import PositionSnapshot
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

    def test_worker_fetches_candles_for_each_selected_interval(self):
        with patch.dict(os.environ, {}, clear=True):
            worker = MarketScannerWorker(
                mode="simulation",
                candle_intervals=("1m", "15m"),
            )
        candle = Candle(1, 100, 101, 99, 100)
        candle_cache = {}

        with patch.object(
            worker._market_data, "get_closed_candles", return_value=[candle]
        ) as get_closed_candles:
            worker._refresh_candles(
                [{"symbol": "BTCUSDT"}], candle_cache, ("1m", "15m")
            )

        self.assertEqual(get_closed_candles.call_count, 2)
        self.assertTrue(
            all(call.args[2] == 3 for call in get_closed_candles.call_args_list)
        )
        self.assertEqual(candle_cache["BTCUSDT"]["1m"], [candle])
        self.assertEqual(candle_cache["BTCUSDT"]["15m"], [candle])

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
