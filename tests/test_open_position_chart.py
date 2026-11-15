import os
import unittest
from unittest.mock import patch

from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QPushButton

from app.backend.market_data import Candle
from app.backend.positions import PositionSnapshot
from app.frontend.chart_window import CandlestickChart, ChartWindow
from app.frontend.main_window import MainWindow


class OpenPositionChartTests(unittest.TestCase):
    def test_chart_computes_and_renders_ema_and_bollinger_overlays(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        chart = CandlestickChart()
        chart.resize(900, 500)
        chart.set_candles(
            [
                Candle(index * 60_000, 100 + index, 102 + index, 98 + index, 101 + index)
                for index in range(25)
            ]
        )

        self.assertIsNone(chart.ema_values[18])
        self.assertIsNotNone(chart.ema_values[19])
        self.assertIsNotNone(chart.bb_upper[19])
        self.assertIsNotNone(chart.bb_middle[19])
        self.assertIsNotNone(chart.bb_lower[19])
        chart.render(QPixmap(chart.size()))
        app.processEvents()

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
                chart_button = window.positions_table.cellWidget(0, 11)

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

    def test_positions_rank_by_favorable_closed_candle_wick_ratio(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        with (
            patch.object(MainWindow, "_append_console"),
            patch.object(MainWindow, "_notify_telegram"),
        ):
            window = MainWindow()
            try:
                window.set_positions(
                    [
                        PositionSnapshot(
                            "ETHUSDT",
                            "SHORT",
                            100,
                            99,
                            1,
                            strategy_name="short_p",
                            timeframe="1m",
                            wick_body_ratio=1.5,
                        ),
                        PositionSnapshot(
                            "BTCUSDT",
                            "LONG",
                            100,
                            101,
                            1,
                            strategy_name="long_p",
                            timeframe="5m",
                            wick_body_ratio=3.25,
                        ),
                        PositionSnapshot("XRPUSDT", "LONG", 100, 99, 1),
                    ]
                )

                self.assertEqual(
                    window.positions_table.item(0, 0).text(), "BTCUSDT"
                )
                self.assertEqual(
                    window.positions_table.item(0, 10).text(), "3.25x"
                )
                self.assertEqual(
                    window.positions_table.item(1, 0).text(), "ETHUSDT"
                )
                self.assertEqual(
                    window.positions_table.item(2, 0).text(), "XRPUSDT"
                )
                self.assertEqual(
                    window.positions_table.item(2, 10).text(), "—"
                )
            finally:
                window.close()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
