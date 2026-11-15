"""Opt-in live Telegram integration test; never runs during normal test discovery."""

import os
import unittest
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from app.backend.telegram import TelegramClient



PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


@unittest.skipUnless(
    os.getenv("RUN_TELEGRAM_LIVE_TEST") == "1",
    "Set RUN_TELEGRAM_LIVE_TEST=1 to send a real Telegram message.",
)
class TelegramLiveIntegrationTests(unittest.TestCase):
    def test_sends_message_to_first_allowed_user(self):
        client = TelegramClient.from_environment()
        if client is None:
            self.fail(
                "Configure TELEGRAM_BOT_TOKEN and "
                "TELEGRAM_ALLOWED_USER_IDS in the project .env file."
            )

        raw_user_ids = os.environ["TELEGRAM_ALLOWED_USER_IDS"]
        user_id = int(raw_user_ids.split(",", maxsplit=1)[0].strip())
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        client.send_message(
            user_id,
            f"Binance Market Console Telegram connection test ({timestamp}).",
        )


if __name__ == "__main__":
    unittest.main()
