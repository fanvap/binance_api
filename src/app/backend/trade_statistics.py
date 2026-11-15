"""In-memory strategy trade statistics for the current application session."""

import math
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


MANUAL_TRADE_STRATEGY = "Thủ công/Telegram"


@dataclass
class _MutableStatistics:
    opened: int = 0
    closed: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    unknown_result: int = 0
    winning_pnl: float = 0.0
    losing_pnl: float = 0.0


@dataclass(frozen=True)
class StrategyTradeStatisticsRow:
    mode: str
    strategy: str
    timeframe: Optional[str]
    opened: int
    closed: int
    wins: int
    losses: int
    breakeven: int
    unknown_result: int
    winning_pnl: float
    losing_pnl: float

    @property
    def win_rate(self) -> float:
        decided_trades = self.wins + self.losses
        if decided_trades == 0:
            return 0.0
        return self.wins / decided_trades * 100

    @property
    def net_pnl(self) -> float:
        return self.winning_pnl - self.losing_pnl


class StrategyTradeStatistics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: Dict[Tuple[str, str, str], _MutableStatistics] = {}

    def record_open(
        self, mode: str, strategy: str, timeframe: Optional[str] = None
    ) -> None:
        key = self._make_key(mode, strategy, timeframe)
        with self._lock:
            row = self._rows.setdefault(key, _MutableStatistics())
            row.opened += 1

    def record_close(
        self,
        mode: str,
        strategy: str,
        net_pnl: Optional[float],
        timeframe: Optional[str] = None,
    ) -> None:
        if net_pnl is not None and not math.isfinite(net_pnl):
            raise ValueError("PnL thống kê phải là số hữu hạn.")
        key = self._make_key(mode, strategy, timeframe)
        with self._lock:
            row = self._rows.setdefault(key, _MutableStatistics())
            row.closed += 1
            if net_pnl is None:
                row.unknown_result += 1
            elif net_pnl > 0:
                row.wins += 1
                row.winning_pnl += net_pnl
            elif net_pnl < 0:
                row.losses += 1
                row.losing_pnl += abs(net_pnl)
            else:
                row.breakeven += 1

    def snapshot(self) -> List[StrategyTradeStatisticsRow]:
        with self._lock:
            return [
                StrategyTradeStatisticsRow(
                    mode=mode,
                    strategy=strategy,
                    timeframe=None if timeframe == "—" else timeframe,
                    opened=stats.opened,
                    closed=stats.closed,
                    wins=stats.wins,
                    losses=stats.losses,
                    breakeven=stats.breakeven,
                    unknown_result=stats.unknown_result,
                    winning_pnl=stats.winning_pnl,
                    losing_pnl=stats.losing_pnl,
                )
                for (mode, strategy, timeframe), stats in sorted(self._rows.items())
            ]

    @staticmethod
    def _make_key(
        mode: str, strategy: str, timeframe: Optional[str]
    ) -> Tuple[str, str, str]:
        if not mode.strip() or not strategy.strip():
            raise ValueError("Chế độ và chiến thuật thống kê không được để trống.")
        if timeframe is not None and not timeframe.strip():
            raise ValueError("Khung nến thống kê không được để trống.")
        return mode, strategy, timeframe or "—"
