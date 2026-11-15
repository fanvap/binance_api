"""Binance USDⓈ-M Futures market data and Testnet order client."""

import hashlib
import hmac
import math
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests

from app.backend.trading_constants import TRADE_LEVERAGE


TESTNET_BASE_URL = "https://testnet.binancefuture.com"


class BinanceClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        base_url: str = TESTNET_BASE_URL,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self._exchange_info: Optional[Dict[str, Any]] = None

    def get_price(self, symbol: str) -> float:
        result = self._request("GET", "/fapi/v1/ticker/price", {"symbol": symbol})
        return float(result["price"])

    def get_closed_candle_closes(self, symbol: str, interval: str, limit: int = 100) -> List[float]:
        rows = self._request(
            "GET",
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )
        current_time_ms = int(time.time() * 1000)
        return [float(row[4]) for row in rows if int(row[6]) < current_time_ms]

    def assert_symbol_is_flat(self, symbol: str) -> None:
        active = [
            item for item in self.get_open_positions()
            if item.get("symbol") == symbol
        ]
        if active:
            raise RuntimeError(
                f"Testnet đang có vị thế {symbol}; hãy đóng vị thế trước khi chạy bot."
            )

    def get_open_positions(self) -> List[Dict[str, Any]]:
        positions = self._signed_request("GET", "/fapi/v2/positionRisk", {})
        return [
            item for item in positions
            if Decimal(item["positionAmt"]) != 0
        ]

    def get_account_summary(self) -> Dict[str, float]:
        account = self._signed_request("GET", "/fapi/v2/account", {})
        return {
            "wallet_balance": float(account["totalWalletBalance"]),
            "available_balance": float(account["availableBalance"]),
            "unrealized_pnl": float(account["totalUnrealizedProfit"]),
        }

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"symbol": symbol} if symbol else {}
        return self._signed_request("GET", "/fapi/v1/openOrders", params)

    def cancel_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        return self._signed_request(
            "DELETE",
            "/fapi/v1/order",
            {"symbol": symbol, "orderId": order_id},
        )

    def cancel_all_orders(self, symbol: str) -> Dict[str, Any]:
        return self._signed_request(
            "DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol}
        )

    def set_leverage(self, symbol: str, leverage: int = TRADE_LEVERAGE) -> Dict[str, Any]:
        if (
            isinstance(leverage, bool)
            or not isinstance(leverage, int)
            or leverage < 1
            or leverage > TRADE_LEVERAGE
        ):
            raise ValueError(f"Đòn bẩy phải từ 1x đến tối đa {TRADE_LEVERAGE}x.")
        return self._signed_request(
            "POST",
            "/fapi/v1/leverage",
            {"symbol": symbol, "leverage": leverage},
        )

    def enter_position(self, symbol: str, side: str, price: float, max_notional: float) -> Dict[str, Any]:
        if side not in ("LONG", "SHORT"):
            raise ValueError(f"Hướng lệnh không hợp lệ: {side}")
        quantity = self._quantity_for_notional(symbol, price, max_notional)
        self.set_leverage(symbol, TRADE_LEVERAGE)
        order_side = "BUY" if side == "LONG" else "SELL"
        return self._signed_request(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": symbol,
                "side": order_side,
                "type": "MARKET",
                "quantity": format(quantity, "f"),
            },
        )

    def enter_limit_position(
        self, symbol: str, side: str, limit_price: float, max_notional: float
    ) -> Dict[str, Any]:
        if side not in ("LONG", "SHORT"):
            raise ValueError(f"Hướng lệnh không hợp lệ: {side}")
        price = self._normalize_limit_price(symbol, limit_price)
        quantity = self._quantity_for_notional(
            symbol, float(price), max_notional
        )
        self.set_leverage(symbol, TRADE_LEVERAGE)
        return self._signed_request(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": symbol,
                "side": "BUY" if side == "LONG" else "SELL",
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": format(quantity, "f"),
                "price": format(price, "f"),
            },
        )

    def close_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        positions = self._signed_request("GET", "/fapi/v2/positionRisk", {"symbol": symbol})
        for item in positions:
            if item.get("symbol") != symbol:
                continue
            amount = Decimal(item["positionAmt"])
            if amount == 0:
                return None
            return self._signed_request(
                "POST",
                "/fapi/v1/order",
                {
                    "symbol": symbol,
                    "side": "SELL" if amount > 0 else "BUY",
                    "type": "MARKET",
                    "quantity": format(abs(amount), "f"),
                    "reduceOnly": "true",
                },
            )
        return None

    def _quantity_for_notional(self, symbol: str, price: float, notional: float) -> Decimal:
        if (
            not math.isfinite(price)
            or not math.isfinite(notional)
            or price <= 0
            or notional <= 0
        ):
            raise ValueError("Giá và giới hạn notional phải hữu hạn và lớn hơn 0.")
        symbol_info = self._get_symbol_info(symbol)
        lot_filter = next(
            item for item in symbol_info["filters"] if item["filterType"] == "LOT_SIZE"
        )
        step_size = Decimal(lot_filter["stepSize"])
        raw_quantity = Decimal(str(notional)) / Decimal(str(price))
        quantity = (raw_quantity / step_size).to_integral_value(rounding=ROUND_DOWN) * step_size
        min_quantity = Decimal(lot_filter["minQty"])
        if quantity < min_quantity:
            raise ValueError(
                f"Notional {notional} USDT thấp hơn khối lượng tối thiểu của {symbol}."
            )
        notional_filter = next(
            (
                item for item in symbol_info["filters"]
                if item["filterType"] == "MIN_NOTIONAL"
            ),
            None,
        )
        if notional_filter is not None:
            min_notional = Decimal(
                str(notional_filter.get("notional", notional_filter.get("minNotional", "0")))
            )
            if quantity * Decimal(str(price)) < min_notional:
                raise ValueError(
                    f"Notional {notional} USDT thấp hơn mức tối thiểu "
                    f"{min_notional} USDT của {symbol}."
                )
        return quantity

    def _normalize_limit_price(self, symbol: str, price: float) -> Decimal:
        if not math.isfinite(price) or price <= 0:
            raise ValueError("Giá LIMIT phải là số hữu hạn lớn hơn 0.")
        symbol_info = self._get_symbol_info(symbol)
        price_filter = next(
            item for item in symbol_info["filters"]
            if item["filterType"] == "PRICE_FILTER"
        )
        tick_size = Decimal(price_filter["tickSize"])
        requested_price = Decimal(str(price))
        aligned_price = (
            (requested_price / tick_size).to_integral_value(rounding=ROUND_DOWN)
            * tick_size
        )
        if aligned_price != requested_price:
            raise ValueError(
                f"Giá LIMIT phải là bội số của tick size {tick_size} cho {symbol}."
            )
        return aligned_price

    def _get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        if self._exchange_info is None:
            self._exchange_info = self._request("GET", "/fapi/v1/exchangeInfo")
        if self._exchange_info is None:
            raise RuntimeError("Không lấy được thông tin giao dịch từ Binance Testnet.")
        symbol_info = next(
            (item for item in self._exchange_info["symbols"] if item["symbol"] == symbol),
            None,
        )
        if (
            symbol_info is None
            or symbol_info.get("status") != "TRADING"
            or symbol_info.get("contractType") != "PERPETUAL"
            or symbol_info.get("quoteAsset") != "USDT"
        ):
            raise ValueError(
                f"{symbol} không phải hợp đồng USDT perpetual đang giao dịch trên Testnet."
            )
        return symbol_info

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        try:
            response = self.session.request(
                method,
                self.base_url + path,
                params=params,
                timeout=10,
            )
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Binance Testnet request failed ({type(exc).__name__})."
            ) from None
        if response.status_code >= 400:
            raise RuntimeError(
                f"Binance Testnet request failed with HTTP {response.status_code}."
            )
        try:
            return response.json()
        except (requests.RequestException, ValueError):
            raise RuntimeError("Binance Testnet returned invalid JSON.") from None

    def _signed_request(self, method: str, path: str, params: Dict[str, Any]) -> Any:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Thiếu API key hoặc secret của Binance Futures Testnet.")
        signed_params = dict(params)
        signed_params["timestamp"] = int(time.time() * 1000)
        signed_params["recvWindow"] = 5000
        query = urlencode(signed_params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}?{query}&signature={signature}",
                headers={"X-MBX-APIKEY": self.api_key},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Binance Testnet request failed ({type(exc).__name__})."
            ) from None
        if response.status_code >= 400:
            raise RuntimeError(
                f"Binance Testnet request failed with HTTP {response.status_code}."
            )
        try:
            return response.json()
        except (requests.RequestException, ValueError):
            raise RuntimeError("Binance Testnet returned invalid JSON.") from None
