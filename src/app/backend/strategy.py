"""Extension points and the built-in candle-pattern strategies."""

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Mapping, Optional, Sequence, Tuple

from app.backend.market_data import Candle


SUPPORTED_CANDLE_INTERVALS = ("1m", "3m", "5m", "15m", "1h", "4h")
CANDLE_INTERVAL_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
}


class SignalAction(str, Enum):
    HOLD = "HOLD"
    SET_POSITION = "SET_POSITION"
    CLOSE = "CLOSE"


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    price: float
    market_cap_usd: float
    candles_by_interval: Mapping[str, Tuple[Candle, ...]] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("Mã hợp đồng không được để trống.")
        if not math.isfinite(self.price) or self.price <= 0:
            raise ValueError("Giá thị trường phải là số hữu hạn lớn hơn 0.")
        if not math.isfinite(self.market_cap_usd) or self.market_cap_usd < 0:
            raise ValueError("Market cap phải là số hữu hạn không âm.")


@dataclass(frozen=True)
class StrategySignal:
    side: Optional[str]
    reason: str
    action: Optional[SignalAction] = None
    timeframe: Optional[str] = None
    strategy_name: Optional[str] = None

    def __post_init__(self) -> None:
        if self.side not in (None, "LONG", "SHORT"):
            raise ValueError("Tín hiệu phải là LONG, SHORT hoặc None.")
        action = self.action
        if action is None:
            action = (
                SignalAction.SET_POSITION
                if self.side is not None
                else SignalAction.CLOSE
            )
            object.__setattr__(self, "action", action)
        elif not isinstance(action, SignalAction):
            try:
                action = SignalAction(action)
            except ValueError as exc:
                raise ValueError("Hành động tín hiệu không hợp lệ.") from exc
            object.__setattr__(self, "action", action)
        if action == SignalAction.HOLD and self.side is not None:
            raise ValueError("Tín hiệu HOLD không được có hướng vị thế.")
        if action == SignalAction.SET_POSITION and self.side is None:
            raise ValueError("Tín hiệu SET_POSITION phải có LONG hoặc SHORT.")
        if action == SignalAction.CLOSE and self.side is not None:
            raise ValueError("Tín hiệu CLOSE không được có hướng vị thế.")


class Strategy(ABC):
    """Base interface for future strategies."""

    @property
    def candle_intervals(self) -> Tuple[str, ...]:
        return ()

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the strategy's display name."""

    @abstractmethod
    def evaluate(self, market: MarketSnapshot) -> StrategySignal:
        """Analyze a market snapshot without placing orders."""


class NoStrategy(Strategy):
    @property
    def name(self) -> str:
        return "Chưa cấu hình"

    def evaluate(self, market: MarketSnapshot) -> StrategySignal:
        return StrategySignal(
            None,
            f"{market.symbol}: chưa thêm chiến thuật; không phát tín hiệu giao dịch.",
            SignalAction.HOLD,
        )


class CandlePatternStrategy(Strategy):
    def __init__(
        self, name: str, side: str, candle_intervals: Sequence[str]
    ) -> None:
        if side not in ("LONG", "SHORT"):
            raise ValueError("Hướng chiến thuật phải là LONG hoặc SHORT.")
        intervals = tuple(dict.fromkeys(candle_intervals))
        if not intervals or any(
            interval not in CANDLE_INTERVAL_SECONDS for interval in intervals
        ):
            raise ValueError("Phải chọn ít nhất một khung nến hợp lệ.")
        self._name = name
        self._side = side
        self._candle_intervals = intervals
        self._triggered_candles: Dict[Tuple[str, str], int] = {}

    @property
    def name(self) -> str:
        return self._name

    @property
    def candle_intervals(self) -> Tuple[str, ...]:
        return self._candle_intervals

    def evaluate(self, market: MarketSnapshot) -> StrategySignal:
        matched_timeframe: Optional[str] = None
        for interval in self._candle_intervals:
            candles = market.candles_by_interval.get(interval, ())
            if len(candles) < 2:
                continue
            previous, current = candles[-2:]
            candle_key = (market.symbol, interval)
            if self._triggered_candles.get(candle_key) == current.open_time:
                continue

            if self._matches(previous, current):
                self._triggered_candles[candle_key] = current.open_time
                if matched_timeframe is None:
                    matched_timeframe = interval
        if matched_timeframe is not None:
            return StrategySignal(
                self._side,
                f"{self._name}: mẫu hình được xác nhận trên khung "
                f"{matched_timeframe}.",
                SignalAction.SET_POSITION,
                matched_timeframe,
                self._name,
            )
        return StrategySignal(
            None,
            f"{market.symbol}: chưa có mẫu hình {self._name}.",
            SignalAction.HOLD,
        )

    def _matches(self, previous: Candle, current: Candle) -> bool:
        candle_range = current.high - current.low
        body = abs(current.close - current.open)
        if candle_range <= 0 or body <= 0:
            return False

        if self._side == "SHORT":
            upper_wick = current.high - max(current.open, current.close)
            close_position = (current.close - current.low) / candle_range
            return (
                current.high <= previous.high * 1.01
                and current.high > current.open
                and upper_wick >= 2 * body
                and close_position <= 0.25
            )

        lower_wick = min(current.open, current.close) - current.low
        close_position = (current.close - current.low) / candle_range
        return (
            current.low < previous.low
            and lower_wick >= 2 * body
            and close_position >= 0.75
        )


class StrategyFactory:
    @staticmethod
    def create(
        name: str = "short_p",
        candle_intervals: Sequence[str] = SUPPORTED_CANDLE_INTERVALS,
    ) -> Strategy:
        if name == "short_p":
            return CandlePatternStrategy("short_p", "SHORT", candle_intervals)
        if name == "long_p":
            return CandlePatternStrategy("long_p", "LONG", candle_intervals)
        if name == "none":
            return NoStrategy()
        raise ValueError(f"Chiến thuật không hợp lệ: {name}")
