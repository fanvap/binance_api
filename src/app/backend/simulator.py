"""Single-position paper trading simulator."""

import math
from dataclasses import dataclass
from typing import Optional

from app.backend.strategy import SignalAction, StrategySignal


@dataclass
class Position:
    side: str
    entry_price: float
    quantity: float
    strategy_name: str
    timeframe: Optional[str] = None


class Simulator:
    def __init__(
        self,
        starting_balance: float = 1000.0,
        order_notional: float = 100.0,
        fee_rate: float = 0.0004,
    ) -> None:
        if (
            not math.isfinite(starting_balance)
            or not math.isfinite(order_notional)
            or starting_balance <= 0
            or order_notional <= 0
        ):
            raise ValueError("Số dư ban đầu và giá trị mỗi lệnh phải lớn hơn 0.")
        if not math.isfinite(fee_rate) or fee_rate < 0:
            raise ValueError("Phí giao dịch không được âm.")
        self.starting_balance = starting_balance
        self.order_notional = order_notional
        self.fee_rate = fee_rate
        self.realized_pnl = 0.0
        self.position: Optional[Position] = None

    def update(self, signal: StrategySignal, price: float) -> None:
        if not math.isfinite(price) or price <= 0:
            raise ValueError("Giá phải là số hữu hạn lớn hơn 0.")
        if signal.action == SignalAction.HOLD:
            return
        desired_side = (
            signal.side
            if signal.action == SignalAction.SET_POSITION
            else None
        )
        if self.position is not None and self.position.side != desired_side:
            self._close(price)
        if desired_side is not None and self.position is None:
            quantity = self.order_notional / price
            self.realized_pnl -= self.order_notional * self.fee_rate
            self.position = Position(
                desired_side,
                price,
                quantity,
                signal.strategy_name or "Thủ công/Telegram",
                signal.timeframe,
            )

    def _close(self, price: float) -> None:
        position = self.position
        if position is None:
            return
        direction = 1.0 if position.side == "LONG" else -1.0
        gross_pnl = (price - position.entry_price) * position.quantity * direction
        exit_fee = price * position.quantity * self.fee_rate
        self.realized_pnl += gross_pnl - exit_fee
        self.position = None

    def unrealized_pnl(self, price: float) -> float:
        if self.position is None:
            return 0.0
        direction = 1.0 if self.position.side == "LONG" else -1.0
        gross_pnl = (price - self.position.entry_price) * self.position.quantity * direction
        estimated_exit_fee = price * self.position.quantity * self.fee_rate
        return gross_pnl - estimated_exit_fee

    @property
    def balance(self) -> float:
        return self.starting_balance + self.realized_pnl
