"""Shared day/night palettes and persistent theme selection."""

from string import Template
from typing import Dict

from PyQt6.QtCore import QObject, QSettings, pyqtSignal


THEMES: Dict[str, Dict[str, str]] = {
    "dark": {
        "background": "#0b0e11",
        "surface": "#151a21",
        "surface_alt": "#11161c",
        "border": "#2b3139",
        "text": "#eaecef",
        "muted": "#848e9c",
        "accent": "#3b82f6",
        "accent_hover": "#60a5fa",
        "accent_text": "#ffffff",
        "secondary": "#202630",
        "secondary_hover": "#2b3139",
        "selection": "#3b5570",
        "selection_text": "#ffffff",
        "input": "#11161c",
        "console": "#080b0f",
        "console_text": "#cbd5e1",
        "chart_bg": "#161a1e",
        "chart_grid": "#2b3139",
        "chart_axis": "#848e9c",
        "chart_crosshair": "#b7bdc6",
        "chart_badge": "#474d57",
        "chart_text": "#eaecef",
    },
    "light": {
        "background": "#f3f5f7",
        "surface": "#ffffff",
        "surface_alt": "#f7f8fa",
        "border": "#e2e5e9",
        "text": "#1e2329",
        "muted": "#707a8a",
        "accent": "#2563eb",
        "accent_hover": "#1d4ed8",
        "accent_text": "#ffffff",
        "secondary": "#edf0f3",
        "secondary_hover": "#e2e5e9",
        "selection": "#d6e7ff",
        "selection_text": "#1e2329",
        "input": "#ffffff",
        "console": "#111820",
        "console_text": "#d6deea",
        "chart_bg": "#ffffff",
        "chart_grid": "#eaecef",
        "chart_axis": "#707a8a",
        "chart_crosshair": "#707a8a",
        "chart_badge": "#5e6673",
        "chart_text": "#1e2329",
    },
}


class ThemeManager(QObject):
    mode_changed = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._settings = QSettings("BinanceMarketConsole", "MarketConsole")
        saved_mode = self._settings.value("theme", "dark", type=str)
        self._mode = saved_mode if saved_mode in THEMES else "dark"

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def colors(self) -> Dict[str, str]:
        return THEMES[self._mode]

    def toggle(self) -> None:
        self.set_mode("light" if self._mode == "dark" else "dark")

    def set_mode(self, mode: str) -> None:
        if mode not in THEMES:
            raise ValueError(f"Unknown theme mode: {mode}")
        if mode == self._mode:
            return
        self._mode = mode
        self._settings.setValue("theme", mode)
        self.mode_changed.emit(mode)


def dashboard_stylesheet(mode: str) -> str:
    colors = THEMES[mode]
    return Template(
        """
        QWidget {
            background: $background; color: $text;
            font-family: "Segoe UI"; font-size: 10pt;
        }
        QLabel#appTitle { color: $text; font-size: 20pt; font-weight: 700; }
        QLabel#appSubtitle { color: $accent; font-size: 9pt; font-weight: 700; }
        QLabel#statusBadge {
            background: $surface; color: $accent; border: 1px solid $border;
            border-radius: 12px; padding: 8px 13px; font-size: 9pt; font-weight: 700;
        }
        QLabel#mutedLabel { color: $muted; }
        QLabel#metricTitle { color: $muted; font-size: 8pt; font-weight: 700; }
        QLabel#metricValue { color: $text; font-size: 14pt; font-weight: 700; }
        QFrame#metricCard {
            background: $surface; border: 1px solid $border; border-radius: 9px;
        }
        QPushButton {
            border-radius: 6px; padding: 10px 16px; font-weight: 700;
        }
        QPushButton#primaryButton { background: $accent; color: $accent_text; }
        QPushButton#primaryButton:hover { background: $accent_hover; }
        QPushButton#secondaryButton, QPushButton#themeButton {
            background: $secondary; color: $text; border: 1px solid $border;
        }
        QPushButton#secondaryButton:hover, QPushButton#themeButton:hover {
            background: $secondary_hover;
        }
        QPushButton#themeButton { padding: 8px 12px; }
        QComboBox, QDoubleSpinBox, QSpinBox {
            background: $input; color: $text; border: 1px solid $border;
            border-radius: 5px; padding: 6px 8px; min-height: 20px;
        }
        QComboBox QAbstractItemView {
            background: $surface; color: $text; selection-background-color: $selection;
        }
        QPushButton#rowChartButton {
            background: $secondary; color: $text; border: 1px solid $border;
            border-radius: 4px; padding: 5px 9px; font-size: 9pt;
        }
        QPushButton#rowChartButton:hover { background: $selection; }
        QPushButton:disabled { background: $secondary; color: $muted; }
        QTableWidget#marketTable, QTableWidget#positionsTable {
            background: $surface; alternate-background-color: $surface_alt;
            color: $text; border: 1px solid $border; border-radius: 8px;
            gridline-color: $border; selection-background-color: $selection;
        }
        QTableWidget#strategyStatisticsTable {
            background: $surface; alternate-background-color: $surface_alt;
            color: $text; border: 1px solid $border; border-radius: 8px;
            gridline-color: $border; selection-background-color: $selection;
            selection-color: $selection_text;
        }
        QTableWidget#strategyStatisticsTable::item:selected {
            background-color: $selection; color: $selection_text;
        }
        QHeaderView::section {
            background: $surface_alt; color: $muted; border: none;
            border-bottom: 1px solid $border; padding: 9px;
            font-size: 8pt; font-weight: 700;
        }
        QTabWidget::pane { border: 1px solid $border; border-radius: 7px; }
        QTabBar::tab {
            background: $surface_alt; color: $muted; padding: 8px 14px;
            border: 1px solid $border; border-bottom: none;
        }
        QTabBar::tab:selected { background: $surface; color: $accent; }
        QLabel#emptyState { color: $muted; padding: 6px; }
        QLabel#sectionTitle { color: $text; font-size: 9pt; font-weight: 700; }
        QLabel#consoleHint { color: #38bdf8; font-family: Consolas; font-size: 8pt; }
        QTextEdit#console {
            background: $console; color: $console_text; border: 1px solid $border;
            border-radius: 8px; padding: 8px; font-family: Consolas, "Courier New";
            font-size: 9pt;
        }
        """
    ).substitute(colors)


def login_stylesheet(mode: str) -> str:
    colors = THEMES[mode]
    return Template(
        """
        QDialog { background: $background; color: $text; font-family: "Segoe UI"; }
        QFrame#loginCard {
            background: $surface; border: 1px solid $border; border-radius: 12px;
        }
        QLabel#loginBrand { color: $accent; font-size: 9pt; font-weight: 700; }
        QLabel#loginTitle { color: $text; font-size: 19pt; font-weight: 700; }
        QLabel#loginSubtitle { color: $muted; font-size: 10pt; }
        QLineEdit {
            background: $input; color: $text; border: 1px solid $border;
            border-radius: 6px; padding: 9px;
        }
        QLineEdit:focus { border: 1px solid $accent; }
        QLabel { color: $text; }
        QLabel#loginError { color: #f6465d; min-height: 18px; }
        QPushButton#loginButton {
            background: $accent; color: $accent_text; border: none;
            border-radius: 6px; padding: 11px; font-weight: 700;
        }
        QPushButton#loginButton:hover { background: $accent_hover; }
        QPushButton#loginThemeButton {
            background: $secondary; color: $text; border: 1px solid $border;
            border-radius: 6px; padding: 6px 10px;
        }
        """
    ).substitute(colors)


def chart_stylesheet(mode: str) -> str:
    colors = THEMES[mode]
    return Template(
        """
        QMainWindow, QWidget {
            background: $chart_bg; color: $chart_text; font-family: "Segoe UI";
        }
        QComboBox, QPushButton {
            background: $surface; color: $text; border: 1px solid $border;
            border-radius: 4px; padding: 7px 10px;
        }
        QPushButton:hover { background: $secondary_hover; }
        QPushButton#themeButton {
            background: $secondary; color: $text; border: 1px solid $border;
            border-radius: 4px; padding: 7px 10px;
        }
        QLabel#chartTitle { color: $chart_text; font-size: 15pt; font-weight: 700; }
        QLabel#ohlcLabel { color: #0ecb81; font-family: Consolas; }
        QLabel#chartStatus { color: $chart_axis; }
        """
    ).substitute(colors)
