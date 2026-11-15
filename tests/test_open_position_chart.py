import os
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QPushButton

from app.backend.positions import PositionSnapshot
from app.frontend.chart_window import ChartWindow
from app.frontend.main_window import MainWindow


class OpenPositionChartTests(unittest.TestCase):
    def test_chart_window_offers_short_intervals_and_keeps_default(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        with patch.object(ChartWindow, "_start_worker"):
            window = ChartWindow("BTCUSDT")
            try:
                intervals = [
                    window.interval_picker.itemText(index)
                    for index in range(window.interval_picker.count())
                ]
                self.assertEqual(
                    intervals, ["1m", "3m", "5m", "15m", "1h", "4h", "1d"]
                )
                self.assertEqual(window.interval_picker.currentText(), "1h")
            finally:
                window.close()
        app.processEvents()

    def test_chart_button_opens_and_reuses_symbol_chart(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        with (
            patch.object(MainWindow, "_append_console"),
            patch.object(MainWindow, "_notify_telegram"),
            patch("app.frontend.main_window.ChartWindow") as chart_window_class,
        ):
            window = MainWindow()
            try:
                window.set_positions(
                    [PositionSnapshot("BTCUSDT", "LONG", 100, 101, 1)]
                )
                chart_button = window.positions_table.cellWidget(0, 10)

                self.assertIsInstance(chart_button, QPushButton)
                chart_button.click()
                chart_window_class.assert_called_once_with(
                    "BTCUSDT", window, window.theme_manager
                )
                chart_button.click()
                chart_window_class.return_value.showNormal.assert_called_once()
            finally:
                window.close()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
