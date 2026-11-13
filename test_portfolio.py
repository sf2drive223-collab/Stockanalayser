"""Portfolio accounting and Upstox contracts, with no network or database access."""
import io
import json
import os
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.error import HTTPError

os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
os.environ.setdefault("DATABASE_NAME", "test")
os.environ.setdefault("PURCHASES_COLLECTION", "records")

import main
from portfolio import transactions, positions, valuation, total_invested
from upstox import UpstoxQuotes, MarketDataError


def trade(quantity, price, side="buy", day="2026-11-01", category="delivery", name="TCS"):
    return dict(symbol=name, quantity=quantity, buyPrice=price, transactionType=side,
                purchaseDate=day, category=category)


class AccountingTests(unittest.TestCase):
    def test_total_invested_counts_only_buys_and_ignores_sales(self):
        legacy = trade(3, 50)
        legacy.pop("transactionType")
        entries, issues = transactions([trade(10, 100), trade(4, 150, "sell"),
                                       trade(2, 200, category="intraday"), legacy,
                                       {"category": "observation", "stockName": "Notes"}])
        self.assertEqual(issues, [])
        self.assertEqual(total_invested(entries), 1550)
        self.assertEqual(total_invested([]), 0)

    def summarize(self, documents, price=150):
        entries, issues = transactions(documents)
        self.assertEqual(issues, [])
        holdings, realized = positions(entries, {entry["name"]: "NSE_EQ|TCS" for entry in entries})
        return valuation(holdings, realized, {"NSE_EQ|TCS": {"last_price": price, "timestamp": "2026-11-09T12:00:00+05:30"}})

    def test_multiple_buys_partial_sale_and_live_valuation(self):
        summary = self.summarize([trade(5, 160, "sell", "2026-11-03"),
                                  trade(10, 100), trade(10, 120, day="2026-11-02")])
        self.assertEqual(summary["portfolioValue"], 2250)
        self.assertEqual(summary["investedValue"], 1650)
        self.assertEqual(summary["unrealizedPnl"], 600)
        self.assertEqual(summary["realizedPnl"], 250)
        self.assertEqual(summary["totalPnl"], 850)

    def test_closed_position_needs_no_live_quote(self):
        entries, _ = transactions([trade(2, 100), trade(2, 80, "sell", "2026-11-02")])
        holdings, realized = positions(entries, {"TCS": "key"})
        result = valuation(holdings, realized, {})
        self.assertEqual(result["portfolioValue"], 0)
        self.assertEqual(result["unrealizedPnl"], 0)
        self.assertEqual(result["realizedPnl"], -40)

    def test_short_position_and_cover(self):
        summary = self.summarize([trade(10, 200, "sell"), trade(4, 180, day="2026-11-02")], price=170)
        self.assertEqual(summary["portfolioValue"], -1020)
        self.assertEqual(summary["unrealizedPnl"], 180)
        self.assertEqual(summary["realizedPnl"], 80)

    def test_delivery_and_intraday_do_not_offset_each_other(self):
        summary = self.summarize([trade(2, 100), trade(2, 120, "sell", category="intraday")])
        self.assertEqual(summary["realizedPnl"], 0)
        self.assertEqual(summary["unrealizedPnl"], 40)

    def test_legacy_buys_and_observations(self):
        document = trade(2, 100)
        document.pop("transactionType")
        document.pop("category")
        summary = self.summarize([document, {"stockName": "Notes", "category": "observation"}])
        self.assertEqual(summary["portfolioValue"], 300)

    def test_bad_data_is_reported_and_missing_prices_never_become_zero(self):
        for price in ("nan", "inf", -1, "bad"):
            self.assertTrue(transactions([trade(1, price)])[1])
        holdings = {("key", "delivery"): {"quantity": Decimal(1), "cost": Decimal(100)}}
        for quotes in ({}, {"key": {"last_price": 0, "timestamp": "today"}}):
            result = valuation(holdings, Decimal(0), quotes)
            self.assertIsNone(result["portfolioValue"])
            self.assertIsNone(result["unrealizedPnl"])
            self.assertTrue(result["issues"])


class ProviderTests(unittest.TestCase):
    @patch.dict(os.environ, {"UPSTOX_INSTRUMENT_MAP": "{}"})
    def test_exact_case_insensitive_resolution_and_ambiguity(self):
        provider = UpstoxQuotes()
        with patch.object(provider, "_catalog", return_value={"tatamotors": {"key"}, "same": {"a", "b"}}):
            names, issues = provider.resolve(["tataMotors", "Same", "Tata"])
        self.assertEqual(names, {"tataMotors": "key"})
        self.assertEqual(len(issues), 2)

    @patch.dict(os.environ, {"UPSTOX_ACCESS_TOKEN": "test-token"})
    def test_v3_response_uses_instrument_token_and_caches_quotes(self):
        provider = UpstoxQuotes()
        quote = {"instrument_token": "key", "last_price": 150, "timestamp": "2026-11-09T12:00:00+05:30"}
        with patch.object(provider, "_json", return_value={"status": "success", "data": {"NSE_EQ:TCS": quote}}) as request:
            self.assertEqual(provider.fetch(["key"]), {"key": quote})
            self.assertEqual(provider.fetch(["key"]), {"key": quote})
            request.assert_called_once()
            self.assertIn("/v3/market-quote/quotes?instrument_key=key", request.call_args.args[0])

    def test_authorization_header_and_safe_auth_error(self):
        provider = UpstoxQuotes()
        with patch("upstox.urlopen", return_value=io.BytesIO(json.dumps({"status": "success"}).encode())) as request:
            provider._json("https://api.upstox.com/test", "secret-test-token")
            self.assertEqual(request.call_args.args[0].get_header("Authorization"), "Bearer secret-test-token")
        with patch("upstox.urlopen", side_effect=HTTPError("url", 401, "bad", {}, None)):
            with self.assertRaisesRegex(MarketDataError, "authorization failed"):
                provider._json("https://api.upstox.com/test", "secret-test-token")


class EndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous = getattr(main.app.state, "purchases", None)
        self.collection = MagicMock()
        self.cursor = self.collection.find.return_value.sort.return_value
        self.cursor.to_list = AsyncMock(return_value=[])
        main.app.state.purchases = self.collection

    async def asyncTearDown(self):
        main.app.state.purchases = self.previous

    async def test_empty_portfolio(self):
        response = await main.get_portfolio()
        self.assertEqual(response["portfolioValue"], 0)
        self.assertEqual(response["totalInvested"], 0)
        self.cursor.to_list.assert_awaited_once_with(length=None)

    @patch.dict(os.environ, {"UPSTOX_ACCESS_TOKEN": "test-token"})
    async def test_live_value_and_unavailable_provider(self):
        self.cursor.to_list.return_value = [trade(2, 100)]
        with patch.object(main.market, "resolve", return_value=({"TCS": "key"}, [])), patch.object(main.market, "fetch", return_value={"key": {"last_price": 130, "timestamp": "2026-11-09T12:00:00+05:30"}}):
            response = await main.get_portfolio()
        self.assertEqual(response["portfolioValue"], 260)
        self.assertEqual(response["totalInvested"], 200)
        self.assertEqual(response["unrealizedPnl"], 60)
        with patch.object(main.market, "resolve", return_value=({"TCS": "key"}, [])), patch.object(main.market, "fetch", side_effect=MarketDataError("Unavailable")):
            response = await main.get_portfolio()
        self.assertIsNone(response["portfolioValue"])
        self.assertEqual(response["totalInvested"], 200)
        self.assertEqual(response["issues"], ["Unavailable"])

    @patch.dict(os.environ, {"UPSTOX_ACCESS_TOKEN": ""})
    async def test_missing_token_reports_setup_without_calling_provider(self):
        self.cursor.to_list.return_value = [trade(2, 100)]
        with patch.object(main.market, "resolve") as resolve:
            response = await main.get_portfolio()
        resolve.assert_not_called()
        self.assertIsNone(response["portfolioValue"])
        self.assertIn("token", response["issues"][0])
        self.assertEqual(response["totalInvested"], 200)


if __name__ == "__main__":
    unittest.main()
