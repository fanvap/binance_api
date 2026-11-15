"""Pluggable execution modes for simulated and Binance Futures Testnet orders."""

import math
import secrets
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set, Tuple

from app.backend.binance_client import BinanceClient
from app.backend.positions import PositionSnapshot
from app.backend.simulator import Simulator
from app.backend.strategy import MarketSnapshot, SignalAction, StrategySignal
from app.backend.trade_statistics import (
    MANUAL_TRADE_STRATEGY,
    StrategyTradeStatistics,
)
from app.backend.trading_constants import TRADE_LEVERAGE

SIMULATION_STARTING_BALANCE = 1000.0
MAX_ORDER_MARGIN = 50.0
SIMULATION_MAX_ORDER_MARGIN = MAX_ORDER_MARGIN
SIMULATION_MAX_ORDER_NOTIONAL = SIMULATION_MAX_ORDER_MARGIN * TRADE_LEVERAGE
SIMULATION_MAX_OPEN_POSITIONS = int(
    SIMULATION_STARTING_BALANCE / SIMULATION_MAX_ORDER_MARGIN
)
PORTFOLIO_PROFIT_TARGET_PCT = 0.5


class ExecutionMode(ABC):
    """Consumes strategy signals and exposes positions and session PnL."""

    @abstractmethod
    def update(
        self, market: MarketSnapshot, signal: StrategySignal
    ) -> List[str]:
        """Apply one strategy result and return user-visible execution events."""

    @abstractmethod
    def positions(self) -> List[PositionSnapshot]:
        """Return current open positions."""

    @abstractmethod
    def update_market_prices(self, prices: Dict[str, float]) -> None:
        """Update market prices used by the execution mode where applicable."""

    @abstractmethod
    def close_positions_at_profit_target(
        self, positions: List[PositionSnapshot]
    ) -> List[str]:
        """Close app-managed positions when aggregate open net PnL reaches target."""

    @abstractmethod
    def account_summary(self) -> Dict[str, float]:
        """Return wallet/equity, available balance, and unrealized PnL."""

    @property
    @abstractmethod
    def realized_pnl(self) -> float:
        """Return session realized PnL, including estimated fees where applicable."""

    def execute_telegram_command(
        self, name: str, args: List[str], user_id: int
    ) -> str:
        if name == "help":
            return (
                "Trong Simulation, Telegram chỉ gửi thông báo và PnL; "
                "các lệnh truy vấn/điều khiển từ xa chỉ khả dụng ở Binance Testnet. "
                "Gửi /help khi chạy Testnet để xem danh sách."
            )
        raise RuntimeError(
            "Lệnh điều khiển từ xa chỉ hỗ trợ ở chế độ Binance Testnet; "
            "Telegram vẫn có thể gửi thông báo trong Simulation."
        )


class SimulationExecutionMode(ExecutionMode):
    def __init__(
        self,
        order_margin: float,
        max_open_positions: int,
        take_profit_pct: float = 1.0,
        stop_loss_pct: float = 1.0,
        reverse_action: str = "close_only",
        fee_rate: float = 0.0004,
        starting_balance: float = SIMULATION_STARTING_BALANCE,
        trade_statistics: Optional[StrategyTradeStatistics] = None,
    ) -> None:
        _validate_limits(order_margin, max_open_positions)
        if order_margin > SIMULATION_MAX_ORDER_MARGIN:
            raise ValueError(
                "Ký quỹ mỗi lệnh Simulation không được vượt "
                f"{SIMULATION_MAX_ORDER_MARGIN:.2f} USDT."
            )
        if max_open_positions > SIMULATION_MAX_OPEN_POSITIONS:
            raise ValueError(
                "Simulation không thể mở quá "
                f"{SIMULATION_MAX_OPEN_POSITIONS} vị thế cùng lúc."
            )
        if (
            isinstance(starting_balance, bool)
            or not isinstance(starting_balance, (int, float))
            or not math.isfinite(starting_balance)
            or starting_balance <= 0
        ):
            raise ValueError("Vốn mô phỏng phải là số hữu hạn lớn hơn 0.")
        _validate_risk_limits(take_profit_pct, stop_loss_pct, reverse_action)
        if (
            isinstance(fee_rate, bool)
            or not isinstance(fee_rate, (int, float))
            or not math.isfinite(fee_rate)
            or fee_rate < 0
        ):
            raise ValueError("Phí giao dịch phải là số hữu hạn và không âm.")
        self.order_margin = float(order_margin)
        self.order_notional = self.order_margin * TRADE_LEVERAGE
        self.max_open_positions = max_open_positions
        self.take_profit_pct = take_profit_pct
        self.stop_loss_pct = stop_loss_pct
        self.reverse_action = reverse_action
        self.fee_rate = fee_rate
        self.starting_balance = float(starting_balance)
        self.trade_statistics = trade_statistics
        self._simulators: Dict[str, Simulator] = {}
        self._market_prices: Dict[str, float] = {}
        self._market_snapshots: Dict[str, MarketSnapshot] = {}
        self._entry_fees: Dict[str, float] = {}
        self._entry_candles: Dict[str, Tuple[str, int]] = {}
        self._reentry_after_candles: Dict[str, Tuple[str, int]] = {}
        self._reported_limits = set()
        self._realized_pnl = 0.0

    def update(
        self, market: MarketSnapshot, signal: StrategySignal
    ) -> List[str]:
        self._market_prices[market.symbol] = market.price
        self._market_snapshots[market.symbol] = market
        simulator = self._simulators.get(market.symbol)
        position = simulator.position if simulator is not None else None

        if position is not None:
            _estimated_pnl, pnl_percent = _estimate_net_pnl(
                position.side,
                position.entry_price,
                position.quantity,
                market.price,
                self.fee_rate,
            )
            reason = _risk_exit_reason(
                pnl_percent, self.take_profit_pct, self.stop_loss_pct
            )
            if reason is not None:
                realized = self._close_position(market.symbol, market.price)
                return [
                    f"SIMULATION: {reason} {market.symbol}; "
                    f"PnL đã chốt {realized:+.4f} USDT."
                ]

            if signal.action == SignalAction.HOLD:
                return []
            if signal.action == SignalAction.CLOSE:
                realized = self._close_position(market.symbol, market.price)
                return [
                    f"SIMULATION: đóng {market.symbol}; "
                    f"PnL đã chốt {realized:+.4f} USDT."
                ]
            if signal.side == position.side:
                return []

            entry_candle = self._entry_candles.get(market.symbol)
            if entry_candle is not None:
                timeframe, entry_open_time = entry_candle
                current_open_time = _current_candle_open_time(
                    market, timeframe
                )
                if (
                    signal.timeframe != timeframe
                    or current_open_time is None
                    or current_open_time <= entry_open_time
                ):
                    return []
                realized = self._close_position(market.symbol, market.price)
                self._reentry_after_candles[market.symbol] = (
                    timeframe,
                    current_open_time,
                )
                return [
                    f"SIMULATION: tín hiệu đảo chiều sau khi nến vào lệnh đóng, "
                    f"đóng bảo vệ {market.symbol}; PnL đã chốt "
                    f"{realized:+.4f} USDT."
                ]

            realized = self._close_position(market.symbol, market.price)
            if self.reverse_action == "close_only":
                return [
                    f"SIMULATION: tín hiệu ngược chiều, đóng {market.symbol}; "
                    f"PnL đã chốt {realized:+.4f} USDT."
                ]
            close_message = (
                f"SIMULATION: đóng {market.symbol}; PnL đã chốt {realized:+.4f} USDT."
            )
            try:
                open_message = self._open_position(market, signal)
            except RuntimeError as exc:
                return [
                    close_message,
                    f"SIMULATION: bỏ qua mở lại {market.symbol}; {exc}",
                ]
            return [close_message, open_message]

        if signal.action != SignalAction.SET_POSITION:
            return []
        if _is_same_or_earlier_candle(
            market, signal, self._reentry_after_candles.get(market.symbol)
        ):
            return []
        if len(self._simulators) >= self.max_open_positions:
            if market.symbol not in self._reported_limits:
                self._reported_limits.add(market.symbol)
                return [
                    f"SIMULATION: bỏ qua {market.symbol}; đã đạt giới hạn "
                    f"{self.max_open_positions} vị thế mở."
                ]
            return []
        rejection = _opposite_closed_candle_rejection(market, signal)
        if rejection is not None:
            return [
                f"SIMULATION: bỏ qua mở {market.symbol}; {rejection}"
            ]
        try:
            return [self._open_position(market, signal)]
        except RuntimeError as exc:
            if market.symbol in self._reported_limits:
                return []
            self._reported_limits.add(market.symbol)
            return [f"SIMULATION: bỏ qua {market.symbol}; {exc}"]

    def update_market_prices(self, prices: Dict[str, float]) -> None:
        self._market_prices.update(prices)

    def _open_position(
        self, market: MarketSnapshot, signal: StrategySignal
    ) -> str:
        self._ensure_can_open(market.symbol)
        rejection = _opposite_closed_candle_rejection(market, signal)
        if rejection is not None:
            raise RuntimeError(rejection)
        simulator = Simulator(
            starting_balance=self.order_margin,
            order_notional=self.order_notional,
            fee_rate=self.fee_rate,
        )
        simulator.update(signal, market.price)
        self._simulators[market.symbol] = simulator
        self._entry_fees[market.symbol] = self.order_notional * self.fee_rate
        entry_candle = _signal_candle_reference(market, signal)
        if entry_candle is None:
            self._entry_candles.pop(market.symbol, None)
        else:
            self._entry_candles[market.symbol] = entry_candle
        self._reentry_after_candles.pop(market.symbol, None)
        self._reported_limits.discard(market.symbol)
        position = simulator.position
        if position is None:
            raise RuntimeError("Không thể tạo vị thế mô phỏng từ tín hiệu.")
        if self.trade_statistics is not None:
            self.trade_statistics.record_open(
                "Simulation", position.strategy_name, position.timeframe
            )
        return (
            f"SIMULATION: mở {position.side} {market.symbol} "
            f"ký quỹ {self.order_margin:.2f} USDT, đòn bẩy x{TRADE_LEVERAGE}, "
            f"vị thế {self.order_notional:.2f} USDT."
        )

    def _ensure_can_open(self, symbol: str) -> None:
        if symbol in self._simulators:
            raise RuntimeError(f"{symbol} đã có vị thế Simulation đang mở.")
        if self.available_balance + 1e-9 < self.order_margin:
            raise RuntimeError(
                "Vượt số tiền mô phỏng: cần ký quỹ "
                f"{self.order_margin:.2f} USDT nhưng số dư khả dụng chỉ còn "
                f"{self.available_balance:.2f} USDT."
            )
        if len(self._simulators) >= self.max_open_positions:
            raise RuntimeError(
                f"Đã đạt giới hạn {self.max_open_positions} vị thế Simulation."
            )

    def _close_position(self, symbol: str, price: float) -> float:
        simulator = self._simulators.pop(symbol)
        self._entry_candles.pop(symbol, None)
        position = simulator.position
        realized_before = simulator.realized_pnl
        simulator.update(StrategySignal(None, "close"), price)
        realized = (
            simulator.realized_pnl
            - realized_before
            - self._entry_fees.pop(symbol, 0.0)
        )
        self._realized_pnl += realized
        if self.trade_statistics is not None and position is not None:
            self.trade_statistics.record_close(
                "Simulation", position.strategy_name, realized, position.timeframe
            )
        return realized

    def positions(self) -> List[PositionSnapshot]:
        result: List[PositionSnapshot] = []
        for symbol, simulator in self._simulators.items():
            position = simulator.position
            if position is None:
                continue
            result.append(
                PositionSnapshot(
                    symbol=symbol,
                    side=position.side,
                    entry_price=position.entry_price,
                    mark_price=self._market_prices.get(symbol, position.entry_price),
                    quantity=position.quantity,
                    strategy_name=position.strategy_name,
                    leverage=float(TRADE_LEVERAGE),
                    timeframe=position.timeframe,
                )
            )
        return result

    @property
    def open_margin(self) -> float:
        return sum(
            simulator.order_notional / TRADE_LEVERAGE
            for simulator in self._simulators.values()
        )

    @property
    def open_notional(self) -> float:
        return self.open_margin * TRADE_LEVERAGE

    @property
    def available_balance(self) -> float:
        return self.starting_balance + self._realized_pnl - self.open_margin

    @property
    def unrealized_pnl(self) -> float:
        total_pnl = 0.0
        for symbol, simulator in self._simulators.items():
            position = simulator.position
            if position is None:
                continue
            total_pnl += _estimate_net_pnl(
                position.side,
                position.entry_price,
                position.quantity,
                self._market_prices.get(symbol, position.entry_price),
                self.fee_rate,
            )[0]
        return total_pnl

    def close_positions_at_profit_target(
        self, positions: List[PositionSnapshot]
    ) -> List[str]:
        if not positions:
            return []
        threshold = self.starting_balance * PORTFOLIO_PROFIT_TARGET_PCT / 100
        open_profit = self.unrealized_pnl
        if open_profit <= threshold:
            return []

        closed_count = 0
        realized_total = 0.0
        for symbol in list(self._simulators):
            simulator = self._simulators[symbol]
            position = simulator.position
            if position is None:
                continue
            price = self._market_prices.get(symbol, position.entry_price)
            realized_total += self._close_position(symbol, price)
            closed_count += 1
        return [
            f"SIMULATION: tổng PnL ròng đang mở {open_profit:+.4f} USDT vượt "
            f"{PORTFOLIO_PROFIT_TARGET_PCT:.2f}% vốn "
            f"({threshold:.4f} USDT); đã đóng {closed_count} vị thế, "
            f"PnL chốt {realized_total:+.4f} USDT."
        ]

    @property
    def equity(self) -> float:
        return self.starting_balance + self._realized_pnl + self.unrealized_pnl

    def account_summary(self) -> Dict[str, float]:
        return {
            "wallet_balance": self.starting_balance + self.realized_pnl,
            "available_balance": self.available_balance,
            "unrealized_pnl": self.unrealized_pnl,
            "equity": self.equity,
        }

    def execute_telegram_command(
        self, name: str, args: List[str], user_id: int
    ) -> str:
        if name == "help":
            return (
                "/account - số dư và PnL Simulation\n"
                "/positions - vị thế Simulation đang mở\n"
                "/pnl - PnL đã chốt và chưa chốt\n"
                "/open SYMBOL LONG|SHORT MARKET - mở lệnh mô phỏng\n"
                "/close SYMBOL - đóng vị thế mô phỏng\n"
                "/close_all - đóng tất cả vị thế mô phỏng"
            )
        if name == "account":
            return (
                "Simulation\n"
                f"Vốn ban đầu: {self.starting_balance:.2f} USDT\n"
                f"Số dư khả dụng: {self.available_balance:.2f} USDT\n"
                f"Equity gồm PnL chưa chốt: {self.equity:.2f} USDT\n"
                f"Ký quỹ đang dùng: {self.open_margin:.2f} USDT\n"
                f"Giá trị vị thế đang mở: {self.open_notional:.2f} USDT "
                f"(đòn bẩy x{TRADE_LEVERAGE})\n"
                f"Vị thế mở: {len(self._simulators)}/{self.max_open_positions}\n"
                f"PnL đã chốt: {self.realized_pnl:+.4f} USDT\n"
                f"PnL chưa chốt: {self.unrealized_pnl:+.4f} USDT"
            )
        if name == "positions":
            open_positions = self.positions()
            if not open_positions:
                return "Không có vị thế Simulation đang mở."
            lines = ["Vị thế Simulation:"]
            for position in open_positions:
                pnl, _ = _estimate_net_pnl(
                    position.side,
                    position.entry_price,
                    position.quantity,
                    position.mark_price,
                    self.fee_rate,
                )
                lines.append(
                    f"{position.symbol} {position.side} | chiến thuật "
                    f"{position.strategy_name}"
                    f"{' · ' + position.timeframe if position.timeframe else ''} | entry "
                    f"{position.entry_price:.8g} | mark {position.mark_price:.8g} | "
                    f"đòn bẩy x{TRADE_LEVERAGE} | ký quỹ "
                    f"{position.margin_usdt:.2f} USDT | vị thế "
                    f"{position.notional_usdt:.2f} USDT | qty "
                    f"{position.quantity:.8g} | PnL ròng {pnl:+.4f} USDT"
                )
            return "\n".join(lines)
        if name == "pnl":
            return (
                "Simulation PnL\n"
                f"Đã chốt: {self.realized_pnl:+.4f} USDT\n"
                f"Chưa chốt (ước tính ròng): {self.unrealized_pnl:+.4f} USDT\n"
                f"Tổng PnL: {self.realized_pnl + self.unrealized_pnl:+.4f} USDT"
            )
        if name == "open":
            return self._telegram_open(args)
        if name == "close":
            return self._telegram_close(args[0])
        if name == "close_all":
            return self._telegram_close_all()
        if name in ("cancel", "confirm"):
            raise RuntimeError(
                "Simulation không hỗ trợ lệnh chờ hoặc lệnh xác nhận."
            )
        raise ValueError("Lệnh Telegram không được hỗ trợ.")

    def _telegram_open(self, args: List[str]) -> str:
        symbol, side, order_type = args[:3]
        if order_type != "MARKET":
            raise RuntimeError(
                "Simulation chỉ hỗ trợ lệnh MARKET; lệnh LIMIT chờ khớp chưa được mô phỏng."
            )
        market = self._market_snapshots.get(symbol)
        if market is None:
            raise RuntimeError(
                f"Chưa có giá {symbol}; hãy đợi mã xuất hiện trong phiên quét."
            )
        return self._open_position(market, StrategySignal(side, "Telegram /open"))

    def _telegram_close(self, symbol: str) -> str:
        if symbol not in self._simulators:
            raise RuntimeError(f"Không có vị thế Simulation {symbol} đang mở.")
        price = self._market_prices.get(symbol)
        if price is None:
            raise RuntimeError(f"Chưa có giá mới nhất để đóng {symbol}.")
        realized = self._close_position(symbol, price)
        return (
            f"Đã đóng vị thế Simulation {symbol}; "
            f"PnL ròng {realized:+.4f} USDT."
        )

    def _telegram_close_all(self) -> str:
        if not self._simulators:
            return "Không có vị thế Simulation đang mở."
        closed_count = 0
        total_realized = 0.0
        for symbol in list(self._simulators):
            position = self._simulators[symbol].position
            if position is None:
                continue
            price = self._market_prices.get(symbol, position.entry_price)
            total_realized += self._close_position(symbol, price)
            closed_count += 1
        return (
            f"Đã đóng {closed_count} vị thế Simulation; "
            f"PnL ròng {total_realized:+.4f} USDT."
        )

    @property
    def realized_pnl(self) -> float:
        return self._realized_pnl


class TestnetExecutionMode(ExecutionMode):
    def __init__(
        self,
        client: BinanceClient,
        order_margin: float,
        max_open_positions: int,
        take_profit_pct: float = 1.0,
        stop_loss_pct: float = 1.0,
        reverse_action: str = "close_only",
        fee_rate: float = 0.0004,
        trade_statistics: Optional[StrategyTradeStatistics] = None,
    ) -> None:
        _validate_limits(order_margin, max_open_positions)
        if order_margin > MAX_ORDER_MARGIN:
            raise ValueError(
                f"Ký quỹ mỗi lệnh không được vượt {MAX_ORDER_MARGIN:.2f} USDT."
            )
        _validate_risk_limits(take_profit_pct, stop_loss_pct, reverse_action)
        if (
            isinstance(fee_rate, bool)
            or not isinstance(fee_rate, (int, float))
            or not math.isfinite(fee_rate)
            or fee_rate < 0
        ):
            raise ValueError("Phí giao dịch phải là số hữu hạn và không âm.")
        self.client = client
        self.order_margin = float(order_margin)
        self.order_notional = self.order_margin * TRADE_LEVERAGE
        self.max_open_positions = max_open_positions
        self.take_profit_pct = take_profit_pct
        self.stop_loss_pct = stop_loss_pct
        self.reverse_action = reverse_action
        self.fee_rate = fee_rate
        self.trade_statistics = trade_statistics
        self._managed_symbols = set()
        self._strategy_by_symbol: Dict[str, str] = {}
        self._timeframe_by_symbol: Dict[str, Optional[str]] = {}
        self._registered_open_symbols: Set[str] = set()
        self._reported_limits = set()
        self._reported_external_positions = set()
        self._realized_pnl = 0.0
        self._profit_target_wallet_balance: Optional[float] = None
        self._managed_order_ids: Dict[str, int] = {}
        self._entry_candles: Dict[str, Tuple[str, int]] = {}
        self._reentry_after_candles: Dict[str, Tuple[str, int]] = {}
        self._close_all_confirmations: Dict[int, Tuple[str, float]] = {}

    def account_summary(self) -> Dict[str, float]:
        summary = self.client.get_account_summary()
        self._profit_target_wallet_balance = float(summary["wallet_balance"])
        summary["equity"] = (
            summary["wallet_balance"] + summary["unrealized_pnl"]
        )
        return summary

    def update(
        self, market: MarketSnapshot, signal: StrategySignal
    ) -> List[str]:
        is_managed = market.symbol in self._managed_symbols
        if not is_managed and signal.action != SignalAction.SET_POSITION:
            return []

        open_positions = self.client.get_open_positions()
        exchange_position = next(
            (item for item in open_positions if item.get("symbol") == market.symbol),
            None,
        )

        if is_managed:
            if exchange_position is None:
                order_id = self._managed_order_ids.get(market.symbol)
                if order_id is not None:
                    pending_orders = self.client.get_open_orders(market.symbol)
                    if any(int(order.get("orderId", -1)) == order_id for order in pending_orders):
                        return []
                    open_positions = self.client.get_open_positions()
                    exchange_position = next(
                        (
                            item for item in open_positions
                            if item.get("symbol") == market.symbol
                        ),
                        None,
                    )
                    if exchange_position is not None:
                        self._managed_order_ids.pop(market.symbol, None)
                    else:
                        self._managed_order_ids.pop(market.symbol, None)
                        self._register_close(market.symbol, None)
                        self._managed_symbols.discard(market.symbol)
                        return [
                            f"Testnet: lệnh LIMIT {market.symbol} đã khớp, hủy "
                            "hoặc hết hiệu lực mà không còn vị thế mở."
                        ]
                else:
                    self._register_close(market.symbol, None)
                    self._managed_symbols.discard(market.symbol)
                    return [f"Testnet: vị thế {market.symbol} đã đóng trên sàn."]
            if exchange_position is None:
                return []
            self._managed_order_ids.pop(market.symbol, None)
            self._register_open(
                market.symbol,
                self._strategy_by_symbol.get(market.symbol),
                self._timeframe_by_symbol.get(market.symbol),
            )
            current = _position_snapshot(exchange_position)
            estimated_pnl, pnl_percent = _estimate_net_pnl(
                current.side,
                current.entry_price,
                current.quantity,
                current.mark_price,
                self.fee_rate,
            )
            reason = _risk_exit_reason(
                pnl_percent, self.take_profit_pct, self.stop_loss_pct
            )
            if reason is not None:
                self.client.close_position(market.symbol)
                self._register_close(market.symbol, estimated_pnl)
                self._managed_symbols.discard(market.symbol)
                self._realized_pnl += estimated_pnl
                return [
                    f"TESTNET: {reason} {market.symbol}; "
                    f"PnL đã chốt ước tính {estimated_pnl:+.4f} USDT."
                ]
            if signal.action == SignalAction.HOLD:
                return []
            if signal.action == SignalAction.CLOSE:
                self.client.close_position(market.symbol)
                self._register_close(market.symbol, estimated_pnl)
                self._managed_symbols.discard(market.symbol)
                self._realized_pnl += estimated_pnl
                return [
                    f"TESTNET: đóng {market.symbol}; "
                    f"PnL đã chốt ước tính {estimated_pnl:+.4f} USDT."
                ]
            if signal.side == current.side:
                return []

            entry_candle = self._entry_candles.get(market.symbol)
            if entry_candle is not None:
                timeframe, entry_open_time = entry_candle
                current_open_time = _current_candle_open_time(
                    market, timeframe
                )
                if (
                    signal.timeframe != timeframe
                    or current_open_time is None
                    or current_open_time <= entry_open_time
                ):
                    return []
                self.client.close_position(market.symbol)
                self._register_close(market.symbol, estimated_pnl)
                self._managed_symbols.discard(market.symbol)
                self._realized_pnl += estimated_pnl
                self._reentry_after_candles[market.symbol] = (
                    timeframe,
                    current_open_time,
                )
                return [
                    f"TESTNET: tín hiệu đảo chiều sau khi nến vào lệnh đóng, "
                    f"đóng bảo vệ {market.symbol}; PnL đã chốt ước tính "
                    f"{estimated_pnl:+.4f} USDT."
                ]

            self.client.close_position(market.symbol)
            self._register_close(market.symbol, estimated_pnl)
            self._managed_symbols.discard(market.symbol)
            self._realized_pnl += estimated_pnl
            close_message = (
                f"TESTNET: tín hiệu ngược chiều, đóng {market.symbol}; "
                f"PnL đã chốt ước tính {estimated_pnl:+.4f} USDT."
            )
            if self.reverse_action == "close_only":
                return [close_message]
            open_positions = self.client.get_open_positions()
            open_orders = self.client.get_open_orders()
            return [
                close_message,
                *self._open_position(market, signal, open_positions, open_orders),
            ]

        if exchange_position is not None:
            if market.symbol in self._reported_external_positions:
                return []
            self._reported_external_positions.add(market.symbol)
            return [
                f"TESTNET: bỏ qua {market.symbol}; tài khoản đã có vị thế ngoài phiên này."
            ]
        open_orders = self.client.get_open_orders()
        if len(_occupied_symbols(open_positions, open_orders)) >= self.max_open_positions:
            if market.symbol not in self._reported_limits:
                self._reported_limits.add(market.symbol)
                return [
                    f"TESTNET: bỏ qua {market.symbol}; đã đạt giới hạn "
                    f"{self.max_open_positions} vị thế mở."
                ]
            return []
        return self._open_position(market, signal, open_positions, open_orders)

    def _open_position(
        self,
        market: MarketSnapshot,
        signal: StrategySignal,
        open_positions: List[Dict[str, Any]],
        open_orders: List[Dict[str, Any]],
    ) -> List[str]:
        if signal.action != SignalAction.SET_POSITION or signal.side is None:
            raise ValueError("Chỉ có thể đặt lệnh từ tín hiệu LONG hoặc SHORT.")
        if _is_same_or_earlier_candle(
            market, signal, self._reentry_after_candles.get(market.symbol)
        ):
            return []
        if market.symbol in _occupied_symbols(open_positions, open_orders):
            return []
        if len(_occupied_symbols(open_positions, open_orders)) >= self.max_open_positions:
            return []
        rejection = _opposite_closed_candle_rejection(market, signal)
        if rejection is not None:
            return [
                f"TESTNET: bỏ qua mở {market.symbol}; {rejection}"
            ]
        price = self.client.get_price(market.symbol)
        self.client.enter_position(
            market.symbol, signal.side, price, self.order_notional
        )
        self._managed_symbols.add(market.symbol)
        positions_after_order = self.client.get_open_positions()
        opened_position = next(
            (
                item
                for item in positions_after_order
                if item.get("symbol") == market.symbol
            ),
            None,
        )
        if opened_position is None:
            self._managed_symbols.discard(market.symbol)
            raise RuntimeError(
                f"Binance Testnet không xác nhận vị thế {market.symbol} sau khi đặt lệnh."
            )
        self._register_open(
            market.symbol,
            signal.strategy_name or MANUAL_TRADE_STRATEGY,
            signal.timeframe,
        )
        entry_candle = _signal_candle_reference(market, signal)
        if entry_candle is None:
            self._entry_candles.pop(market.symbol, None)
        else:
            self._entry_candles[market.symbol] = entry_candle
        self._reentry_after_candles.pop(market.symbol, None)
        self._reported_limits.discard(market.symbol)
        self._reported_external_positions.discard(market.symbol)
        return [
            f"TESTNET: đã mở {signal.side} {market.symbol}, "
            f"ký quỹ {self.order_margin:.2f} USDT, đòn bẩy "
            f"x{TRADE_LEVERAGE}, vị thế tối đa {self.order_notional:.2f} USDT."
        ]

    def execute_telegram_command(
        self, name: str, args: List[str], user_id: int
    ) -> str:
        if name == "help":
            return (
                "/account - số dư và vị thế\n"
                "/positions - vị thế đang mở\n"
                "/open SYMBOL LONG|SHORT MARKET\n"
                "/open SYMBOL LONG|SHORT LIMIT PRICE\n"
                "/close SYMBOL - đóng vị thế\n"
                "/cancel SYMBOL ORDER_ID - hủy lệnh chờ\n"
                "/close_all - yêu cầu xác nhận đóng tất cả\n"
                "/confirm CODE - xác nhận trong 60 giây"
            )
        if name == "account":
            account = self.account_summary()
            positions = self.client.get_open_positions()
            return (
                "Binance Futures Testnet\n"
                f"Ví: {account['wallet_balance']:.4f} USDT\n"
                f"Khả dụng: {account['available_balance']:.4f} USDT\n"
                f"PnL chưa chốt: {account['unrealized_pnl']:+.4f} USDT\n"
                f"Equity: {account['equity']:.4f} USDT\n"
                f"Vị thế mở: {len(positions)}"
            )
        if name == "positions":
            positions = self.client.get_open_positions()
            if not positions:
                return "Không có vị thế Futures Testnet đang mở."
            lines = ["Vị thế Binance Futures Testnet:"]
            for position in positions:
                symbol = str(position["symbol"])
                snapshot = _position_snapshot(
                    position,
                    leverage=(
                        float(TRADE_LEVERAGE)
                        if symbol in self._managed_symbols
                        else None
                    ),
                    timeframe=self._timeframe_by_symbol.get(symbol),
                )
                leverage_label = (
                    f"x{snapshot.leverage:g}"
                    if snapshot.leverage is not None
                    else "chưa rõ"
                )
                margin_label = (
                    f"{snapshot.margin_usdt:.2f}"
                    if snapshot.margin_usdt is not None
                    else "chưa rõ"
                )
                lines.append(
                    f"{snapshot.symbol} {snapshot.side} | chiến thuật "
                    f"{self._strategy_by_symbol.get(snapshot.symbol, 'Ngoài phiên')}"
                    f"{' · ' + snapshot.timeframe if snapshot.timeframe else ''} | entry "
                    f"{snapshot.entry_price:.8g} | mark {snapshot.mark_price:.8g} | "
                    f"đòn bẩy {leverage_label} | ký quỹ "
                    f"{margin_label} USDT | vị thế "
                    f"{snapshot.notional_usdt:.2f} USDT | qty "
                    f"{snapshot.quantity:.8g} | PnL "
                    f"{snapshot.unrealized_pnl:+.4f} USDT"
                )
            total_pnl = sum(
                _position_snapshot(position).unrealized_pnl
                for position in positions
            )
            lines.append(f"Tổng PnL chưa chốt: {total_pnl:+.4f} USDT")
            return "\n".join(lines)
        if name == "open":
            return self._telegram_open(args)
        if name == "close":
            return self._telegram_close(args[0])
        if name == "cancel":
            return self._telegram_cancel(args[0], int(args[1]))
        if name == "close_all":
            code = secrets.token_hex(3).upper()
            self._close_all_confirmations[user_id] = (
                code,
                time.monotonic() + 60,
            )
            return (
                "Yêu cầu đóng mọi vị thế và hủy mọi lệnh chờ trong tài khoản "
                "Testnet. Nếu chắc chắn, gửi /confirm "
                f"{code} trong 60 giây."
            )
        if name == "confirm":
            return self._confirm_close_all(user_id, args[0])
        raise ValueError("Lệnh Telegram không được hỗ trợ.")

    def _telegram_open(self, args: List[str]) -> str:
        symbol, side, order_type = args[:3]
        open_positions = self.client.get_open_positions()
        open_orders = self.client.get_open_orders()
        occupied = _occupied_symbols(open_positions, open_orders)
        if symbol in occupied:
            raise RuntimeError(f"{symbol} đã có vị thế hoặc lệnh chờ.")
        if len(occupied) >= self.max_open_positions:
            raise RuntimeError(
                f"Đã đạt giới hạn {self.max_open_positions} vị thế/lệnh chờ."
            )
        if order_type == "MARKET":
            price = self.client.get_price(symbol)
            self.client.enter_position(
                symbol, side, price, self.order_notional
            )
            self._managed_symbols.add(symbol)
            position = next(
                (
                    item for item in self.client.get_open_positions()
                    if item.get("symbol") == symbol
                ),
                None,
            )
            if position is None:
                self._managed_symbols.discard(symbol)
                raise RuntimeError(
                    f"Testnet chưa xác nhận vị thế {symbol} sau lệnh MARKET."
                )
            self._register_open(symbol, MANUAL_TRADE_STRATEGY)
            return (
                f"Đã mở MARKET {side} {symbol}, notional tối đa "
                f"{self.order_notional:.2f} USDT (ký quỹ "
                f"{self.order_margin:.2f} USDT, đòn bẩy x{TRADE_LEVERAGE})."
            )

        limit_price = float(args[3])
        order = self.client.enter_limit_position(
            symbol, side, limit_price, self.order_notional
        )
        order_id = int(order["orderId"])
        self._managed_symbols.add(symbol)
        self._managed_order_ids[symbol] = order_id
        self._strategy_by_symbol[symbol] = MANUAL_TRADE_STRATEGY
        return (
            f"Đã gửi lệnh LIMIT {side} {symbol} tại {order.get('price', limit_price)}; "
            f"order ID {order_id}; ký quỹ {self.order_margin:.2f} USDT, "
            f"đòn bẩy x{TRADE_LEVERAGE}, vị thế tối đa "
            f"{self.order_notional:.2f} USDT."
        )

    def _telegram_close(self, symbol: str) -> str:
        position = next(
            (
                item for item in self.client.get_open_positions()
                if item.get("symbol") == symbol
            ),
            None,
        )
        if position is None:
            raise RuntimeError(
                f"Không có vị thế {symbol}; dùng /cancel để hủy lệnh chờ."
            )
        snapshot = _position_snapshot(position)
        estimated_pnl, _ = _estimate_net_pnl(
            snapshot.side,
            snapshot.entry_price,
            snapshot.quantity,
            snapshot.mark_price,
            self.fee_rate,
        )
        self.client.close_position(symbol)
        self._register_close(
            symbol,
            estimated_pnl if symbol in self._registered_open_symbols else None,
        )
        self._managed_symbols.discard(symbol)
        self._managed_order_ids.pop(symbol, None)
        self._realized_pnl += estimated_pnl
        return (
            f"Đã gửi lệnh đóng {symbol}; PnL ròng ước tính "
            f"{estimated_pnl:+.4f} USDT (chưa gồm funding)."
        )

    def _telegram_cancel(self, symbol: str, order_id: int) -> str:
        order = self.client.cancel_order(symbol, order_id)
        if self._managed_order_ids.get(symbol) == order_id:
            self._managed_order_ids.pop(symbol, None)
            self._managed_symbols.discard(symbol)
            if symbol not in self._registered_open_symbols:
                self._strategy_by_symbol.pop(symbol, None)
        return (
            f"Đã gửi yêu cầu hủy lệnh {order_id} của {symbol}; "
            f"trạng thái: {order.get('status', 'đã nhận yêu cầu')}."
        )

    def _confirm_close_all(self, user_id: int, code: str) -> str:
        confirmation = self._close_all_confirmations.pop(user_id, None)
        if (
            confirmation is None
            or confirmation[1] < time.monotonic()
            or confirmation[0] != code
        ):
            raise ValueError("Mã xác nhận sai hoặc đã hết hạn; gửi lại /close_all.")

        open_orders = self.client.get_open_orders()
        symbols_with_orders = {
            str(order["symbol"])
            for order in open_orders
            if order.get("symbol")
        }
        for symbol in symbols_with_orders:
            self.client.cancel_all_orders(symbol)

        positions = self.client.get_open_positions()
        total_estimated_pnl = 0.0
        closed = 0
        for position in positions:
            snapshot = _position_snapshot(position)
            estimated_pnl, _ = _estimate_net_pnl(
                snapshot.side,
                snapshot.entry_price,
                snapshot.quantity,
                snapshot.mark_price,
                self.fee_rate,
            )
            self.client.close_position(snapshot.symbol)
            self._register_close(
                snapshot.symbol,
                (
                    estimated_pnl
                    if snapshot.symbol in self._registered_open_symbols
                    else None
                ),
            )
            total_estimated_pnl += estimated_pnl
            self._managed_symbols.discard(snapshot.symbol)
            self._managed_order_ids.pop(snapshot.symbol, None)
            closed += 1
        self._realized_pnl += total_estimated_pnl
        self._close_all_confirmations.clear()
        return (
            f"Đã gửi yêu cầu hủy lệnh chờ của {len(symbols_with_orders)} mã và đóng "
            f"{closed} vị thế trên Testnet. PnL ròng ước tính "
            f"{total_estimated_pnl:+.4f} USDT."
        )

    def positions(self) -> List[PositionSnapshot]:
        return [
            _position_snapshot(
                item,
                self._strategy_by_symbol.get(str(item["symbol"]), "Ngoài phiên"),
                (
                    float(TRADE_LEVERAGE)
                    if str(item["symbol"]) in self._managed_symbols
                    else None
                ),
                self._timeframe_by_symbol.get(str(item["symbol"])),
            )
            for item in self.client.get_open_positions()
        ]

    def close_positions_at_profit_target(
        self, positions: List[PositionSnapshot]
    ) -> List[str]:
        capital = self._profit_target_wallet_balance
        if capital is None or not math.isfinite(capital) or capital <= 0:
            return []
        managed_positions = [
            position
            for position in positions
            if position.symbol in self._managed_symbols
        ]
        if not managed_positions:
            return []
        open_profit = sum(
            _estimate_net_pnl(
                position.side,
                position.entry_price,
                position.quantity,
                position.mark_price,
                self.fee_rate,
            )[0]
            for position in managed_positions
        )
        threshold = capital * PORTFOLIO_PROFIT_TARGET_PCT / 100
        if open_profit <= threshold:
            return []

        managed_symbols = set(self._managed_symbols)
        managed_order_symbols = set(self._managed_order_ids)
        cancelled_order_symbols: Set[str] = set()
        if self._managed_order_ids:
            open_orders = self.client.get_open_orders()
            open_order_ids = {
                (str(order["symbol"]), int(order["orderId"]))
                for order in open_orders
                if order.get("symbol") and order.get("orderId") is not None
            }
            for symbol, order_id in list(self._managed_order_ids.items()):
                if (symbol, order_id) in open_order_ids:
                    self.client.cancel_order(symbol, order_id)
                    cancelled_order_symbols.add(symbol)
                self._managed_order_ids.pop(symbol, None)

        closed_count = 0
        cancelled_count = len(cancelled_order_symbols)
        realized_total = 0.0
        active_managed_symbols: Set[str] = set()
        for exchange_position in self.client.get_open_positions():
            symbol = str(exchange_position["symbol"])
            if symbol not in managed_symbols:
                continue
            active_managed_symbols.add(symbol)
            self._register_open(
                symbol,
                self._strategy_by_symbol.get(symbol),
                self._timeframe_by_symbol.get(symbol),
            )
            snapshot = _position_snapshot(
                exchange_position,
                self._strategy_by_symbol.get(symbol, MANUAL_TRADE_STRATEGY),
                float(TRADE_LEVERAGE),
                self._timeframe_by_symbol.get(symbol),
            )
            estimated_pnl, _ = _estimate_net_pnl(
                snapshot.side,
                snapshot.entry_price,
                snapshot.quantity,
                snapshot.mark_price,
                self.fee_rate,
            )
            self.client.close_position(symbol)
            self._register_close(symbol, estimated_pnl)
            self._managed_symbols.discard(symbol)
            self._managed_order_ids.pop(symbol, None)
            self._realized_pnl += estimated_pnl
            realized_total += estimated_pnl
            closed_count += 1

        for symbol in managed_order_symbols:
            if symbol not in active_managed_symbols:
                self._managed_symbols.discard(symbol)
                self._strategy_by_symbol.pop(symbol, None)
                self._timeframe_by_symbol.pop(symbol, None)

        return [
            f"TESTNET: tổng PnL ròng vị thế đang mở {open_profit:+.4f} USDT vượt "
            f"{PORTFOLIO_PROFIT_TARGET_PCT:.2f}% vốn "
            f"({threshold:.4f} USDT); đã gửi đóng {closed_count} vị thế "
            f"do ứng dụng quản lý và hủy {cancelled_count} lệnh chờ; "
            f"PnL chốt ước tính {realized_total:+.4f} USDT."
        ]

    def update_market_prices(self, prices: Dict[str, float]) -> None:
        return None

    def _register_open(
        self,
        symbol: str,
        strategy_name: Optional[str],
        timeframe: Optional[str] = None,
    ) -> None:
        self._reentry_after_candles.pop(symbol, None)
        strategy = strategy_name or self._strategy_by_symbol.get(
            symbol, MANUAL_TRADE_STRATEGY
        )
        self._strategy_by_symbol[symbol] = strategy
        if timeframe is not None or symbol not in self._timeframe_by_symbol:
            self._timeframe_by_symbol[symbol] = timeframe
        if symbol in self._registered_open_symbols:
            return
        if self.trade_statistics is not None:
            self.trade_statistics.record_open(
                "Testnet", strategy, self._timeframe_by_symbol[symbol]
            )
        self._registered_open_symbols.add(symbol)

    def _register_close(self, symbol: str, net_pnl: Optional[float]) -> None:
        strategy = self._strategy_by_symbol.pop(symbol, None)
        timeframe = self._timeframe_by_symbol.pop(symbol, None)
        self._entry_candles.pop(symbol, None)
        self._reentry_after_candles.pop(symbol, None)
        if symbol not in self._registered_open_symbols:
            return
        if self.trade_statistics is not None and strategy is not None:
            self.trade_statistics.record_close(
                "Testnet", strategy, net_pnl, timeframe
            )
        self._registered_open_symbols.discard(symbol)

    @property
    def realized_pnl(self) -> float:
        return self._realized_pnl


class ExecutionModeFactory:
    @staticmethod
    def create(
        mode: str,
        order_margin: float,
        max_open_positions: int,
        take_profit_pct: float = 1.0,
        stop_loss_pct: float = 1.0,
        reverse_action: str = "close_only",
        trade_statistics: Optional[StrategyTradeStatistics] = None,
    ) -> ExecutionMode:
        _validate_limits(order_margin, max_open_positions)
        _validate_risk_limits(take_profit_pct, stop_loss_pct, reverse_action)
        if mode == "simulation":
            return SimulationExecutionMode(
                order_margin,
                max_open_positions,
                take_profit_pct,
                stop_loss_pct,
                reverse_action,
                trade_statistics=trade_statistics,
            )
        if mode == "testnet":
            from app.core.config import load_settings, validate_testnet_settings

            settings = load_settings()
            validate_testnet_settings(settings)
            configured_cap = settings.max_notional_usdt
            if configured_cap is None:
                raise RuntimeError("Thiếu giới hạn notional Testnet.")
            position_notional = order_margin * TRADE_LEVERAGE
            if position_notional > configured_cap:
                raise ValueError(
                    "Giá trị vị thế (ký quỹ × đòn bẩy) "
                    f"{position_notional:.2f} USDT vượt "
                    "BINANCE_TESTNET_MAX_NOTIONAL_USDT "
                    f"({configured_cap:.2f} USDT)."
                )
            return TestnetExecutionMode(
                BinanceClient(settings.api_key, settings.api_secret),
                order_margin,
                max_open_positions,
                take_profit_pct,
                stop_loss_pct,
                reverse_action,
                trade_statistics=trade_statistics,
            )
        raise ValueError(f"Chế độ giao dịch không hợp lệ: {mode}")


def _validate_limits(order_margin: float, max_open_positions: int) -> None:
    if (
        isinstance(order_margin, bool)
        or not isinstance(order_margin, (int, float))
        or not math.isfinite(order_margin)
        or order_margin <= 0
    ):
        raise ValueError("Ký quỹ mỗi lệnh phải là số hữu hạn lớn hơn 0.")
    if (
        isinstance(max_open_positions, bool)
        or not isinstance(max_open_positions, int)
        or max_open_positions < 1
    ):
        raise ValueError("Số vị thế mở tối đa phải là số nguyên từ 1 trở lên.")


def _current_candle_open_time(
    market: MarketSnapshot, timeframe: str
) -> Optional[int]:
    candles = market.candles_by_interval.get(timeframe, ())
    return candles[-1].open_time if candles else None


def _opposite_closed_candle_rejection(
    market: MarketSnapshot, signal: StrategySignal
) -> Optional[str]:
    if signal.timeframe is None or signal.side not in ("LONG", "SHORT"):
        return None
    candles = market.candles_by_interval.get(signal.timeframe, ())
    if len(candles) < 2:
        return None

    candle = candles[-2]
    candle_range = candle.high - candle.low
    body = abs(candle.close - candle.open)
    if candle_range <= 0 or body <= 0:
        return None

    if signal.side == "SHORT":
        upper_wick = candle.high - max(candle.open, candle.close)
        lower_wick = min(candle.open, candle.close) - candle.low
        is_opposite_rejection = (
            lower_wick >= 2 * upper_wick and lower_wick > body
        )
        rejection_description = (
            "râu dưới ít nhất gấp đôi râu trên và lớn hơn thân nến"
        )
    else:
        upper_wick = candle.high - max(candle.open, candle.close)
        close_position = (candle.close - candle.low) / candle_range
        is_opposite_rejection = upper_wick >= 2 * body and close_position <= 0.25
        rejection_description = (
            "râu trên ít nhất gấp đôi thân nến và giá đóng nằm trong "
            "25% biên độ phía dưới"
        )

    if not is_opposite_rejection:
        return None
    return (
        f"nến đã đóng trước đó trên khung {signal.timeframe} có "
        f"{rejection_description}."
    )


def _signal_candle_reference(
    market: MarketSnapshot, signal: StrategySignal
) -> Optional[Tuple[str, int]]:
    if signal.timeframe is None:
        return None
    open_time = _current_candle_open_time(market, signal.timeframe)
    if open_time is None:
        return None
    return signal.timeframe, open_time


def _is_same_or_earlier_candle(
    market: MarketSnapshot,
    signal: StrategySignal,
    reference: Optional[Tuple[str, int]],
) -> bool:
    if reference is None or signal.timeframe != reference[0]:
        return False
    current_open_time = _current_candle_open_time(market, reference[0])
    return current_open_time is None or current_open_time <= reference[1]


def _validate_risk_limits(
    take_profit_pct: float, stop_loss_pct: float, reverse_action: str
) -> None:
    for label, value in (
        ("chốt lời", take_profit_pct),
        ("dừng lỗ", stop_loss_pct),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
            or value > 1000
        ):
            raise ValueError(f"Phần trăm {label} phải trong khoảng (0, 1000].")
    if reverse_action not in ("close_only", "close_and_reverse"):
        raise ValueError("Hành động khi có tín hiệu ngược chiều không hợp lệ.")


def _estimate_net_pnl(
    side: str,
    entry_price: float,
    quantity: float,
    mark_price: float,
    fee_rate: float,
) -> Tuple[float, float]:
    values = (entry_price, quantity, mark_price, fee_rate)
    if any(not math.isfinite(value) for value in values):
        raise ValueError("Dữ liệu PnL phải là số hữu hạn.")
    if entry_price <= 0 or quantity <= 0 or mark_price <= 0 or fee_rate < 0:
        raise ValueError("Dữ liệu PnL phải có giá trị dương hợp lệ.")
    entry_notional = entry_price * quantity
    direction = 1.0 if side == "LONG" else -1.0
    gross_pnl = (mark_price - entry_price) * quantity * direction
    estimated_fees = (entry_notional + mark_price * quantity) * fee_rate
    net_pnl = gross_pnl - estimated_fees
    return net_pnl, net_pnl / entry_notional * 100.0


def _risk_exit_reason(
    pnl_percent: float, take_profit_pct: float, stop_loss_pct: float
) -> Optional[str]:
    if pnl_percent >= take_profit_pct:
        return "chốt lời"
    if pnl_percent <= -stop_loss_pct:
        return "dừng lỗ"
    return None


def _position_snapshot(
    position: Dict[str, Any],
    strategy_name: str = "Ngoài phiên",
    leverage: Optional[float] = None,
    timeframe: Optional[str] = None,
) -> PositionSnapshot:
    amount = float(position["positionAmt"])
    entry_price = float(position["entryPrice"])
    mark_price = float(position["markPrice"])
    if leverage is None and position.get("leverage") is not None:
        leverage = float(position["leverage"])
    return PositionSnapshot(
        symbol=str(position["symbol"]),
        side="LONG" if amount > 0 else "SHORT",
        entry_price=entry_price,
        mark_price=mark_price if mark_price > 0 else entry_price,
        quantity=abs(amount),
        strategy_name=strategy_name,
        leverage=leverage,
        timeframe=timeframe,
    )


def _occupied_symbols(
    open_positions: List[Dict[str, Any]],
    open_orders: List[Dict[str, Any]],
) -> Set[str]:
    position_symbols = {
        str(position["symbol"])
        for position in open_positions
        if position.get("symbol")
    }
    pending_symbols = {
        str(order["symbol"])
        for order in open_orders
        if order.get("symbol")
        and not order.get("reduceOnly")
        and not order.get("closePosition")
    }
    return position_symbols | pending_symbols
