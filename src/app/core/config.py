"""Environment-based configuration."""

import math
import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    testnet_trading_enabled: bool
    api_key: Optional[str]
    api_secret: Optional[str]
    max_notional_usdt: Optional[float]


def load_settings() -> Settings:
    raw_notional = os.getenv("BINANCE_TESTNET_MAX_NOTIONAL_USDT", "").strip()
    max_notional = float(raw_notional) if raw_notional else None
    if max_notional is not None and (
        not math.isfinite(max_notional) or max_notional <= 0
    ):
        raise ValueError(
            "BINANCE_TESTNET_MAX_NOTIONAL_USDT phải là số hữu hạn lớn hơn 0."
        )
    return Settings(
        testnet_trading_enabled=os.getenv("BINANCE_TESTNET_TRADING_ENABLED", "").lower() == "true",
        api_key=os.getenv("BINANCE_TESTNET_API_KEY") or None,
        api_secret=os.getenv("BINANCE_TESTNET_API_SECRET") or None,
        max_notional_usdt=max_notional,
    )


def validate_testnet_settings(settings: Settings) -> None:
    if not settings.testnet_trading_enabled:
        raise RuntimeError("Testnet bị khóa. Đặt BINANCE_TESTNET_TRADING_ENABLED=true để mở.")
    if not settings.api_key or not settings.api_secret:
        raise RuntimeError("Cần cấu hình API key và secret của Binance Futures Testnet.")
    if settings.max_notional_usdt is None:
        raise RuntimeError("Cần đặt BINANCE_TESTNET_MAX_NOTIONAL_USDT làm giới hạn mỗi lệnh.")
