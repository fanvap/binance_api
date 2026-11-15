"""Position snapshots shared by future strategy and UI integrations."""

import math
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    side: str
    entry_price: float
    mark_price: float
    quantity: float
    strategy_name: str = "Thủ công/Telegram"
    leverage: Optional[float] = None
    timeframe: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("Mã hợp đồng không được để trống.")
        if self.side not in ("LONG", "SHORT"):
            raise ValueError("Hướng vị thế phải là LONG hoặc SHORT.")
        if not self.strategy_name.strip():
            raise ValueError("Tên chiến thuật không được để trống.")
        values = (self.entry_price, self.mark_price, self.quantity)
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("Giá vào, giá hiện tại và khối lượng phải lớn hơn 0.")
        if self.leverage is not None and (
            not math.isfinite(self.leverage) or self.leverage <= 0
        ):
            raise ValueError("Đòn bẩy phải là số hữu hạn lớn hơn 0.")
        if self.timeframe is not None and not self.timeframe.strip():
            raise ValueError("Khung nến không được để trống.")

    @property
    def notional_usdt(self) -> float:
        return self.entry_price * self.quantity

    @property
    def margin_usdt(self) -> Optional[float]:
        if self.leverage is None:
            return None
        return self.notional_usdt / self.leverage

    @property
    def unrealized_pnl(self) -> float:
        direction = 1.0 if self.side == "LONG" else -1.0
        return (self.mark_price - self.entry_price) * self.quantity * direction
