"""Dark terminal-style market dashboard."""

import sys
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.backend.app_log import AppLogWriter
from app.backend.execution import (
    MAX_ORDER_MARGIN,
    SIMULATION_MAX_OPEN_POSITIONS,
    SIMULATION_MAX_ORDER_MARGIN,
    SIMULATION_STARTING_BALANCE,
)
from app.backend.market_caps import MarketCapClient
from app.backend.positions import PositionSnapshot
from app.backend.telegram import TelegramClient, TelegramMessageNotifier
from app.backend.strategy import SUPPORTED_CANDLE_INTERVALS
from app.backend.trade_statistics import (
    StrategyTradeStatistics,
    StrategyTradeStatisticsRow,
)
from app.backend.trading_constants import TRADE_LEVERAGE
from app.frontend.chart_window import ChartWindow
from app.frontend.worker import MarketRow, MarketScannerWorker
from app.frontend.theme import ThemeManager, dashboard_stylesheet


class MainWindow(QMainWindow):
    def __init__(self, theme_manager: Optional[ThemeManager] = None) -> None:
        super().__init__()
        self.setWindowTitle("Binance Market Console")
        self.resize(1180, 780)
        self.setMinimumSize(900, 600)
        self.theme_manager = theme_manager or ThemeManager()
        self.worker: Optional[MarketScannerWorker] = None
        self._telegram_notifiers: List[TelegramMessageNotifier] = []
        self._close_after_telegram_notifications = False
        self._app_log = AppLogWriter(self._application_log_directory())
        self._app_log_error_reported = False
        self._network_quality_checked = False
        self._network_latency_ms: Optional[float] = None
        self.trade_statistics = StrategyTradeStatistics()
        self.market_caps_client = MarketCapClient()
        self.chart_windows: Dict[str, ChartWindow] = {}
        self._table_symbols: List[str] = []
        self._build_ui()
        self.theme_manager.mode_changed.connect(self._apply_theme)
        self._apply_theme(self.theme_manager.mode)
        self._append_console(
            "READY",
            "Dashboard sẵn sàng. Nhấn Bắt đầu quét để tải thị trường. "
            f"Log: {self._app_log.directory}",
        )
        self._notify_telegram_startup()

    def _notify_telegram_startup(self) -> None:
        started_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        self._notify_telegram(
            "Binance Market Console đã khởi động.\n"
            f"Thời gian: {started_at}\n"
            f"Đòn bẩy cố định x{TRADE_LEVERAGE}; ký quỹ tối đa "
            f"{MAX_ORDER_MARGIN:.2f} USDT/lệnh → vị thế tối đa "
            f"{MAX_ORDER_MARGIN * TRADE_LEVERAGE:.2f} USDT.\n"
            "Ứng dụng đang chờ bắt đầu quét thị trường."
        )

    def _notify_telegram(self, message: str) -> None:
        try:
            client = TelegramClient.from_environment()
        except ValueError as exc:
            self._append_console(
                "ERROR", f"Cấu hình Telegram không hợp lệ: {exc}"
            )
            return
        if client is None:
            self._append_console(
                "INFO", "Telegram chưa cấu hình; bỏ qua thông báo."
            )
            return

        notifier = TelegramMessageNotifier(client, message, self)
        notifier.log_received.connect(self._append_console)
        notifier.delivery_succeeded.connect(self._telegram_delivery_succeeded)
        notifier.finished.connect(
            lambda notifier=notifier: self._telegram_notifier_finished(notifier)
        )
        self._telegram_notifiers.append(notifier)
        notifier.start()

    def _telegram_notifier_finished(
        self, notifier: TelegramMessageNotifier
    ) -> None:
        if notifier in self._telegram_notifiers:
            self._telegram_notifiers.remove(notifier)
        notifier.deleteLater()

    @staticmethod
    def _application_log_directory() -> Path:
        if getattr(sys, "frozen", False):
            application_directory = Path(sys.executable).resolve().parent
        else:
            application_directory = Path(__file__).resolve().parents[3]
        return application_directory / "applog"

    def _build_ui(self) -> None:
        root = QWidget()
        page = QVBoxLayout(root)
        page.setContentsMargins(22, 18, 22, 18)
        page.setSpacing(16)

        header = QHBoxLayout()
        title_block = QVBoxLayout()
        self.title_label = QLabel("BINANCE  /  MARKET CONSOLE")
        self.title_label.setObjectName("appTitle")
        self.subtitle_label = QLabel("USDⓈ-M PERPETUAL  •  MARKET CAP > $100M")
        self.subtitle_label.setObjectName("appSubtitle")
        title_block.addWidget(self.title_label)
        title_block.addWidget(self.subtitle_label)
        header.addLayout(title_block)
        header.addStretch()

        initial_balance = f"{SIMULATION_STARTING_BALANCE:,.2f}"
        self.account_balance_label = QLabel(
            f"Ví Simulation: {initial_balance} | "
            f"Khả dụng: {initial_balance} USDT"
        )
        self.account_balance_label.setObjectName("statusBadge")
        self.account_balance_label.setToolTip(
            "Số dư Simulation; số dư Testnet sẽ cập nhật khi bắt đầu quét."
        )
        header.addWidget(self.account_balance_label)
        header.addSpacing(10)
        self.status_badge = QLabel("●  ĐANG CHỜ")
        self.status_badge.setObjectName("statusBadge")
        header.addWidget(self.status_badge, alignment=Qt.AlignmentFlag.AlignVCenter)
        page.addLayout(header)

        toolbar = QHBoxLayout()
        self.start_button = QPushButton("▶   Bắt đầu quét")
        self.start_button.setObjectName("primaryButton")
        self.stop_button = QPushButton("■   Dừng")
        self.stop_button.setObjectName("secondaryButton")
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(self._start)
        self.stop_button.clicked.connect(self._stop)
        toolbar.addWidget(self.start_button)
        toolbar.addWidget(self.stop_button)
        toolbar.addStretch()
        self.source_label = QLabel(
            "Giá Binance Futures  •  Market cap CoinLore → CoinGecko"
        )
        self.source_label.setObjectName("mutedLabel")
        toolbar.addWidget(self.source_label)
        self.network_quality_label = QLabel("Mạng: chờ kiểm tra")
        self.network_quality_label.setObjectName("mutedLabel")
        self.network_quality_label.setToolTip(
            "Chất lượng kết nối được ước lượng từ thời gian phản hồi Binance Futures."
        )
        toolbar.addWidget(self.network_quality_label)
        self.telegram_status_label = QLabel(
            "Telegram: chỉ bật khi đã cấu hình"
        )
        self.telegram_status_label.setObjectName("mutedLabel")
        self.telegram_status_label.setToolTip(
            "Bot chỉ nhận lệnh riêng từ Telegram user ID được cho phép."
        )
        toolbar.addWidget(self.telegram_status_label)
        self.theme_button = QPushButton()
        self.theme_button.setObjectName("themeButton")
        self.theme_button.clicked.connect(self.theme_manager.toggle)
        toolbar.addWidget(self.theme_button)
        page.addLayout(toolbar)

        execution_controls = QHBoxLayout()
        mode_label = QLabel("CHẾ ĐỘ")
        mode_label.setObjectName("mutedLabel")
        self.mode_selector = QComboBox()
        self.mode_selector.addItem("Simulation", "simulation")
        self.mode_selector.addItem("Binance Testnet", "testnet")
        self.mode_selector.setToolTip(
            "Testnet không dùng tiền thật; cần cấu hình API Testnet trong file .env."
        )
        self._mode_defaults = {
            "simulation": (
                SIMULATION_MAX_ORDER_MARGIN,
                SIMULATION_MAX_OPEN_POSITIONS,
            ),
            "testnet": (MAX_ORDER_MARGIN, 3),
        }
        self._selected_execution_mode = "simulation"
        amount_label = QLabel("KÝ QUỸ / LỆNH")
        amount_label.setObjectName("mutedLabel")
        self.order_margin_input = QDoubleSpinBox()
        self.order_margin_input.setRange(1.0, MAX_ORDER_MARGIN)
        self.order_margin_input.setDecimals(2)
        self.order_margin_input.setSingleStep(10.0)
        self.order_margin_input.setValue(SIMULATION_MAX_ORDER_MARGIN)
        self.order_margin_input.setSuffix(" USDT")
        self.order_margin_input.setToolTip(
            "Ký quỹ cho mỗi lệnh; giá trị vị thế bằng ký quỹ × đòn bẩy x2."
        )
        self.max_positions_input = QSpinBox()
        self.max_positions_input.setRange(1, SIMULATION_MAX_OPEN_POSITIONS)
        self.max_positions_input.setValue(SIMULATION_MAX_OPEN_POSITIONS)
        self.max_positions_input.setToolTip("Số vị thế mở tối đa cùng lúc.")
        self.mode_selector.currentIndexChanged.connect(
            self._execution_mode_changed
        )
        max_positions_label = QLabel("VỊ THẾ TỐI ĐA")
        max_positions_label.setObjectName("mutedLabel")
        take_profit_label = QLabel("CHỐT LỜI")
        take_profit_label.setObjectName("mutedLabel")
        self.take_profit_input = QDoubleSpinBox()
        self.take_profit_input.setRange(0.1, 100.0)
        self.take_profit_input.setDecimals(2)
        self.take_profit_input.setSingleStep(0.1)
        self.take_profit_input.setValue(1.0)
        self.take_profit_input.setSuffix(" %")
        self.take_profit_input.setToolTip(
            "Đóng vị thế khi PnL ròng ước tính đạt mức % notional ban đầu."
        )
        stop_loss_label = QLabel("DỪNG LỖ")
        stop_loss_label.setObjectName("mutedLabel")
        self.stop_loss_input = QDoubleSpinBox()
        self.stop_loss_input.setRange(0.1, 100.0)
        self.stop_loss_input.setDecimals(2)
        self.stop_loss_input.setSingleStep(0.1)
        self.stop_loss_input.setValue(1.0)
        self.stop_loss_input.setSuffix(" %")
        self.stop_loss_input.setToolTip(
            "Đóng vị thế khi PnL ròng ước tính chạm mức âm này."
        )
        execution_controls.addWidget(mode_label)
        execution_controls.addWidget(self.mode_selector)
        execution_controls.addSpacing(10)
        execution_controls.addWidget(amount_label)
        execution_controls.addWidget(self.order_margin_input)
        execution_controls.addSpacing(10)
        execution_controls.addWidget(max_positions_label)
        execution_controls.addWidget(self.max_positions_input)
        execution_controls.addSpacing(10)
        execution_controls.addWidget(take_profit_label)
        execution_controls.addWidget(self.take_profit_input)
        execution_controls.addSpacing(10)
        execution_controls.addWidget(stop_loss_label)
        execution_controls.addWidget(self.stop_loss_input)
        execution_controls.addStretch()
        page.addLayout(execution_controls)

        strategy_controls = QHBoxLayout()
        strategy_label = QLabel("CHIẾN THUẬT")
        strategy_label.setObjectName("mutedLabel")
        self.strategy_selector = QComboBox()
        self.strategy_selector.addItem("short_p", "short_p")
        self.strategy_selector.addItem("long_p", "long_p")
        self.strategy_selector.setToolTip(
            "Chiến thuật được chọn là hướng vào lệnh. Khi vị thế chiến thuật đang mở, "
            "ứng dụng theo dõi thêm tín hiệu ngược chiều trên cùng khung nến để đóng "
            "bảo vệ sau khi nến vào lệnh kết thúc."
        )
        strategy_label.setBuddy(self.strategy_selector)
        timeframe_label = QLabel("KHUNG NẾN")
        timeframe_label.setObjectName("mutedLabel")
        self.candle_interval_checkboxes: Dict[str, QCheckBox] = {}
        for interval in SUPPORTED_CANDLE_INTERVALS:
            checkbox = QCheckBox(interval)
            checkbox.setChecked(True)
            checkbox.setToolTip(
                f"Cho phép chiến thuật phát tín hiệu trên khung {interval}."
            )
            checkbox.toggled.connect(
                lambda _checked=False: self._update_strategy_status()
            )
            self.candle_interval_checkboxes[interval] = checkbox
        self.strategy_selector.currentIndexChanged.connect(
            self._update_strategy_status
        )
        strategy_controls.addWidget(strategy_label)
        strategy_controls.addWidget(self.strategy_selector)
        strategy_controls.addSpacing(10)
        strategy_controls.addWidget(timeframe_label)
        for checkbox in self.candle_interval_checkboxes.values():
            strategy_controls.addWidget(checkbox)
        self.leverage_summary_label = QLabel()
        self.leverage_summary_label.setObjectName("mutedLabel")
        self.order_margin_input.valueChanged.connect(
            self._update_leverage_summary
        )
        self._update_leverage_summary()
        strategy_controls.addSpacing(10)
        strategy_controls.addWidget(self.leverage_summary_label)
        strategy_controls.addStretch()
        page.addLayout(strategy_controls)

        metrics = QHBoxLayout()
        self.contract_count, self.contract_count_value = self._metric_card(
            "HỢP ĐỒNG ĐẠT LỌC", "—"
        )
        self.strategy_status, self.strategy_status_value = self._metric_card(
            "CHIẾN THUẬT", "short_p"
        )
        self._update_strategy_status()
        self.refresh_status, self.refresh_status_value = self._metric_card(
            "GIÁ THỊ TRƯỜNG", "Chưa kết nối"
        )
        self.open_positions_card, self.open_positions_value = self._metric_card(
            "LỆNH ĐANG MỞ", "0"
        )
        self.open_pnl_card, self.open_pnl_value = self._metric_card(
            "PNL CHƯA CHỐT", "+0.00 USDT"
        )
        self.realized_pnl_card, self.realized_pnl_value = self._metric_card(
            "PNL ĐÃ CHỐT (ƯỚC TÍNH)", "+0.00 USDT"
        )
        metrics.addWidget(self.contract_count)
        metrics.addWidget(self.strategy_status)
        metrics.addWidget(self.refresh_status)
        metrics.addWidget(self.open_positions_card)
        metrics.addWidget(self.open_pnl_card)
        metrics.addWidget(self.realized_pnl_card)
        page.addLayout(metrics)

        self.tabs = QTabWidget()
        market_page = QWidget()
        market_layout = QVBoxLayout(market_page)
        market_layout.setContentsMargins(0, 8, 0, 0)
        self.market_table = QTableWidget(0, 7)
        self.market_table.setHorizontalHeaderLabels(
            [
                "HỢP ĐỒNG",
                "TÀI SẢN",
                "MARKET CAP",
                "GIÁ (USDT)",
                "CHIẾN THUẬT",
                "TÍN HIỆU",
                "BIỂU ĐỒ",
            ]
        )
        self.market_table.setObjectName("marketTable")
        self.market_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.market_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.market_table.setAlternatingRowColors(True)
        vertical_header = self.market_table.verticalHeader()
        if vertical_header is not None:
            vertical_header.setVisible(False)
        horizontal_header = self.market_table.horizontalHeader()
        if horizontal_header is not None:
            horizontal_header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            horizontal_header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.market_table.setColumnWidth(0, 150)
        self.market_table.setMinimumHeight(280)
        market_layout.addWidget(self.market_table)
        self.tabs.addTab(market_page, "Thị trường")

        positions_page = QWidget()
        positions_layout = QVBoxLayout(positions_page)
        positions_layout.setContentsMargins(0, 8, 0, 0)
        self.positions_empty_label = QLabel(
            "Chưa có lệnh mở. Lệnh sẽ xuất hiện khi chiến thuật cung cấp dữ liệu vị thế."
        )
        self.positions_empty_label.setObjectName("emptyState")
        positions_layout.addWidget(self.positions_empty_label)
        self.positions_table = QTableWidget(0, 12)
        self.positions_table.setHorizontalHeaderLabels(
            [
                "HỢP ĐỒNG",
                "CHIẾN THUẬT",
                "HƯỚNG",
                "ĐÒN BẨY",
                "KÝ QUỸ (USDT)",
                "VỊ THẾ (USDT)",
                "GIÁ VÀO",
                "GIÁ HIỆN TẠI",
                "KHỐI LƯỢNG",
                "PNL CHƯA CHỐT (USDT)",
                "RÂU/THÂN NẾN TRƯỚC",
                "BIỂU ĐỒ",
            ]
        )
        self.positions_table.setObjectName("positionsTable")
        self.positions_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.positions_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.positions_table.setAlternatingRowColors(True)
        positions_vertical_header = self.positions_table.verticalHeader()
        if positions_vertical_header is not None:
            positions_vertical_header.setVisible(False)
        positions_header = self.positions_table.horizontalHeader()
        if positions_header is not None:
            positions_header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            positions_header.setSectionResizeMode(
                11, QHeaderView.ResizeMode.ResizeToContents
            )
        positions_layout.addWidget(self.positions_table, stretch=1)
        self.tabs.addTab(positions_page, "Lệnh đang mở (0)")

        statistics_page = QWidget()
        statistics_layout = QVBoxLayout(statistics_page)
        statistics_layout.setContentsMargins(0, 8, 0, 0)
        self.strategy_statistics_table = QTableWidget(0, 13)
        self.strategy_statistics_table.setHorizontalHeaderLabels(
            [
                "CHẾ ĐỘ",
                "CHIẾN THUẬT",
                "KHUNG NẾN",
                "ĐÒN BẨY",
                "MỞ",
                "ĐÓNG",
                "THẮNG",
                "THUA",
                "HÒA / CHƯA RÕ",
                "TỈ LỆ THẮNG",
                "TỔNG THẮNG (USDT)",
                "TỔNG THUA (USDT)",
                "PNL RÒNG (USDT)",
            ]
        )
        self.strategy_statistics_table.setObjectName("strategyStatisticsTable")
        self.strategy_statistics_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.strategy_statistics_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.strategy_statistics_table.itemSelectionChanged.connect(
            self._style_selected_statistics_rows
        )
        self.strategy_statistics_table.setAlternatingRowColors(True)
        statistics_vertical_header = self.strategy_statistics_table.verticalHeader()
        if statistics_vertical_header is not None:
            statistics_vertical_header.setVisible(False)
        statistics_header = self.strategy_statistics_table.horizontalHeader()
        if statistics_header is not None:
            statistics_header.setSectionResizeMode(
                QHeaderView.ResizeMode.ResizeToContents
            )
        statistics_layout.addWidget(self.strategy_statistics_table)
        self.tabs.addTab(statistics_page, "Thống kê chiến thuật")
        page.addWidget(self.tabs, stretch=3)

        console_header = QHBoxLayout()
        console_title = QLabel("SYSTEM CONSOLE")
        console_title.setObjectName("sectionTitle")
        console_hint = QLabel("LIVE EVENTS")
        console_hint.setObjectName("consoleHint")
        console_header.addWidget(console_title)
        console_header.addStretch()
        console_header.addWidget(console_hint)
        page.addLayout(console_header)

        self.console = QTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        document = self.console.document()
        if document is not None:
            document.setMaximumBlockCount(400)
        self.console.setMinimumHeight(145)
        page.addWidget(self.console, stretch=1)

        self.setCentralWidget(root)

    def _metric_card(self, title: str, value: str) -> Tuple[QFrame, QLabel]:
        card = QFrame()
        card.setObjectName("metricCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 11, 14, 11)
        title_label = QLabel(title)
        title_label.setObjectName("metricTitle")
        value_label = QLabel(value)
        value_label.setObjectName("metricValue")
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        return card, value_label

    def _apply_theme(self, mode: str) -> None:
        self.setStyleSheet(dashboard_stylesheet(mode))
        self.theme_button.setText("☀  Chế độ sáng" if mode == "dark" else "☾  Chế độ tối")
        self._recolor_tables()
        if self._network_quality_checked:
            self._render_network_quality()
        pnl_color = "#0ecb81" if self.open_pnl_value.text().startswith("+") else "#f6465d"
        self.open_pnl_value.setStyleSheet(f"color: {pnl_color};")

    def _execution_mode_changed(self, _index: int) -> None:
        self._mode_defaults[self._selected_execution_mode] = (
            self.order_margin_input.value(),
            self.max_positions_input.value(),
        )
        mode = str(self.mode_selector.currentData())
        self._selected_execution_mode = mode
        max_open_positions = (
            SIMULATION_MAX_OPEN_POSITIONS if mode == "simulation" else 100
        )
        self.order_margin_input.setMaximum(MAX_ORDER_MARGIN)
        self.max_positions_input.setMaximum(max_open_positions)
        order_margin, max_positions = self._mode_defaults[mode]
        self.order_margin_input.setValue(order_margin)
        self.max_positions_input.setValue(max_positions)
        if mode == "simulation":
            initial_balance = f"{SIMULATION_STARTING_BALANCE:,.2f}"
            self.account_balance_label.setText(
                f"Ví Simulation: {initial_balance} | "
                f"Khả dụng: {initial_balance} USDT"
            )
            self.account_balance_label.setToolTip(
                "Số dư ảo của phiên Simulation hiện tại."
            )
        else:
            self.account_balance_label.setText("Ví Testnet: đang chờ cập nhật")
            self.account_balance_label.setToolTip(
                "Số dư thực trên Binance Futures Testnet; cập nhật khi đang quét."
            )

    def _recolor_tables(self) -> None:
        colors = self.theme_manager.colors
        for row in range(self.market_table.rowCount()):
            for column in range(self.market_table.columnCount()):
                item = self.market_table.item(row, column)
                if item is None:
                    continue
                color = colors["accent"] if column == 0 else colors["text"]
                if column == 4:
                    color = colors["muted"]
                elif column == 5:
                    color = colors["accent"]
                item.setForeground(QColor(color))

        for row in range(self.positions_table.rowCount()):
            for column in range(self.positions_table.columnCount()):
                item = self.positions_table.item(row, column)
                if item is None:
                    continue
                if column == 0:
                    color = colors["accent"]
                elif column == 2:
                    color = "#0ecb81" if item.text() == "LONG" else "#f6465d"
                elif column == 9:
                    try:
                        pnl = float(item.text().replace(",", ""))
                    except ValueError:
                        pnl = 0.0
                    color = "#0ecb81" if pnl >= 0 else "#f6465d"
                else:
                    color = colors["text"]
                item.setForeground(QColor(color))

        for row in range(self.strategy_statistics_table.rowCount()):
            for column in range(self.strategy_statistics_table.columnCount()):
                item = self.strategy_statistics_table.item(row, column)
                if item is None:
                    continue
                if column == 1:
                    color = colors["accent"]
                elif column == 12:
                    try:
                        pnl = float(item.text().replace(",", ""))
                    except ValueError:
                        pnl = 0.0
                    color = "#0ecb81" if pnl >= 0 else "#f6465d"
                else:
                    color = colors["text"]
                item.setForeground(QColor(color))

        self._style_selected_statistics_rows()

    def _style_selected_statistics_rows(self) -> None:
        table = self.strategy_statistics_table
        selection_model = table.selectionModel()
        selected_rows = (
            {index.row() for index in selection_model.selectedRows()}
            if selection_model is not None
            else set()
        )
        colors = self.theme_manager.colors
        for row in range(table.rowCount()):
            selected = row in selected_rows
            for column in range(table.columnCount()):
                item = table.item(row, column)
                if item is None:
                    continue
                if selected:
                    item.setBackground(QColor(colors["selection"]))
                    color = colors["selection_text"]
                else:
                    item.setBackground(QBrush())
                    if column == 1:
                        color = colors["accent"]
                    elif column == 12:
                        try:
                            pnl = float(item.text().replace(",", ""))
                        except ValueError:
                            pnl = 0.0
                        color = "#0ecb81" if pnl >= 0 else "#f6465d"
                    else:
                        color = colors["text"]
                item.setForeground(QColor(color))

    def _start(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        candle_intervals = self._selected_candle_intervals()
        if not candle_intervals:
            self._show_error("Hãy chọn ít nhất một khung nến để quét.")
            return
        try:
            self.worker = MarketScannerWorker(
                mode=str(self.mode_selector.currentData()),
                order_margin=self.order_margin_input.value(),
                max_open_positions=self.max_positions_input.value(),
                take_profit_pct=self.take_profit_input.value(),
                stop_loss_pct=self.stop_loss_input.value(),
                market_caps_client=self.market_caps_client,
                parent=self,
                strategy_name=str(self.strategy_selector.currentData()),
                candle_intervals=candle_intervals,
                trade_statistics=self.trade_statistics,
            )
        except (ValueError, RuntimeError) as exc:
            self._show_error(str(exc))
            return
        self.market_table.setRowCount(0)
        self._table_symbols = []
        self.worker.snapshot_received.connect(self._update_table)
        self.worker.log_received.connect(self._append_console)
        self.worker.error_received.connect(self._show_error)
        self.worker.positions_received.connect(self.set_positions)
        self.worker.strategy_statistics_received.connect(
            self.set_strategy_statistics
        )
        self.worker.realized_pnl_received.connect(self.set_realized_pnl)
        self.worker.account_summary_received.connect(self.set_account_summary)
        self.worker.source_received.connect(self.set_market_source)
        self.worker.telegram_status_received.connect(self.set_telegram_status)
        self.worker.network_quality_received.connect(self.set_network_quality)
        self.worker.finished_with_status.connect(self._set_status)
        self.worker.finished.connect(self._worker_stopped)
        self.worker.start()
        self._set_running(True)
        self._set_status("ĐANG KẾT NỐI")
        self.refresh_status_value.setText("Đang tải...")
        self._notify_scan_event("Bắt đầu quét")

    def _stop(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            if (
                self.mode_selector.currentData() == "testnet"
                and self.positions_table.rowCount() > 0
            ):
                reply = QMessageBox.question(
                    self,
                    "Testnet còn vị thế mở",
                    "Dừng sẽ ngừng theo dõi TP/SL tự động. Vị thế Testnet vẫn mở trên sàn. "
                    "Bạn có chắc muốn dừng?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
            self._notify_scan_event("Đang dừng quét theo yêu cầu")
            self.worker.stop()
            self._set_status("ĐANG DỪNG")
            self._append_console("WARN", "Đã yêu cầu dừng cập nhật.")
            self.stop_button.setEnabled(False)

    def _open_chart_for_symbol(self, symbol: str) -> None:
        window = self.chart_windows.get(symbol)
        if window is not None:
            window.showNormal()
            window.raise_()
            window.activateWindow()
            return

        window = ChartWindow(symbol, self, self.theme_manager)
        window.closed.connect(self._remove_chart_window)
        self.chart_windows[symbol] = window
        window.show()
        self._append_console("INFO", f"Mở cửa sổ chart cho {symbol}.")

    def _remove_chart_window(self, symbol: str) -> None:
        self.chart_windows.pop(symbol, None)

    def set_positions(self, positions: List[PositionSnapshot]) -> None:
        """Refresh the open-position panel from a strategy or execution adapter."""
        positions = sorted(
            positions,
            key=lambda position: (
                position.wick_body_ratio is not None,
                position.wick_body_ratio or 0.0,
            ),
            reverse=True,
        )
        self.positions_table.setRowCount(len(positions))
        total_pnl = 0.0
        for row_index, position in enumerate(positions):
            pnl = position.unrealized_pnl
            total_pnl += pnl
            side_color = "#0ecb81" if position.side == "LONG" else "#f6465d"
            pnl_color = "#0ecb81" if pnl >= 0 else "#f6465d"
            self._set_position_cell(
                row_index, 0, position.symbol, self.theme_manager.colors["accent"], True
            )
            strategy_label = position.strategy_name
            if position.timeframe:
                strategy_label = f"{strategy_label} · {position.timeframe}"
            self._set_position_cell(row_index, 1, strategy_label)
            self._set_position_cell(row_index, 2, position.side, side_color, True)
            self._set_position_cell(
                row_index,
                3,
                f"x{position.leverage:g}" if position.leverage is not None else "—",
            )
            margin = position.margin_usdt
            self._set_position_cell(
                row_index, 4, f"{margin:,.2f}" if margin is not None else "—"
            )
            self._set_position_cell(row_index, 5, f"{position.notional_usdt:,.2f}")
            self._set_position_cell(row_index, 6, self._format_price(position.entry_price))
            self._set_position_cell(row_index, 7, self._format_price(position.mark_price))
            self._set_position_cell(row_index, 8, f"{position.quantity:.8g}")
            self._set_position_cell(row_index, 9, f"{pnl:+,.4f}", pnl_color, True)
            ratio_label = (
                f"{position.wick_body_ratio:.2f}x"
                if position.wick_body_ratio is not None
                else "—"
            )
            self._set_position_cell(row_index, 10, ratio_label)
            ratio_item = self.positions_table.item(row_index, 10)
            if ratio_item is not None:
                ratio_item.setToolTip(
                    "Tỉ lệ râu thuận hướng/thân của nến đã đóng gần nhất "
                    "trên khung nến của vị thế. Vị thế có tỉ lệ cao hơn đứng trước."
                )
            chart_button = QPushButton("▥  Chart")
            chart_button.setObjectName("rowChartButton")
            chart_button.clicked.connect(
                lambda _checked=False, symbol=position.symbol: (
                    self._open_chart_for_symbol(symbol)
                )
            )
            self.positions_table.setCellWidget(row_index, 11, chart_button)

        self.open_positions_value.setText(str(len(positions)))
        self.open_pnl_value.setText(f"{total_pnl:+,.2f} USDT")
        self.open_pnl_value.setStyleSheet(
            f"color: {'#0ecb81' if total_pnl >= 0 else '#f6465d'};"
        )
        self.positions_empty_label.setVisible(not positions)
        self.tabs.setTabText(1, f"Lệnh đang mở ({len(positions)})")

    def set_strategy_statistics(
        self, rows: List[StrategyTradeStatisticsRow]
    ) -> None:
        self.strategy_statistics_table.setRowCount(len(rows))
        colors = self.theme_manager.colors
        for row_index, row in enumerate(rows):
            values = (
                row.mode,
                row.strategy,
                row.timeframe or "—",
                f"x{TRADE_LEVERAGE}",
                str(row.opened),
                str(row.closed),
                str(row.wins),
                str(row.losses),
                str(row.breakeven + row.unknown_result),
                f"{row.win_rate:.2f}%",
                f"{row.winning_pnl:,.4f}",
                f"{row.losing_pnl:,.4f}",
                f"{row.net_pnl:+,.4f}",
            )
            for column, value in enumerate(values):
                if column == 1:
                    color = colors["accent"]
                elif column == 12:
                    color = "#0ecb81" if row.net_pnl >= 0 else "#f6465d"
                else:
                    color = colors["text"]
                item = QTableWidgetItem(value)
                item.setForeground(QColor(color))
                if column >= 2:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight
                        | Qt.AlignmentFlag.AlignVCenter
                    )
                self.strategy_statistics_table.setItem(
                    row_index, column, item
                )
        self._style_selected_statistics_rows()

    def set_realized_pnl(self, realized_pnl: float) -> None:
        self.realized_pnl_value.setText(f"{realized_pnl:+,.2f} USDT")
        color = "#0ecb81" if realized_pnl >= 0 else "#f6465d"
        self.realized_pnl_value.setStyleSheet(f"color: {color};")

    def set_account_summary(self, summary: Dict[str, float]) -> None:
        mode_label = (
            "Simulation"
            if self.mode_selector.currentData() == "simulation"
            else "Testnet"
        )
        wallet_balance = summary["wallet_balance"]
        available_balance = summary["available_balance"]
        unrealized_pnl = summary["unrealized_pnl"]
        equity = summary["equity"]
        self.account_balance_label.setText(
            f"Ví {mode_label}: {wallet_balance:,.2f} | "
            f"Khả dụng: {available_balance:,.2f} USDT"
        )
        self.account_balance_label.setToolTip(
            f"{mode_label}\n"
            f"Equity: {equity:,.2f} USDT\n"
            f"PnL chưa chốt: {unrealized_pnl:+,.4f} USDT\n"
            f"Đòn bẩy: x{TRADE_LEVERAGE}; ký quỹ tối đa "
            f"{MAX_ORDER_MARGIN:.2f} USDT/lệnh → vị thế tối đa "
            f"{MAX_ORDER_MARGIN * TRADE_LEVERAGE:.2f} USDT"
        )

    def set_market_source(self, source: str) -> None:
        self.source_label.setText(source)

    def set_network_quality(self, latency_ms: Optional[float]) -> None:
        self._network_quality_checked = True
        self._network_latency_ms = latency_ms
        self._render_network_quality()

    def _render_network_quality(self) -> None:
        latency_ms = self._network_latency_ms
        if latency_ms is None:
            label = "Mạng: mất kết nối"
            color = "#f6465d"
            tooltip = "Không nhận được phản hồi từ Binance Futures."
        elif latency_ms <= 500:
            label = f"Mạng: tốt · {latency_ms:.0f} ms"
            color = "#0ecb81" if self.theme_manager.mode == "dark" else "#087f5b"
            tooltip = "Độ trễ Binance Futures tốt (≤ 500 ms)."
        elif latency_ms <= 1500:
            label = f"Mạng: trung bình · {latency_ms:.0f} ms"
            color = "#f0b90b" if self.theme_manager.mode == "dark" else "#9a6700"
            tooltip = "Độ trễ Binance Futures trung bình (501–1500 ms)."
        else:
            label = f"Mạng: kém · {latency_ms:.0f} ms"
            color = "#f6465d"
            tooltip = "Độ trễ Binance Futures cao (> 1500 ms)."
        self.network_quality_label.setText(label)
        self.network_quality_label.setStyleSheet(
            f"color: {color}; font-weight: 700;"
        )
        self.network_quality_label.setToolTip(tooltip)

    def set_telegram_status(self, status: str) -> None:
        self.telegram_status_label.setText(status)
        if status == "Telegram: đã kết nối":
            self.telegram_status_label.setStyleSheet("color: #0ecb81;")
        elif status == "Telegram: mất kết nối":
            self.telegram_status_label.setStyleSheet("color: #f6465d;")
        else:
            self.telegram_status_label.setStyleSheet("")

    def _telegram_delivery_succeeded(self, delivered: int) -> None:
        self.set_telegram_status("Telegram: đã kết nối")
        self.telegram_status_label.setToolTip(
            f"Đã gửi thông báo thành công tới {delivered} tài khoản Telegram."
        )

    def _notify_scan_event(self, event: str) -> None:
        mode = (
            "Binance Futures Testnet"
            if self.mode_selector.currentData() == "testnet"
            else "Simulation"
        )
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        intervals = ", ".join(self._selected_candle_intervals()) or "chưa chọn"
        self._notify_telegram(
            f"{event}.\nThời gian: {timestamp}\nChế độ: {mode}\n"
            f"Chiến thuật: {self.strategy_selector.currentData()}\n"
            f"Đòn bẩy x{TRADE_LEVERAGE}; ký quỹ "
            f"{self.order_margin_input.value():.2f} USDT → vị thế tối đa "
            f"{self.order_margin_input.value() * TRADE_LEVERAGE:.2f} USDT/lệnh.\n"
            f"Khung nến đang bật: {intervals}."
        )

    def _update_leverage_summary(self, _value: float = 0.0) -> None:
        if not hasattr(self, "leverage_summary_label"):
            return
        margin = self.order_margin_input.value()
        self.leverage_summary_label.setText(
            f"x{TRADE_LEVERAGE} | {margin:.2f} → "
            f"{margin * TRADE_LEVERAGE:.2f} USDT/lệnh"
        )

    def _selected_candle_intervals(self) -> List[str]:
        return [
            interval
            for interval in SUPPORTED_CANDLE_INTERVALS
            if self.candle_interval_checkboxes[interval].isChecked()
        ]

    def _update_strategy_status(self, _index: int = 0) -> None:
        if not hasattr(self, "strategy_status_value"):
            return
        intervals = ", ".join(self._selected_candle_intervals()) or "chưa chọn"
        self.strategy_status_value.setText(
            f"{self.strategy_selector.currentData()} | {intervals}"
        )

    def _set_position_cell(
        self,
        row: int,
        column: int,
        text: str,
        color: Optional[str] = None,
        bold: bool = False,
    ) -> None:
        item = QTableWidgetItem(text)
        item.setForeground(QColor(color or self.theme_manager.colors["text"]))
        if bold:
            font = item.font()
            font.setBold(True)
            item.setFont(font)
        if column >= 3:
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
        self.positions_table.setItem(row, column, item)

    def _update_table(self, rows: List[MarketRow]) -> None:
        symbols = [row["symbol"] for row in rows]
        if symbols != self._table_symbols:
            self._table_symbols = symbols
            self.market_table.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                self._set_cell(
                    row_index,
                    0,
                    row["symbol"],
                    self.theme_manager.colors["accent"],
                    True,
                )
                self._set_cell(row_index, 1, row["base_asset"])
                self._set_cell(row_index, 2, self._format_market_cap(row["market_cap"]))
                self._set_cell(row_index, 3, self._format_price(row["price"]))
                self._set_cell(
                    row_index, 4, row["strategy"], self.theme_manager.colors["muted"]
                )
                self._set_cell(
                    row_index,
                    5,
                    row["signal"],
                    self.theme_manager.colors["accent"],
                    True,
                )
                chart_button = QPushButton("▥  Chart")
                chart_button.setObjectName("rowChartButton")
                chart_button.clicked.connect(
                    lambda _checked=False, symbol=row["symbol"]: self._open_chart_for_symbol(symbol)
                )
                self.market_table.setCellWidget(row_index, 6, chart_button)
        else:
            for row_index, row in enumerate(rows):
                price_item = self.market_table.item(row_index, 3)
                signal_item = self.market_table.item(row_index, 5)
                if price_item is not None:
                    price_item.setText(self._format_price(row["price"]))
                if signal_item is not None:
                    signal_item.setText(row["signal"])

        self.contract_count_value.setText(str(len(rows)))
        self.refresh_status_value.setText(
            datetime.now().strftime("Cập nhật %H:%M:%S")
        )
        self._set_status("ĐANG THEO DÕI")

    def _set_cell(
        self,
        row: int,
        column: int,
        text: str,
        color: Optional[str] = None,
        bold: bool = False,
    ) -> None:
        item = QTableWidgetItem(text)
        item.setForeground(QColor(color or self.theme_manager.colors["text"]))
        if bold:
            font = item.font()
            font.setBold(True)
            item.setFont(font)
        if column in (2, 3):
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
        self.market_table.setItem(row, column, item)

    @staticmethod
    def _format_market_cap(value: float) -> str:
        return f"${value / 1_000_000_000:.2f}B" if value >= 1_000_000_000 else f"${value / 1_000_000:.1f}M"

    @staticmethod
    def _format_price(value: float) -> str:
        return f"{value:,.8f}".rstrip("0").rstrip(".")

    def _append_console(self, level: str, message: str) -> None:
        colors = {
            "READY": "#38bdf8",
            "INFO": "#94a3b8",
            "SUCCESS": "#34d399",
            "UPDATE": "#60a5fa",
            "WARN": "#fb923c",
            "ERROR": "#fb7185",
        }
        now = datetime.now().astimezone()
        try:
            self._app_log.append(now, level, message)
        except OSError as exc:
            if not self._app_log_error_reported:
                self._app_log_error_reported = True
                QMessageBox.warning(
                    self,
                    "Không lưu được app log",
                    f"Không ghi được log vào {self._app_log.directory}: {exc}",
                )
        timestamp = now.strftime("%H:%M:%S")
        color = colors.get(level, "#cbd5e1")
        self.console.append(
            f'<span style="color:#64748b">[{timestamp}]</span> '
            f'<span style="color:{color};font-weight:700">[{escape(level)}]</span> '
            f'<span style="color:#cbd5e1">{escape(message)}</span>'
        )

    def _show_error(self, message: str) -> None:
        self._set_status("CÓ LỖI")
        self._append_console("ERROR", message)

    def _set_status(self, text: str) -> None:
        self.status_badge.setText(f"●  {text}")

    def _worker_stopped(self) -> None:
        self._set_running(False)
        if (
            self.mode_selector.currentData() == "testnet"
            and self.positions_table.rowCount() > 0
        ):
            self._append_console(
                "WARN",
                "Vẫn còn vị thế Testnet mở; TP/SL tự động không còn được theo dõi.",
            )

    def _set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        if running:
            self.start_button.setText("●  ĐANG QUÉT")
            self.start_button.setStyleSheet(
                "QPushButton#primaryButton, "
                "QPushButton#primaryButton:disabled {"
                "background-color: #0ecb81; color: #0b0e11; "
                "border: 1px solid #0ecb81;}"
            )
        else:
            self.start_button.setText("▶   Bắt đầu quét")
            self.start_button.setStyleSheet("")
        self.stop_button.setEnabled(running)
        self.mode_selector.setEnabled(not running)
        self.order_margin_input.setEnabled(not running)
        self.max_positions_input.setEnabled(not running)
        self.take_profit_input.setEnabled(not running)
        self.stop_loss_input.setEnabled(not running)
        self.strategy_selector.setEnabled(not running)
        for checkbox in self.candle_interval_checkboxes.values():
            checkbox.setEnabled(not running)

    def closeEvent(self, event) -> None:
        if (
            not self._close_after_telegram_notifications
            and self.mode_selector.currentData() == "testnet"
            and self.positions_table.rowCount() > 0
        ):
            reply = QMessageBox.question(
                self,
                "Testnet còn vị thế mở",
                "Đóng ứng dụng sẽ ngừng theo dõi TP/SL tự động. Vị thế Testnet vẫn mở trên sàn. "
                "Bạn có chắc muốn thoát?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        for window in list(self.chart_windows.values()):
            window.close()
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(20000)
        running_notifiers = [
            notifier
            for notifier in self._telegram_notifiers
            if notifier.isRunning()
        ]
        if running_notifiers:
            if not self._close_after_telegram_notifications:
                self._close_after_telegram_notifications = True
                for notifier in running_notifiers:
                    notifier.finished.connect(self.close)
            event.ignore()
            return
        event.accept()
