"""Background market-cap filtering and multi-symbol price polling."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Empty
from typing import Dict, List, Optional, Sequence, Set, Tuple, TypedDict

from PyQt6.QtCore import QThread, pyqtSignal

from app.backend.market_caps import MarketCapClient, MARKET_CAP_THRESHOLD_USD
from app.backend.market_data import BinanceMarketDataClient
from app.backend.execution import (
    MAX_ORDER_MARGIN,
    SIMULATION_MAX_OPEN_POSITIONS,
    SIMULATION_MAX_ORDER_MARGIN,
    ExecutionMode,
    ExecutionModeFactory,
)
from app.backend.market_data import Candle
from app.backend.strategy import (
    CANDLE_INTERVAL_SECONDS,
    SUPPORTED_CANDLE_INTERVALS,
    MarketSnapshot,
    Strategy,
    StrategyFactory,
    StrategySignal,
)
from app.backend.trade_statistics import (
    StrategyTradeStatistics,
)
from app.backend.trading_constants import TRADE_LEVERAGE
from app.backend.telegram import (
    TelegramClient,
    TelegramWorker,
    parse_command,
)


class MarketRow(TypedDict):
    symbol: str
    base_asset: str
    market_cap: float
    price: float
    strategy: str
    signal: str


class MarketScannerWorker(QThread):
    snapshot_received = pyqtSignal(object)
    log_received = pyqtSignal(str, str)
    error_received = pyqtSignal(str)
    finished_with_status = pyqtSignal(str)
    positions_received = pyqtSignal(object)
    realized_pnl_received = pyqtSignal(float)
    account_summary_received = pyqtSignal(object)
    strategy_statistics_received = pyqtSignal(object)
    source_received = pyqtSignal(str)
    telegram_status_received = pyqtSignal(str)

    def __init__(
        self,
        mode: str = "simulation",
        order_margin: Optional[float] = None,
        max_open_positions: Optional[int] = None,
        take_profit_pct: float = 1.0,
        stop_loss_pct: float = 1.0,
        market_caps_client: Optional[MarketCapClient] = None,
        parent=None,
        strategy_name: str = "short_p",
        candle_intervals: Optional[Sequence[str]] = None,
        trade_statistics: Optional[StrategyTradeStatistics] = None,
    ) -> None:
        super().__init__(parent)
        self._stop_event = threading.Event()
        self._market_data = BinanceMarketDataClient()
        self._market_caps = market_caps_client or MarketCapClient()
        self._strategy: Strategy = StrategyFactory.create(
            strategy_name,
            candle_intervals
            if candle_intervals is not None
            else SUPPORTED_CANDLE_INTERVALS,
        )
        self._trade_statistics = trade_statistics or StrategyTradeStatistics()
        default_order_margin = (
            SIMULATION_MAX_ORDER_MARGIN if mode == "simulation" else MAX_ORDER_MARGIN
        )
        default_max_positions = (
            SIMULATION_MAX_OPEN_POSITIONS if mode == "simulation" else 3
        )
        self._execution_mode: ExecutionMode = ExecutionModeFactory.create(
            mode,
            order_margin if order_margin is not None else default_order_margin,
            (
                max_open_positions
                if max_open_positions is not None
                else default_max_positions
            ),
            take_profit_pct,
            stop_loss_pct,
            reverse_action="close_only",
            trade_statistics=self._trade_statistics,
        )
        self._telegram_worker: Optional[TelegramWorker] = None
        self._last_telegram_pnl_at = 0.0
        self._last_account_summary_at = 0.0
        self._eligible_symbols: Set[str] = set()
        self._mode = mode
        telegram_client = TelegramClient.from_environment()
        if telegram_client is not None:
            self._telegram_worker = TelegramWorker(telegram_client)

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        try:
            if self._telegram_worker is not None:
                self._telegram_worker.log_received.connect(self.log_received.emit)
                self._telegram_worker.status_received.connect(
                    self.telegram_status_received.emit
                )
                self._telegram_worker.start()
            else:
                self.telegram_status_received.emit(
                    "Telegram: chưa cấu hình"
                )
            self._send_account_summary()
            self.log_received.emit("INFO", "Đang tải hợp đồng Binance Futures USDT...")
            contracts = self._market_data.get_usdt_perpetual_symbols()
            base_assets = {contract["base_asset"] for contract in contracts}

            self.log_received.emit(
                "INFO",
                "Giá và hợp đồng ưu tiên Binance; market cap lấy CoinLore trước, "
                "CoinGecko dự phòng. Ticker trùng bị loại.",
            )
            market_caps = self._market_caps.fetch_market_caps(base_assets)
            for warning in self._market_caps.last_warnings:
                self.log_received.emit("WARN", warning)
            sources = ", ".join(self._market_caps.last_sources) or "chưa có dữ liệu"
            self.source_received.emit(
                f"Giá Binance Futures  •  Market cap {sources}"
            )
            eligible = [
                contract
                for contract in contracts
                if market_caps.get(contract["base_asset"], 0) > MARKET_CAP_THRESHOLD_USD
            ]
            eligible.sort(
                key=lambda item: market_caps[item["base_asset"]],
                reverse=True,
            )
            self._eligible_symbols = {
                contract["symbol"] for contract in eligible
            }
            if not eligible:
                raise RuntimeError(
                    "Không tìm thấy hợp đồng có market cap trên 100 triệu USD; "
                    "kiểm tra kết nối hoặc các nguồn market-cap."
                )

            self.log_received.emit(
                "SUCCESS",
                f"Đã lọc {len(eligible)} hợp đồng trên tổng {len(contracts)} hợp đồng.",
            )
            self.log_received.emit(
                "INFO",
                f"Strategy: {self._strategy.name}; khung nến "
                f"{', '.join(self._strategy.candle_intervals) or 'không dùng'}; chế độ: "
                f"{self._execution_mode.__class__.__name__}.",
            )

            candle_cache: Dict[str, Dict[str, List[Candle]]] = {}
            last_candle_bucket: Dict[str, int] = {}
            while not self._stop_event.is_set():
                current_time_ms = int(time.time() * 1000)
                current_time = current_time_ms // 1000
                due_intervals = [
                    interval
                    for interval in self._strategy.candle_intervals
                    if (
                        last_candle_bucket.get(interval)
                        != current_time // CANDLE_INTERVAL_SECONDS[interval]
                        or any(
                            not self._has_current_candle(
                                candle_cache,
                                contract["symbol"],
                                interval,
                                current_time_ms,
                            )
                            for contract in eligible
                        )
                    )
                ]
                if due_intervals:
                    refreshed_buckets = {
                        interval: current_time // CANDLE_INTERVAL_SECONDS[interval]
                        for interval in due_intervals
                    }
                    self._refresh_candles(
                        eligible, candle_cache, due_intervals
                    )
                    last_candle_bucket.update(refreshed_buckets)
                prices = self._market_data.get_all_prices()
                current_time_ms = int(time.time() * 1000)
                self._update_live_candles(
                    candle_cache, prices, current_time_ms
                )
                rows: List[MarketRow] = []
                for contract in eligible:
                    symbol = contract["symbol"]
                    price = prices.get(symbol)
                    if price is None:
                        continue
                    base_asset = contract["base_asset"]
                    market_cap = market_caps[base_asset]
                    snapshot = MarketSnapshot(
                        symbol,
                        price,
                        market_cap,
                        {
                            interval: self._current_candle_pair(
                                candle_cache,
                                symbol,
                                interval,
                                current_time_ms,
                            )
                            for interval in self._strategy.candle_intervals
                        },
                    )
                    signal = self._strategy.evaluate(snapshot)
                    for message in self._execution_mode.update(snapshot, signal):
                        self.log_received.emit("SUCCESS", message)
                        if self._telegram_worker is not None:
                            self._broadcast_execution_event(message, signal)
                    rows.append(
                        MarketRow(
                            symbol=symbol,
                            base_asset=base_asset,
                            market_cap=market_cap,
                            price=price,
                            strategy=(
                                f"{self._strategy.name} · {signal.timeframe}"
                                if signal.timeframe
                                else self._strategy.name
                            ),
                            signal=signal.side or "CHỜ",
                        )
                    )
                self.snapshot_received.emit(rows)
                self.positions_received.emit(self._execution_mode.positions())
                self.realized_pnl_received.emit(self._execution_mode.realized_pnl)
                self._process_telegram_commands()
                self.strategy_statistics_received.emit(
                    self._trade_statistics.snapshot()
                )
                self._send_account_summary()
                self._send_periodic_telegram_pnl()
                self.log_received.emit(
                    "UPDATE",
                    f"Đã cập nhật giá {len(rows)}/{len(eligible)} hợp đồng.",
                )
                self._stop_event.wait(5)

            self.finished_with_status.emit("Đã dừng theo dõi thị trường.")
        except Exception as exc:
            self.error_received.emit(str(exc))
            self.log_received.emit("ERROR", str(exc))
            self.finished_with_status.emit("Quét thị trường dừng do lỗi.")
        finally:
            if self._telegram_worker is not None:
                self._telegram_worker.stop()
                self._telegram_worker.wait(10000)

    def _broadcast_execution_event(
        self, message: str, signal: StrategySignal
    ) -> None:
        if self._telegram_worker is None:
            return
        if signal.side is not None:
            marker = "🟢" if signal.side == "LONG" else "🔴"
            message = f"{marker} {signal.side}\n{message}"
        if signal.timeframe is not None:
            message = (
                f"{message}\nChiến thuật: {self._strategy.name}; "
                f"khung nến: {signal.timeframe}."
            )
        self._telegram_worker.broadcast(message)

    def _process_telegram_commands(self) -> None:
        if self._telegram_worker is None:
            return
        while True:
            try:
                command = self._telegram_worker.commands.get_nowait()
            except Empty:
                return
            try:
                name, args = parse_command(command)
                if name == "open" and args[0] not in self._eligible_symbols:
                    raise ValueError(
                        "Chỉ mở mã thuộc danh sách đã quét (market cap > 100 triệu USD)."
                    )
                response = self._execution_mode.execute_telegram_command(
                    name, args, command.user_id
                )
                self.log_received.emit(
                    "SUCCESS", f"Telegram /{name}: {response}"
                )
                notify_all = name in ("open", "close", "cancel", "confirm")
            except (ValueError, RuntimeError) as exc:
                response = f"Lỗi: {exc}"
                notify_all = False
                self.log_received.emit(
                    "WARN", f"Telegram /{command.text.lstrip('/').split()[0]}: {exc}"
                )
            if notify_all:
                marker = ""
                if name == "open" and len(args) > 1:
                    marker = "🟢" if args[1] == "LONG" else "🔴"
                    marker = f"{marker} {args[1]}\n"
                self._telegram_worker.broadcast(
                    f"{marker}Telegram /{name}: {response}"
                )
            else:
                self._telegram_worker.send_to(command.user_id, response)

    def _send_periodic_telegram_pnl(self) -> None:
        if self._telegram_worker is None:
            return
        now = time.monotonic()
        if now - self._last_telegram_pnl_at < 300:
            return
        positions = self._execution_mode.positions()
        if not positions:
            return
        total_pnl = sum(position.unrealized_pnl for position in positions)
        mode_label = (
            "Binance Futures Testnet" if self._mode == "testnet" else "Simulation"
        )
        self._telegram_worker.broadcast(
            f"{mode_label} PnL chưa chốt: {total_pnl:+.4f} USDT "
            f"trên {len(positions)} vị thế."
        )
        self._last_telegram_pnl_at = now

    def _send_account_summary(self) -> None:
        now = time.monotonic()
        if self._mode == "testnet" and now - self._last_account_summary_at < 15:
            return
        self._last_account_summary_at = now
        try:
            summary = self._execution_mode.account_summary()
        except (RuntimeError, ValueError) as exc:
            self.log_received.emit(
                "WARN", f"Không cập nhật được số dư tài khoản: {exc}"
            )
            return
        self.account_summary_received.emit(summary)

    def _refresh_candles(
        self,
        contracts: List[Dict[str, str]],
        candle_cache: Dict[str, Dict[str, List[Candle]]],
        intervals: Sequence[str],
    ) -> None:
        if not contracts or not intervals:
            return
        tasks = len(contracts) * len(intervals)
        with ThreadPoolExecutor(max_workers=min(8, tasks)) as executor:
            requests = {
                executor.submit(
                    self._market_data.get_candles,
                    contract["symbol"],
                    interval,
                    3,
                ): (contract["symbol"], interval)
                for contract in contracts
                for interval in intervals
            }
            for future in as_completed(requests):
                symbol, interval = requests[future]
                try:
                    candles = future.result()
                except Exception as exc:
                    self.log_received.emit(
                        "WARN",
                        f"Không tải được nến {symbol} khung {interval}: {exc}",
                    )
                    continue
                if candles:
                    candle_cache.setdefault(symbol, {})[interval] = candles

    @staticmethod
    def _has_current_candle(
        candle_cache: Dict[str, Dict[str, List[Candle]]],
        symbol: str,
        interval: str,
        current_time_ms: int,
    ) -> bool:
        candles = candle_cache.get(symbol, {}).get(interval, [])
        if not candles:
            return False
        interval_ms = CANDLE_INTERVAL_SECONDS[interval] * 1000
        current_open_time = current_time_ms // interval_ms * interval_ms
        return candles[-1].open_time == current_open_time

    def _current_candle_pair(
        self,
        candle_cache: Dict[str, Dict[str, List[Candle]]],
        symbol: str,
        interval: str,
        current_time_ms: int,
    ) -> Tuple[Candle, ...]:
        candles = candle_cache.get(symbol, {}).get(interval, [])
        if len(candles) < 2 or not self._has_current_candle(
            candle_cache, symbol, interval, current_time_ms
        ):
            return ()
        return tuple(candles[-2:])

    def _update_live_candles(
        self,
        candle_cache: Dict[str, Dict[str, List[Candle]]],
        prices: Dict[str, float],
        current_time_ms: int,
    ) -> None:
        for symbol, candles_by_interval in candle_cache.items():
            price = prices.get(symbol)
            if price is None:
                continue
            for interval in self._strategy.candle_intervals:
                candles = candles_by_interval.get(interval)
                if candles is None or len(candles) < 2:
                    continue
                if not self._has_current_candle(
                    candle_cache, symbol, interval, current_time_ms
                ):
                    continue
                current = candles[-1]
                candles[-1] = Candle(
                    open_time=current.open_time,
                    open=current.open,
                    high=max(current.high, price),
                    low=min(current.low, price),
                    close=price,
                    volume=current.volume,
                    close_time=current.close_time,
                )
