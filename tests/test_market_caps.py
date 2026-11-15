import unittest

import requests

from app.backend.market_caps import (
    CoinGeckoMarketCapClient,
    CoinLoreMarketCapClient,
    MarketCapClient,
    unique_coin_ids,
    unique_ticker_market_caps,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url, params, timeout))
        return FakeResponse(self.responses.pop(0))


class FakeMarketCapProvider:
    def __init__(self, result=None, error=None):
        self.result = result or {}
        self.error = error
        self.calls = []

    def fetch_market_caps(self, base_assets, minimum_usd):
        wanted = set(base_assets)
        self.calls.append(wanted)
        if self.error is not None:
            raise self.error
        return {
            symbol: cap
            for symbol, cap in self.result.items()
            if symbol in wanted
        }


class MarketCapMappingTests(unittest.TestCase):
    def test_ambiguous_tickers_are_excluded(self):
        coins = [
            {"id": "bitcoin", "symbol": "btc"},
            {"id": "wrapped-bitcoin", "symbol": "btc"},
            {"id": "ethereum", "symbol": "eth"},
        ]

        result = unique_coin_ids(coins, {"BTC", "ETH", "SOL"})

        self.assertEqual(result, {"ETH": "ethereum"})

    def test_ambiguous_ticker_market_caps_are_excluded(self):
        tickers = [
            {"id": "btc-bitcoin", "symbol": "BTC", "market_cap_usd": "1000000000"},
            {"id": "btc-wrapped", "symbol": "BTC", "market_cap_usd": "200000000"},
            {"id": "eth-ethereum", "symbol": "ETH", "market_cap_usd": "500000000"},
            {"id": "small-coin", "symbol": "SMALL", "market_cap_usd": "10"},
        ]

        result = unique_ticker_market_caps(tickers, {"BTC", "ETH", "SMALL"})

        self.assertEqual(result, {"ETH": 500_000_000})

    def test_coingecko_is_fallback_after_primary_rate_limit(self):
        primary = FakeMarketCapProvider(
            error=requests.HTTPError("429 Client Error: Too Many Requests")
        )
        fallback = FakeMarketCapProvider({"BTC": 1_000_000_000})
        client = MarketCapClient(primary, fallback)

        result = client.fetch_market_caps({"BTC"})

        self.assertEqual(result, {"BTC": 1_000_000_000})
        self.assertTrue(fallback.calls)
        self.assertIn("429", client.last_warnings[0])
        self.assertEqual(client.last_sources, ["CoinGecko"])

    def test_market_cap_results_are_cached_between_scans(self):
        primary = FakeMarketCapProvider({"BTC": 1_000_000_000})
        fallback = FakeMarketCapProvider()
        client = MarketCapClient(primary, fallback)

        self.assertEqual(client.fetch_market_caps({"BTC"}), {"BTC": 1_000_000_000})
        self.assertEqual(client.fetch_market_caps({"BTC"}), {"BTC": 1_000_000_000})

        self.assertEqual(len(primary.calls), 1)
        self.assertEqual(fallback.calls, [])

    def test_coingecko_uses_ranked_markets_without_coins_list(self):
        client = CoinGeckoMarketCapClient()
        client.session = FakeSession(
            [
                [
                    {"id": "bitcoin", "symbol": "btc", "market_cap": 1_000_000_000},
                    {"id": "ethereum", "symbol": "eth", "market_cap": 500_000_000},
                ]
            ]
        )

        result = client.fetch_market_caps({"BTC"})

        self.assertEqual(result, {"BTC": 1_000_000_000})
        self.assertIn("/coins/markets", client.session.calls[0][0])
        self.assertNotIn("/coins/list", client.session.calls[0][0])

    def test_coinlore_pages_until_market_cap_threshold(self):
        first_page = [
            {
                "id": str(index),
                "symbol": "BTC" if index == 0 else f"T{index}",
                "market_cap_usd": "200000000",
            }
            for index in range(100)
        ]
        second_page = [
            {
                "id": str(index + 100),
                "symbol": f"S{index}",
                "market_cap_usd": "50000000",
            }
            for index in range(100)
        ]
        client = CoinLoreMarketCapClient()
        client.session = FakeSession(
            [{"data": first_page}, {"data": second_page}]
        )

        result = client.fetch_market_caps({"BTC"})

        self.assertEqual(result, {"BTC": 200_000_000.0})
        self.assertEqual(
            [call[1]["start"] for call in client.session.calls], [0, 100]
        )


if __name__ == "__main__":
    unittest.main()
