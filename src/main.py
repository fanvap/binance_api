"""Application entry point."""

import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication
from dotenv import load_dotenv

from app.frontend.login_window import LoginWindow
from app.frontend.main_window import MainWindow
from app.frontend.theme import ThemeManager

if getattr(sys, "frozen", False):
    env_file = Path(sys.executable).resolve().parent / ".env"
else:
    env_file = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(env_file)


def main() -> int:
    application = QApplication(sys.argv)
    theme_manager = ThemeManager()
    login = LoginWindow(theme_manager)
    if login.exec() != LoginWindow.DialogCode.Accepted:
        return 0

    window = MainWindow(theme_manager)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
