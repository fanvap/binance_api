import os
import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

import requests

from app.backend.telegram import (
    TelegramClient,
    TelegramCommand,
    TelegramError,
    TelegramMessageNotifier,
    _split_message,
    parse_command,
)


class TelegramTests(unittest.TestCase):
    def test_parser_accepts_only_supported_open_shapes(self):
        self.assertEqual(
            parse_command(
                TelegramCommand(42, "/open@my_bot BTCUSDT long market")
            ),
            ("open", ["BTCUSDT", "LONG", "MARKET"]),
        )
        self.assertEqual(
            parse_command(
                TelegramCommand(42, "/open BTCUSDT SHORT LIMIT 65000")
            ),
            ("open", ["BTCUSDT", "SHORT", "LIMIT", "65000"]),
        )
        self.assertEqual(parse_command(TelegramCommand(42, "/pnl")), ("pnl", []))

    def test_parser_rejects_nonfinite_limit_price_and_unknown_command(self):
        for text in (
            "/open BTCUSDT LONG LIMIT nan",
            "/open BTCUSDT LONG MARKET 65000",
            "/close_all now",
            "/pnl extra",
            "/unknown",
        ):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_command(TelegramCommand(42, text))

    def test_authorization_requires_allowlisted_user_in_private_chat(self):
        client = TelegramClient("test-token", [42])
        allowed = {
            "message": {
                "from": {"id": 42},
                "chat": {"id": 42, "type": "private"},
                "text": "/account",
            }
        }
        group = {
            "message": {
                "from": {"id": 42},
                "chat": {"id": -100, "type": "supergroup"},
                "text": "/account",
            }
        }
        unauthorized = {
            "message": {
                "from": {"id": 7},
                "chat": {"id": 7, "type": "private"},
                "text": "/account",
            }
        }

        self.assertEqual(
            client.authorized_command(allowed),
            TelegramCommand(42, "/account"),
        )
        self.assertIsNone(client.authorized_command(group))
        self.assertIsNone(client.authorized_command(unauthorized))

    def test_environment_requires_both_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(TelegramClient.from_environment())
        with patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": "token"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "cả"):
                TelegramClient.from_environment()

    def test_network_error_does_not_expose_bot_token(self):
        token = "secret-bot-token"
        client = TelegramClient(token, [42])
        with patch.object(
            client.session,
            "request",
            side_effect=requests.Timeout(),
        ):
            with self.assertRaises(RuntimeError) as error:
                client.send_message(42, "test")
        self.assertNotIn(token, str(error.exception))

    def test_http_404_reports_invalid_or_revoked_token_without_exposing_it(self):
        token = "secret-bot-token"
        client = TelegramClient(token, [42])
        with patch.object(
            client.session,
            "request",
            return_value=SimpleNamespace(
                status_code=404,
                json=lambda: {"description": "Not Found"},
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "không hợp lệ hoặc đã bị thu hồi"
            ) as error:
                client.send_message(42, "test")
        self.assertNotIn(token, str(error.exception))

    def test_http_403_explains_bot_must_be_started_by_user(self):
        client = TelegramClient("test-token", [42])
        with patch.object(
            client.session,
            "request",
            return_value=SimpleNamespace(
                status_code=403,
                json=lambda: {
                    "description": "Forbidden: bot was blocked by the user"
                },
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "bot was blocked by the user"
            ):
                client.send_message(42, "test")

    def test_api_error_detail_never_reveals_bot_token(self):
        token = "secret-bot-token"
        client = TelegramClient(token, [42])
        with patch.object(
            client.session,
            "request",
            return_value=SimpleNamespace(
                status_code=403,
                json=lambda: {"description": f"Forbidden {token}"},
            ),
        ):
            with self.assertRaises(RuntimeError) as error:
                client.send_message(42, "test")
        self.assertNotIn(token, str(error.exception))

    def test_long_lines_are_split_within_telegram_message_limit(self):
        chunks = _split_message("x" * 8000, limit=3500)

        self.assertEqual([len(chunk) for chunk in chunks], [3500, 3500, 1000])

    def test_message_notifier_sends_to_each_authorized_user(self):
        client = TelegramClient("test-token", [42, 84])
        with patch.object(client, "send_message") as send_message:
            notifier = TelegramMessageNotifier(client, "App started")
            logs = []
            notifier.log_received.connect(lambda level, message: logs.append(message))
            delivered_counts = []
            notifier.delivery_succeeded.connect(delivered_counts.append)

            notifier.run()

        self.assertCountEqual(
            send_message.call_args_list,
            [
                call(42, "App started"),
                call(84, "App started"),
            ],
        )
        self.assertIn("Đã gửi thông báo Telegram tới 2 tài khoản.", logs)
        self.assertEqual(delivered_counts, [2])

    def test_message_notifier_logs_generic_delivery_failure(self):
        client = TelegramClient("test-token", [42])
        with patch.object(
            client,
            "send_message",
            side_effect=TelegramError("Telegram unavailable"),
        ):
            notifier = TelegramMessageNotifier(client, "Scan started")
            logs = []
            notifier.log_received.connect(lambda level, message: logs.append(message))
            delivered_counts = []
            notifier.delivery_succeeded.connect(delivered_counts.append)

            notifier.run()

        self.assertEqual(
            logs,
            ["Không gửi được thông báo Telegram tới user 42: Telegram unavailable"],
        )
        self.assertEqual(delivered_counts, [])


if __name__ == "__main__":
    unittest.main()
