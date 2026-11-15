"""Login dialog shown before the market dashboard."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)
from typing import Optional

from app.backend.auth import verify_credentials
from app.frontend.theme import ThemeManager, login_stylesheet


class LoginWindow(QDialog):
    def __init__(self, theme_manager: Optional[ThemeManager] = None) -> None:
        super().__init__()
        self.setWindowTitle("Đăng nhập - Binance Market Console")
        self.setFixedSize(420, 390)
        self.theme_manager = theme_manager or ThemeManager()
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self._build_ui()
        self.theme_manager.mode_changed.connect(self._apply_theme)
        self._apply_theme(self.theme_manager.mode)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 24)

        card = QFrame()
        card.setObjectName("loginCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(26, 26, 26, 26)
        layout.setSpacing(16)

        header = QHBoxLayout()
        brand = QLabel("BINANCE  /  MARKET CONSOLE")
        brand.setObjectName("loginBrand")
        header.addWidget(brand)
        header.addStretch()
        self.theme_button = QPushButton()
        self.theme_button.setObjectName("loginThemeButton")
        self.theme_button.clicked.connect(self.theme_manager.toggle)
        header.addWidget(self.theme_button)
        layout.addLayout(header)

        title = QLabel("Chào mừng trở lại")
        title.setObjectName("loginTitle")
        subtitle = QLabel("Đăng nhập để tiếp tục")
        subtitle.setObjectName("loginSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        form = QFormLayout()
        form.setSpacing(10)
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Tên tài khoản")
        self.username_input.setClearButtonEnabled(True)
        self.username_input.setMinimumHeight(40)
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Mật khẩu")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setMinimumHeight(40)
        self.password_input.returnPressed.connect(self._attempt_login)
        form.addRow("Tài khoản", self.username_input)
        form.addRow("Mật khẩu", self.password_input)
        layout.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.setObjectName("loginError")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        self.login_button = QPushButton("ĐĂNG NHẬP")
        self.login_button.setObjectName("loginButton")
        self.login_button.clicked.connect(self._attempt_login)
        layout.addWidget(self.login_button)

        root.addWidget(card)

    def _apply_theme(self, mode: str) -> None:
        self.setStyleSheet(login_stylesheet(mode))
        self.theme_button.setText("☀" if mode == "dark" else "☾")

    def _attempt_login(self) -> None:
        if verify_credentials(self.username_input.text(), self.password_input.text()):
            self.accept()
            return
        self.error_label.setText("Tên tài khoản hoặc mật khẩu không chính xác.")
        self.password_input.clear()
        self.password_input.setFocus()
