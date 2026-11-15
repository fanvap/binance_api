"""Market-cap providers for Binance Futures contract base assets."""

import math
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import requests


COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
COINLORE_BASE_URL = "https://api.coinlore.net/api"
MARKET_CAP_THRESHOLD_USD = 50_000_000
MARKET_CAP_CACHE_TTL_SECONDS = 1800
COINLORE_PAGE_SIZE = 100
COINLORE_MAX_RECORDS = 2000


def unique_coin_ids(
    coins: Sequence[Mapping[str, Any]],
    base_assets: Iterable[str],
) -> Dict[str, str]:
    wanted = {asset.upper() for asset in base_assets}
    ids_by_symbol: Dict[str, List[str]] = {}
    for coin in coins:
        symbol = str(coin.get("symbol", "")).upper()
        coin_id = coin.get("id")
        if symbol in wanted and coin_id:
            ids_by_symbol.setdefault(symbol, []).append(str(coin_id))
    return {
        symbol: coin_ids[0]
        for symbol, coin_ids in ids_by_symbol.items()
        if len(coin_ids) == 1
    }


class CoinGeckoMarketCapClient:
    def __init__(self) -> None:
        self.session = requests.Session()

    def fetch_market_caps(
        self,
        base_assets: Iterable[str],
        minimum_usd: float = MARKET_CAP_THRESHOLD_USD,
    ) -> Dict[str, float]:
        wanted = {asset.upper() for asset in base_assets}
        matches: Dict[str, List[Tuple[str, float]]] = {}
        page = 1
        while page <= 10:
            response = self.session.get(
                f"{COINGECKO_BASE_URL}/coins/markets",
                params={
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": 250,
                    "page": page,
                },
                timeout=20,
            )
            response.raise_for_status()
            coins = response.json()
            if not isinstance(coins, list):
                raise ValueError("CoinGecko trả về dữ liệu market cap không hợp lệ.")
            if not coins:
                break

            page_caps: List[float] = []
            for coin in coins:
                market_cap = coin.get("market_cap")
                if market_cap is None:
                    continue
                cap = float(market_cap)
                if not math.isfinite(cap):
                    continue
                page_caps.append(cap)
                symbol = str(coin.get("symbol", "")).upper()
                coin_id = coin.get("id")
                if symbol in wanted and coin_id and cap > minimum_usd:
                    matches.setdefault(symbol, []).append((str(coin_id), cap))
            if len(coins) < 250 or (
                page_caps and min(page_caps) <= minimum_usd
            ):
                break
            if page == 10:
                raise RuntimeError(
                    "CoinGecko chưa đạt ngưỡng market cap trong giới hạn phân trang."
                )
            page += 1

        return {
            symbol: coin_matches[0][1]
            for symbol, coin_matches in matches.items()
            if len(coin_matches) == 1
        }


class CoinLoreMarketCapClient:
    """Fetches ranked market-cap pages and stops below the requested threshold."""

    def __init__(self) -> None:
        self.session = requests.Session()

    def fetch_market_caps(
        self,
        base_assets: Iterable[str],
        minimum_usd: float = MARKET_CAP_THRESHOLD_USD,
    ) -> Dict[str, float]:
        wanted = {asset.upper() for asset in base_assets}
        tickers: List[Mapping[str, Any]] = []
        start = 0
        last_page_minimum: Optional[float] = None
        while start < COINLORE_MAX_RECORDS:
            response = self.session.get(
                f"{COINLORE_BASE_URL}/tickers/",
                params={"start": start, "limit": COINLORE_PAGE_SIZE},
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, Mapping) or not isinstance(
                payload.get("data"), list
            ):
                raise ValueError("CoinLore trả về dữ liệu market cap không hợp lệ.")
            page_tickers = payload["data"]
            if not page_tickers:
                break
            tickers.extend(page_tickers)
            page_caps = [
                float(item["market_cap_usd"])
                for item in page_tickers
                if item.get("market_cap_usd") not in (None, "")
                and math.isfinite(float(item["market_cap_usd"]))
            ]
            last_page_minimum = min(page_caps) if page_caps else None
            if (
                len(page_tickers) < COINLORE_PAGE_SIZE
                or (
                    last_page_minimum is not None
                    and last_page_minimum <= minimum_usd
                )
            ):
                break
            start += len(page_tickers)
            time.sleep(0.25)
        if (
            start >= COINLORE_MAX_RECORDS
            and last_page_minimum is not None
            and last_page_minimum > minimum_usd
        ):
            raise RuntimeError(
                "CoinLore chưa đạt ngưỡng market cap trong giới hạn phân trang."
            )
        return unique_ticker_market_caps(tickers, wanted, minimum_usd)


def unique_ticker_market_caps(
    tickers: Sequence[Mapping[str, Any]],
    base_assets: Iterable[str],
    minimum_usd: float = MARKET_CAP_THRESHOLD_USD,
) -> Dict[str, float]:
    wanted = {asset.upper() for asset in base_assets}
    by_symbol: Dict[str, List[Tuple[str, Optional[float]]]] = {}
    for ticker in tickers:
        symbol = str(ticker.get("symbol", "")).upper()
        ticker_id = ticker.get("id")
        market_cap = ticker.get("market_cap_usd")
        if symbol not in wanted or not ticker_id:
            continue
        cap = float(market_cap) if market_cap is not None else None
        by_symbol.setdefault(symbol, []).append((str(ticker_id), cap))
    results: Dict[str, float] = {}
    for symbol, matches in by_symbol.items():
        if len(matches) != 1:
            continue
        cap = matches[0][1]
        if cap is not None and math.isfinite(cap) and cap > minimum_usd:
            results[symbol] = cap
    return results


class MarketCapClient:
    """Uses CoinLore first and falls back to CoinGecko if the primary fails."""

    def __init__(
        self,
        primary: Optional[CoinLoreMarketCapClient] = None,
        fallback: Optional[CoinGeckoMarketCapClient] = None,
    ) -> None:
        self.primary = primary or CoinLoreMarketCapClient()
        self.fallback = fallback or CoinGeckoMarketCapClient()
        self._cache: Dict[str, Tuple[float, float, str]] = {}
        self.last_warnings: List[str] = []
        self.last_sources: List[str] = []

    def fetch_market_caps(
        self,
        base_assets: Iterable[str],
        minimum_usd: float = MARKET_CAP_THRESHOLD_USD,
    ) -> Dict[str, float]:
        self.last_warnings = []
        self.last_sources = []
        wanted = {asset.upper() for asset in base_assets}
        now = time.monotonic()
        caps: Dict[str, float] = {}
        stale: Dict[str, Tuple[float, str]] = {}
        sources = set()

        for symbol in wanted:
            cached = self._cache.get(symbol)
            if cached is None:
                continue
            cap, fetched_at, source = cached
            if now - fetched_at <= MARKET_CAP_CACHE_TTL_SECONDS:
                caps[symbol] = cap
                sources.add(source)
            else:
                stale[symbol] = (cap, source)

        missing = wanted - caps.keys()
        provider_succeeded = False
        if missing:
            try:
                fetched = self.primary.fetch_market_caps(missing, minimum_usd)
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                self.last_warnings.append(
                    f"CoinLore không khả dụng: {exc}"
                )
                try:
                    fetched = self.fallback.fetch_market_caps(
                        missing, minimum_usd
                    )
                except (requests.RequestException, RuntimeError, ValueError) as fallback_exc:
                    self.last_warnings.append(
                        f"CoinGecko không khả dụng: {fallback_exc}"
                    )
                else:
                    self._cache_provider_results(
                        missing, fetched, now, "CoinGecko", caps, sources
                    )
                    provider_succeeded = True
            else:
                self._cache_provider_results(
                    missing, fetched, now, "CoinLore", caps, sources
                )
                provider_succeeded = True

        missing = wanted - caps.keys()
        for symbol in missing:
            cached = stale.get(symbol)
            if cached is None:
                continue
            cap, source = cached
            caps[symbol] = cap
            sources.add(source)
            self.last_warnings.append(
                f"Dùng market cap đã lưu cũ cho {symbol}; nguồn {source} đang lỗi."
            )

        self.last_sources = [
            source for source in ("CoinLore", "CoinGecko") if source in sources
        ]
        if (
            not provider_succeeded
            and not any(cap > 0 for cap in caps.values())
            and self.last_warnings
        ):
            raise RuntimeError(
                "Không lấy được market cap từ CoinLore hoặc CoinGecko. "
                + " | ".join(self.last_warnings)
            )
        return {
            symbol: cap
            for symbol, cap in caps.items()
            if cap > minimum_usd
        }

    def _cache_provider_results(
        self,
        requested: Iterable[str],
        fetched: Mapping[str, float],
        fetched_at: float,
        source: str,
        caps: Dict[str, float],
        sources: Set[str],
    ) -> None:
        normalized = {
            symbol.upper(): float(cap)
            for symbol, cap in fetched.items()
            if math.isfinite(float(cap)) and float(cap) > 0
        }
        sources.add(source)
        for symbol in requested:
            cap = normalized.get(symbol, 0.0)
            self._cache[symbol] = (cap, fetched_at, source)
            if cap > 0:
                caps[symbol] = cap
