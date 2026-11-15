"""Telegram Bot API polling, allowlisting, and queued notifications."""

import math
import os
import queue
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests
from PyQt6.QtCore import QThread, pyqtSignal


TELEGRAM_API_BASE_URL = "https://api.telegram.org"


class TelegramError(RuntimeError):
    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class TelegramCommand:
    user_id: int
    text: str


class TelegramClient:
    def __init__(self, token: str, allowed_user_ids: List[int]) -> None:
        if not token.strip():
            raise ValueError("Thiếu TELEGRAM_BOT_TOKEN.")
        if not allowed_user_ids:
            raise ValueError("Cần cấu hình TELEGRAM_ALLOWED_USER_IDS.")
        self._token = token.strip()
        self.allowed_user_ids = frozenset(allowed_user_ids)
        self.session = requests.Session()

    @classmethod
    def from_environment(cls) -> Optional["TelegramClient"]:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        raw_user_ids = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").strip()
        if not token and not raw_user_ids:
            return None
        if not token or not raw_user_ids:
            raise ValueError(
                "Cần cấu hình cả TELEGRAM_BOT_TOKEN và TELEGRAM_ALLOWED_USER_IDS."
            )
        try:
            user_ids = [int(value.strip()) for value in raw_user_ids.split(",")]
        except ValueError as exc:
            raise ValueError(
                "TELEGRAM_ALLOWED_USER_IDS phải là danh sách số nguyên, cách nhau bằng dấu phẩy."
            ) from exc
        if any(user_id <= 0 for user_id in user_ids):
            raise ValueError("Telegram user ID phải là số nguyên dương.")
        return cls(token, list(dict.fromkeys(user_ids)))

    def get_updates(self, offset: Optional[int]) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "timeout": 0,
            "allowed_updates": '["message"]',
        }
        if offset is not None:
            params["offset"] = offset
        result = self._request("getUpdates", method="GET", params=params)
        updates = result.get("result")
        if not isinstance(updates, list):
            raise TelegramError("Telegram trả về danh sách update không hợp lệ.")
        return updates

    def discard_pending_updates(self) -> Optional[int]:
        offset: Optional[int] = None
        while True:
            updates = self.get_updates(offset)
            if not updates:
                return offset
            offset = max(int(item["update_id"]) for item in updates) + 1

    def authorized_command(
        self, update: Dict[str, Any]
    ) -> Optional[TelegramCommand]:
        message = update.get("message")
        if not isinstance(message, dict):
            return None
        sender = message.get("from")
        chat = message.get("chat")
        if not isinstance(sender, dict) or not isinstance(chat, dict):
            return None
        user_id = sender.get("id")
        chat_id = chat.get("id")
        if (
            chat.get("type") != "private"
            or not isinstance(user_id, int)
            or user_id not in self.allowed_user_ids
            or chat_id != user_id
        ):
            return None
        text = message.get("text")
        if not isinstance(text, str) or not text.lstrip().startswith("/"):
            return None
        return TelegramCommand(user_id, text.strip())

    def send_message(self, user_id: int, text: str) -> None:
        if user_id not in self.allowed_user_ids:
            raise TelegramError("Không được phép gửi tới Telegram user này.")
        self._request(
            "sendMessage",
            method="POST",
            json={"chat_id": user_id, "text": text},
        )

    def _request(
        self,
        method_name: str,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{TELEGRAM_API_BASE_URL}/bot{self._token}/{method_name}"
        try:
            response = self.session.request(
                method,
                url,
                params=params,
                json=json,
                timeout=8,
            )
        except requests.RequestException as exc:
            raise TelegramError(
                f"Telegram network error ({type(exc).__name__}).",
                retryable=True,
            ) from None
        if response.status_code != 200:
            status = response.status_code
            try:
                error_payload = response.json()
            except (requests.RequestException, ValueError):
                error_payload = {}
            description = (
                error_payload.get("description")
                if isinstance(error_payload, dict)
                else None
            )
            if isinstance(description, str):
                description = description.replace(self._token, "[redacted]").strip()
            else:
                description = ""
            if status == 404:
                raise TelegramError(
                    "Telegram HTTP 404: bot token không hợp lệ hoặc đã bị thu hồi. "
                    "Kiểm tra TELEGRAM_BOT_TOKEN và dùng token mới từ BotFather."
                )
            if status == 403:
                raise TelegramError(
                    "Telegram HTTP 403: bot không được phép nhắn tới user này. "
                    "Mở đúng bot, gửi /start và kiểm tra bot chưa bị chặn."
                    + (f" Chi tiết Telegram: {description}" if description else "")
                )
            raise TelegramError(
                f"Telegram HTTP error {status}."
                + (f" Chi tiết Telegram: {description}" if description else ""),
                retryable=status == 429 or status >= 500,
            )
        try:
            payload = response.json()
        except (requests.RequestException, ValueError):
            raise TelegramError("Telegram trả về JSON không hợp lệ.") from None
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            error_code = payload.get("error_code") if isinstance(payload, dict) else None
            retryable = error_code == 429 or (
                isinstance(error_code, int) and error_code >= 500
            )
            raise TelegramError(
                f"Telegram API từ chối yêu cầu (mã {error_code or 'không rõ'}).",
                retryable=retryable,
            )
        return payload


class TelegramMessageNotifier(QThread):
    log_received = pyqtSignal(str, str)
    delivery_succeeded = pyqtSignal(int)

    def __init__(
        self, client: TelegramClient, message: str, parent=None
    ) -> None:
        super().__init__(parent)
        self.client = client
        self.message = message

    def run(self) -> None:
        delivered = 0
        for user_id in self.client.allowed_user_ids:
            try:
                self.client.send_message(user_id, self.message)
            except TelegramError as exc:
                self.log_received.emit(
                    "WARN",
                    f"Không gửi được thông báo Telegram tới user {user_id}: {exc}",
                )
            else:
                delivered += 1
        if delivered:
            self.delivery_succeeded.emit(delivered)
            self.log_received.emit(
                "INFO",
                f"Đã gửi thông báo Telegram tới {delivered} tài khoản.",
            )


class TelegramWorker(QThread):
    """Runs Telegram polling separately so network latency never blocks trading."""

    log_received = pyqtSignal(str, str)
    status_received = pyqtSignal(str)

    def __init__(self, client: TelegramClient, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.commands: queue.Queue[TelegramCommand] = queue.Queue()
        self._outgoing: queue.Queue[Tuple[int, str]] = queue.Queue()
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def send_to(self, user_id: int, text: str) -> None:
        for chunk in _split_message(text):
            self._outgoing.put((user_id, chunk))

    def broadcast(self, text: str) -> None:
        for user_id in self.client.allowed_user_ids:
            self.send_to(user_id, text)

    def run(self) -> None:
        offset: Optional[int] = None
        pending_updates_cleared = False
        self.status_received.emit("Telegram: đang kết nối")
        while not self._stop_event.is_set():
            try:
                if not pending_updates_cleared:
                    offset = self.client.discard_pending_updates()
                    pending_updates_cleared = True
                    self.log_received.emit(
                        "INFO",
                        "Telegram đã kết nối; bỏ qua lệnh gửi trước khi phiên chạy bắt đầu.",
                    )
                self._send_pending()
                for update in self.client.get_updates(offset):
                    offset = int(update["update_id"]) + 1
                    command = self.client.authorized_command(update)
                    if command is not None:
                        self.commands.put(command)
                        self.log_received.emit(
                            "INFO", f"Nhận lệnh Telegram từ user {command.user_id}."
                        )
                self.status_received.emit("Telegram: đã kết nối")
                self._stop_event.wait(1)
            except TelegramError as exc:
                self.status_received.emit("Telegram: mất kết nối")
                self.log_received.emit("WARN", str(exc))
                self._stop_event.wait(5 if exc.retryable else 10)
        self.status_received.emit("Telegram: đã dừng")

    def _send_pending(self) -> None:
        for _ in range(10):
            try:
                user_id, text = self._outgoing.get_nowait()
            except queue.Empty:
                return
            try:
                self.client.send_message(user_id, text)
            except TelegramError as exc:
                self.log_received.emit("WARN", str(exc))
                if exc.retryable:
                    self._outgoing.put((user_id, text))
                    return


def parse_command(command: TelegramCommand) -> Tuple[str, List[str]]:
    parts = command.text.strip().split()
    if not parts or not parts[0].startswith("/"):
        raise ValueError("Lệnh phải bắt đầu bằng dấu /.")
    name = parts[0].split("@", 1)[0].lower()
    args = parts[1:]
    if name in ("/help", "/account", "/positions", "/pnl", "/close_all"):
        if args:
            raise ValueError(f"{name} không nhận tham số.")
        return name[1:], args
    if name in ("/close",):
        if len(args) != 1:
            raise ValueError("Cú pháp: /close SYMBOL")
        _validate_symbol(args[0])
        return name[1:], [args[0].upper()]
    if name == "/cancel":
        if len(args) != 2 or not args[1].isdigit():
            raise ValueError("Cú pháp: /cancel SYMBOL ORDER_ID")
        _validate_symbol(args[0])
        return "cancel", [args[0].upper(), args[1]]
    if name == "/open":
        if len(args) not in (3, 4):
            raise ValueError(
                "Cú pháp: /open SYMBOL LONG|SHORT MARKET hoặc "
                "/open SYMBOL LONG|SHORT LIMIT PRICE"
            )
        symbol, side, order_type = args[:3]
        _validate_symbol(symbol)
        side = side.upper()
        order_type = order_type.upper()
        if side not in ("LONG", "SHORT"):
            raise ValueError("Hướng lệnh phải là LONG hoặc SHORT.")
        if order_type == "MARKET" and len(args) == 3:
            return "open", [symbol.upper(), side, order_type]
        if order_type == "LIMIT" and len(args) == 4:
            try:
                price = float(args[3])
            except ValueError as exc:
                raise ValueError("Giá LIMIT phải là số lớn hơn 0.") from exc
            if not math.isfinite(price) or price <= 0:
                raise ValueError("Giá LIMIT phải là số hữu hạn lớn hơn 0.")
            return "open", [symbol.upper(), side, order_type, args[3]]
        raise ValueError(
            "Dùng MARKET không kèm giá hoặc LIMIT kèm giá."
        )
    if name == "/confirm":
        if len(args) != 1 or len(args[0]) != 6 or not args[0].isalnum():
            raise ValueError("Cú pháp: /confirm MÃ_XÁC_NHẬN")
        return "confirm", [args[0].upper()]
    raise ValueError("Lệnh không được hỗ trợ. Gửi /help để xem cú pháp.")


def _validate_symbol(symbol: str) -> None:
    if not symbol.isalnum() or not 4 <= len(symbol) <= 20:
        raise ValueError("Mã hợp đồng không hợp lệ.")


def _split_message(message: str, limit: int = 3500) -> List[str]:
    if len(message) <= limit:
        return [message]
    chunks: List[str] = []
    current = ""
    for line in message.splitlines():
        if len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(
                line[index:index + limit]
                for index in range(0, len(line), limit)
            )
            continue
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
