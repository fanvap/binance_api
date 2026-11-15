"""Public Binance Futures market data client."""

import time
from dataclasses import dataclass
from typing import Any, Dict, List

import requests


BINANCE_FUTURES_BASE_URL = "https://fapi.binance.com"


@dataclass(frozen=True)
class Candle:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    close_time: int = 0


class BinanceMarketDataClient:
    def __init__(self) -> None:
        self.session = requests.Session()

    def get_usdt_perpetual_symbols(self) -> List[Dict[str, str]]:
        response = self.session.get(
            f"{BINANCE_FUTURES_BASE_URL}/fapi/v1/exchangeInfo",
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        return [
            {"symbol": item["symbol"], "base_asset": item["baseAsset"]}
            for item in payload["symbols"]
            if item.get("status") == "TRADING"
            and item.get("contractType") == "PERPETUAL"
            and item.get("quoteAsset") == "USDT"
        ]

    def get_all_prices(self) -> Dict[str, float]:
        response = self.session.get(
            f"{BINANCE_FUTURES_BASE_URL}/fapi/v1/ticker/price",
            timeout=15,
        )
        response.raise_for_status()
        payload: List[Dict[str, Any]] = response.json()
        return {item["symbol"]: float(item["price"]) for item in payload}

    def get_candles(self, symbol: str, interval: str, limit: int = 100) -> List[Candle]:
        response = self.session.get(
            f"{BINANCE_FUTURES_BASE_URL}/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=15,
        )
        response.raise_for_status()
        return [
            Candle(
                open_time=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[7]),
                close_time=int(row[6]),
            )
            for row in response.json()
        ]

    def get_closed_candles(
        self, symbol: str, interval: str, limit: int = 100
    ) -> List[Candle]:
        current_time_ms = int(time.time() * 1000)
        return [
            candle
            for candle in self.get_candles(symbol, interval, limit)
            if candle.close_time < current_time_ms
        ]
