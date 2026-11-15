"""Binance-style candlestick chart window for a Futures symbol."""

import threading
from datetime import datetime
from typing import List, Optional

from PyQt6.QtCore import QThread, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.backend.market_data import BinanceMarketDataClient, Candle
from app.frontend.theme import THEMES, ThemeManager, chart_stylesheet


class ChartWorker(QThread):
    candles_received = pyqtSignal(object)
    error_received = pyqtSignal(str)

    def __init__(self, symbol: str, interval: str, parent=None) -> None:
        super().__init__(parent)
        self.symbol = symbol
        self.interval = interval
        self._stop_event = threading.Event()
        self._client = BinanceMarketDataClient()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                candles = self._client.get_candles(self.symbol, self.interval, 100)
                self.candles_received.emit(candles)
            except Exception as exc:
                self.error_received.emit(str(exc))
            self._stop_event.wait(15)


class CandlestickChart(QWidget):
    cursor_changed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.candles: List[Candle] = []
        self.crosshair_position: Optional[QPointF] = None
        self.colors = THEMES["dark"]
        self.setMouseTracking(True)
        self.setMinimumSize(640, 380)

    def set_theme(self, mode: str) -> None:
        self.colors = THEMES[mode]
        self.update()

    def set_candles(self, candles: List[Candle]) -> None:
        self.candles = candles
        self.crosshair_position = None
        if candles:
            self.cursor_changed.emit(self._ohlc_text(candles[-1]))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(self.colors["chart_bg"]))
        if not self.candles:
            painter.setPen(QColor(self.colors["chart_axis"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Đang tải dữ liệu nến...")
            return

        left = 18.0
        right_axis_width = 88.0
        plot_width = self.width() - left - right_axis_width
        axis_bottom = self.height() - 28.0
        volume_height = max(48.0, self.height() * 0.18)
        volume_top = axis_bottom - volume_height
        price_bottom = volume_top - 23.0
        plot = QRectF(left, 34.0, plot_width, price_bottom - 34.0)
        volume_plot = QRectF(left, volume_top, plot_width, volume_height)
        if plot.width() <= 0 or plot.height() <= 0:
            return

        highest = max(candle.high for candle in self.candles)
        lowest = min(candle.low for candle in self.candles)
        if highest == lowest:
            padding = max(abs(highest) * 0.005, 0.01)
            highest += padding
            lowest -= padding
        price_range = highest - lowest

        self._draw_grid(painter, plot, highest, lowest)
        self._draw_volume_grid(painter, volume_plot)
        self._draw_candles(painter, plot, highest, lowest, price_range)
        self._draw_volume(painter, volume_plot)
        self._draw_time_axis(painter, plot, axis_bottom)
        self._draw_current_price(painter, plot, lowest, price_range)
        self._draw_crosshair(painter, plot, volume_plot, axis_bottom, highest, lowest, price_range)

    def _draw_grid(self, painter: QPainter, plot: QRectF, highest: float, lowest: float) -> None:
        painter.setFont(self.font())
        for index in range(6):
            ratio = index / 5
            y = plot.top() + plot.height() * ratio
            price = highest - (highest - lowest) * ratio
            painter.setPen(QPen(QColor(self.colors["chart_grid"]), 1))
            painter.drawLine(int(plot.left()), int(y), int(plot.right()), int(y))
            painter.setPen(QColor(self.colors["chart_axis"]))
            painter.drawText(
                QRectF(plot.right() + 8, y - 9, 76, 18),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"{price:.6g}",
            )

    def _draw_volume_grid(self, painter: QPainter, volume_plot: QRectF) -> None:
        painter.setPen(QPen(QColor(self.colors["chart_grid"]), 1))
        painter.drawLine(
            int(volume_plot.left()),
            int(volume_plot.top()),
            int(volume_plot.right()),
            int(volume_plot.top()),
        )
        painter.setPen(QColor(self.colors["chart_axis"]))
        painter.drawText(
            QRectF(volume_plot.left(), volume_plot.top() - 19, 90, 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "VOL (USDT)",
        )

    def _draw_candles(
        self,
        painter: QPainter,
        plot: QRectF,
        highest: float,
        lowest: float,
        price_range: float,
    ) -> None:
        count = len(self.candles)
        slot_width = plot.width() / count
        body_width = max(2.0, min(12.0, slot_width * 0.68))
        for index, candle in enumerate(self.candles):
            x = plot.left() + slot_width * (index + 0.5)
            y_high = self._price_y(candle.high, lowest, price_range, plot)
            y_low = self._price_y(candle.low, lowest, price_range, plot)
            y_open = self._price_y(candle.open, lowest, price_range, plot)
            y_close = self._price_y(candle.close, lowest, price_range, plot)
            color = QColor("#0ecb81") if candle.close >= candle.open else QColor("#f6465d")
            painter.setPen(QPen(color, 1))
            painter.drawLine(int(x), int(y_high), int(x), int(y_low))
            body_top = min(y_open, y_close)
            body_height = max(1.0, abs(y_close - y_open))
            painter.fillRect(
                QRectF(x - body_width / 2, body_top, body_width, body_height),
                color,
            )

    def _draw_volume(self, painter: QPainter, volume_plot: QRectF) -> None:
        max_volume = max((candle.volume for candle in self.candles), default=0.0)
        if max_volume <= 0:
            return
        slot_width = volume_plot.width() / len(self.candles)
        bar_width = max(2.0, min(12.0, slot_width * 0.68))
        for index, candle in enumerate(self.candles):
            bar_height = (candle.volume / max_volume) * (volume_plot.height() - 3)
            x = volume_plot.left() + slot_width * (index + 0.5)
            color = QColor("#0ecb81") if candle.close >= candle.open else QColor("#f6465d")
            color.setAlpha(125)
            painter.fillRect(
                QRectF(
                    x - bar_width / 2,
                    volume_plot.bottom() - bar_height,
                    bar_width,
                    bar_height,
                ),
                color,
            )

    def _draw_time_axis(self, painter: QPainter, plot: QRectF, axis_bottom: float) -> None:
        count = len(self.candles)
        slot_width = plot.width() / count
        tick_indexes = sorted({0, count // 4, count // 2, (count * 3) // 4, count - 1})
        painter.setPen(QColor(self.colors["chart_axis"]))
        for index in tick_indexes:
            x = plot.left() + slot_width * (index + 0.5)
            timestamp = datetime.fromtimestamp(self.candles[index].open_time / 1000)
            painter.drawText(
                QRectF(x - 45, axis_bottom + 5, 90, 18),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                timestamp.strftime("%m-%d %H:%M"),
            )

    def _draw_current_price(
        self,
        painter: QPainter,
        plot: QRectF,
        lowest: float,
        price_range: float,
    ) -> None:
        latest = self.candles[-1]
        y = self._price_y(latest.close, lowest, price_range, plot)
        color = QColor("#0ecb81") if latest.close >= latest.open else QColor("#f6465d")
        pen = QPen(color, 1, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(int(plot.left()), int(y), int(plot.right()), int(y))
        painter.fillRect(QRectF(plot.right() + 3, y - 10, 82, 20), color)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(
            QRectF(plot.right() + 5, y - 10, 78, 20),
            Qt.AlignmentFlag.AlignCenter,
            f"{latest.close:.6g}",
        )

    def _draw_crosshair(
        self,
        painter: QPainter,
        plot: QRectF,
        volume_plot: QRectF,
        axis_bottom: float,
        highest: float,
        lowest: float,
        price_range: float,
    ) -> None:
        position = self.crosshair_position
        if position is None or not plot.contains(position):
            return
        count = len(self.candles)
        slot_width = plot.width() / count
        candle_index = min(count - 1, int((position.x() - plot.left()) / slot_width))
        candle = self.candles[candle_index]
        price = highest - ((position.y() - plot.top()) / plot.height()) * price_range

        painter.setPen(
            QPen(QColor(self.colors["chart_crosshair"]), 1, Qt.PenStyle.DotLine)
        )
        painter.drawLine(
            int(position.x()),
            int(plot.top()),
            int(position.x()),
            int(volume_plot.bottom()),
        )
        painter.drawLine(
            int(plot.left()),
            int(position.y()),
            int(plot.right()),
            int(position.y()),
        )

        painter.fillRect(
            QRectF(plot.right() + 3, position.y() - 10, 82, 20),
            QColor(self.colors["chart_badge"]),
        )
        painter.setPen(QColor(self.colors["chart_text"]))
        painter.drawText(
            QRectF(plot.right() + 5, position.y() - 10, 78, 20),
            Qt.AlignmentFlag.AlignCenter,
            f"{price:.6g}",
        )
        timestamp = datetime.fromtimestamp(candle.open_time / 1000)
        painter.fillRect(
            QRectF(position.x() - 48, axis_bottom + 4, 96, 20),
            QColor(self.colors["chart_badge"]),
        )
        painter.setPen(QColor(self.colors["chart_text"]))
        painter.drawText(
            QRectF(position.x() - 46, axis_bottom + 4, 92, 20),
            Qt.AlignmentFlag.AlignCenter,
            timestamp.strftime("%m-%d %H:%M"),
        )

    @staticmethod
    def _price_y(price: float, lowest: float, price_range: float, plot: QRectF) -> float:
        return plot.bottom() - ((price - lowest) / price_range) * plot.height()

    @staticmethod
    def _ohlc_text(candle: Candle) -> str:
        return (
            f"O {candle.open:.8g}    H {candle.high:.8g}    "
            f"L {candle.low:.8g}    C {candle.close:.8g}"
        )

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.crosshair_position = event.position()
        if self.candles:
            left = 18.0
            plot_width = self.width() - left - 88.0
            slot_width = plot_width / len(self.candles)
            index = int((event.position().x() - left) / slot_width)
            index = max(0, min(len(self.candles) - 1, index))
            self.cursor_changed.emit(self._ohlc_text(self.candles[index]))
        self.update()

    def leaveEvent(self, event) -> None:
        self.crosshair_position = None
        if self.candles:
            self.cursor_changed.emit(self._ohlc_text(self.candles[-1]))
        self.update()


class ChartWindow(QMainWindow):
    closed = pyqtSignal(str)

    def __init__(
        self,
        symbol: str,
        parent=None,
        theme_manager: Optional[ThemeManager] = None,
    ) -> None:
        super().__init__(parent)
        self.symbol = symbol
        self.theme_manager = theme_manager or ThemeManager()
        self._worker: Optional[ChartWorker] = None
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"{symbol} - Binance Futures Chart")
        self.resize(1100, 700)
        self._build_ui()
        self.theme_manager.mode_changed.connect(self._apply_theme)
        self._apply_theme(self.theme_manager.mode)
        self._start_worker()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        toolbar = QHBoxLayout()
        title = QLabel(f"{self.symbol}  /  USDT PERPETUAL")
        title.setObjectName("chartTitle")
        toolbar.addWidget(title)
        toolbar.addSpacing(18)
        self.ohlc_label = QLabel("O —    H —    L —    C —")
        self.ohlc_label.setObjectName("ohlcLabel")
        toolbar.addWidget(self.ohlc_label)
        toolbar.addStretch()
        toolbar.addWidget(QLabel("Interval"))
        self.interval_picker = QComboBox()
        self.interval_picker.addItems(["1m", "3m", "5m", "15m", "1h", "4h", "1d"])
        self.interval_picker.setCurrentText("1h")
        self.interval_picker.currentTextChanged.connect(
            lambda _interval: self._restart_worker()
        )
        toolbar.addWidget(self.interval_picker)
        self.refresh_button = QPushButton("Làm mới")
        self.refresh_button.clicked.connect(self._restart_worker)
        toolbar.addWidget(self.refresh_button)
        self.theme_button = QPushButton()
        self.theme_button.setObjectName("themeButton")
        self.theme_button.clicked.connect(self.theme_manager.toggle)
        toolbar.addWidget(self.theme_button)
        layout.addLayout(toolbar)

        self.chart = CandlestickChart()
        self.chart.cursor_changed.connect(self.ohlc_label.setText)
        layout.addWidget(self.chart, stretch=1)
        self.status_label = QLabel("Đang tải dữ liệu từ Binance Futures...")
        self.status_label.setObjectName("chartStatus")
        layout.addWidget(self.status_label)
        self.setCentralWidget(root)

    def _apply_theme(self, mode: str) -> None:
        self.setStyleSheet(chart_stylesheet(mode))
        self.chart.set_theme(mode)
        self.theme_button.setText("☀" if mode == "dark" else "☾")

    def _start_worker(self) -> None:
        worker = ChartWorker(self.symbol, self.interval_picker.currentText(), self)
        worker.candles_received.connect(self._update_chart)
        worker.error_received.connect(self._show_error)
        worker.start()
        self._worker = worker

    def _restart_worker(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(17000)
            if self._worker.isRunning():
                self.status_label.setText("Không thể dừng luồng tải nến trước đó.")
                return
            self._worker.deleteLater()
        self._worker = None
        self.status_label.setText("Đang tải dữ liệu từ Binance Futures...")
        self._start_worker()

    def _update_chart(self, candles: List[Candle]) -> None:
        self.chart.set_candles(candles)
        if candles:
            latest = candles[-1]
            self.status_label.setText(
                f"{len(candles)} nến  •  Đóng gần nhất: {latest.close:.8g} USDT  "
                f"•  Tự cập nhật mỗi 15 giây"
            )

    def _show_error(self, message: str) -> None:
        self.status_label.setText(f"Lỗi tải chart: {message}")

    def closeEvent(self, event) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(17000)
        self.closed.emit(self.symbol)
        event.accept()
