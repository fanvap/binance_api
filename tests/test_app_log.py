import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app.backend.app_log import AppLogWriter


class AppLogWriterTests(unittest.TestCase):
    def test_appends_timestamped_entries_to_daily_text_file(self):
        timestamp = datetime(2026, 11, 15, 8, 52, 0)
        with tempfile.TemporaryDirectory() as directory:
            writer = AppLogWriter(Path(directory) / "applog")
            writer.append(timestamp, "INFO", "Scan started\nMode: Simulation")
            writer.append(timestamp, "WARN", "Telegram unavailable")

            log_file = Path(directory) / "applog" / "app_20261115.txt"
            self.assertEqual(
                log_file.read_text(encoding="utf-8").splitlines(),
                [
                    "[2026-11-15 08:52:00] [INFO] Scan started",
                    "[2026-11-15 08:52:00] [INFO] Mode: Simulation",
                    "[2026-11-15 08:52:00] [WARN] Telegram unavailable",
                ],
            )


if __name__ == "__main__":
    unittest.main()
