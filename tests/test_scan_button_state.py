import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.frontend.main_window import MainWindow


class ScanButtonStateTests(unittest.TestCase):
    def test_scanning_state_changes_button_text_and_color_then_resets(self):
        start_button = Mock()
        window = SimpleNamespace(
            start_button=start_button,
            stop_button=Mock(),
            mode_selector=Mock(),
            order_margin_input=Mock(),
            max_positions_input=Mock(),
            take_profit_input=Mock(),
            stop_loss_input=Mock(),
            strategy_selector=Mock(),
            candle_interval_checkboxes={
                interval: Mock() for interval in ("1m", "3m", "5m", "15m", "1h", "4h")
            },
        )

        MainWindow._set_running(window, True)

        start_button.setText.assert_called_with("●  ĐANG QUÉT")
        self.assertIn("#0ecb81", start_button.setStyleSheet.call_args.args[0])
        window.stop_button.setEnabled.assert_called_with(True)

        MainWindow._set_running(window, False)

        start_button.setText.assert_called_with("▶   Bắt đầu quét")
        start_button.setStyleSheet.assert_called_with("")
        window.stop_button.setEnabled.assert_called_with(False)

    def test_scan_event_notice_includes_mode_and_timestamp(self):
        notifier = Mock()
        mode_selector = Mock()
        mode_selector.currentData.return_value = "testnet"
        strategy_selector = Mock()
        strategy_selector.currentData.return_value = "long_p"
        interval_checkboxes = {
            interval: Mock()
            for interval in ("1m", "3m", "5m", "15m", "1h", "4h")
        }
        for interval in ("1m", "3m", "5m", "15m", "1h", "4h"):
            interval_checkboxes[interval].isChecked.return_value = True
        order_margin_input = Mock()
        order_margin_input.value.return_value = 50
        window = SimpleNamespace(
            mode_selector=mode_selector,
            strategy_selector=strategy_selector,
            candle_interval_checkboxes=interval_checkboxes,
            order_margin_input=order_margin_input,
            _selected_candle_intervals=lambda: [
                interval
                for interval, checkbox in interval_checkboxes.items()
                if checkbox.isChecked()
            ],
            _notify_telegram=notifier,
        )

        MainWindow._notify_scan_event(window, "Bắt đầu quét")

        message = notifier.call_args.args[0]
        self.assertIn("Bắt đầu quét", message)
        self.assertIn("Chế độ: Binance Futures Testnet", message)
        self.assertIn("Chiến thuật: long_p", message)
        self.assertIn("Khung nến đang bật: 1m, 3m, 5m, 15m, 1h, 4h", message)
        self.assertIn("Thời gian:", message)


if __name__ == "__main__":
    unittest.main()
