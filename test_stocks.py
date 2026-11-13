"""Tests for MongoDB stock listing and filters; no database connection needed."""
import os
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from bson import ObjectId
from fastapi import HTTPException

os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
os.environ.setdefault("DATABASE_NAME", "test")
os.environ.setdefault("PURCHASES_COLLECTION", "records")

import main
from tools import stock_query, stock_summary


class StockFilterTests(unittest.TestCase):
    def test_category_filters(self):
        self.assertEqual(stock_query("all"), {})
        self.assertEqual(stock_query("trades"), {"$or": [
            {"category": {"$in": ["delivery", "intraday"]}}, {"category": {"$exists": False}},
        ]})
        self.assertEqual(stock_query("observation"), {"category": "observation"})
        self.assertEqual(stock_query("intraday"), {"category": "intraday"})
        self.assertEqual(stock_query("delivery"), {"$or": [
            {"category": "delivery"}, {"category": {"$exists": False}},
        ]})

    def test_search_is_literal_case_insensitive_and_combines_with_category(self):
        query = stock_query("observation", "  A+B  ")
        self.assertEqual(query["$and"][0], {"category": "observation"})
        self.assertEqual(query["$and"][1], {"$or": [
            {"stockName": {"$regex": r"A\+B", "$options": "i"}},
            {"symbol": {"$regex": r"A\+B", "$options": "i"}},
        ]})

    def test_names_symbols_and_legacy_category(self):
        self.assertEqual(stock_summary({"_id": 1, "symbol": " TCS "}),
                         {"id": "1", "stockName": "TCS", "category": "delivery"})
        self.assertEqual(stock_summary({"_id": 2, "stockName": "Tata", "category": "observation"}),
                         {"id": "2", "stockName": "Tata", "category": "observation"})
        self.assertIsNone(stock_summary({"_id": 3}))
        self.assertIsNone(stock_summary({"_id": 4, "stockName": "   "}))

    def test_sector_is_separate_from_trading_category(self):
        result = stock_summary({"_id": 1, "symbol": "TCS", "category": "delivery", "sector": " IT "})
        self.assertEqual(result["sector"], "IT")
        self.assertEqual(result["category"], "delivery")
        result = stock_summary({"_id": 2, "stockName": "Example", "category": "observation", "observationCategory": "Pharmaceuticals"})
        self.assertEqual(result["sector"], "Pharmaceuticals")
        self.assertNotIn("sector", stock_summary({"_id": 3, "symbol": "Unknown", "category": "intraday"}))


class StockEndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Stock listing tests never invoke a real model, even with a local API key.
        self.sector_patch = patch("main.enrich_sectors", side_effect=lambda stocks: (stocks, ""))
        self.sector_patch.start()
        self.previous = getattr(main.app.state, "purchases", None)
        self.collection = MagicMock()
        self.collection.find_one = AsyncMock(return_value=None)
        self.cursor = self.collection.find.return_value.sort.return_value
        self.cursor.to_list = AsyncMock(return_value=[])
        main.app.state.purchases = self.collection

    def tearDown(self):
        self.sector_patch.stop()
        main.app.state.purchases = self.previous

    async def test_lists_all_records_without_500_record_cutoff(self):
        self.cursor.to_list.return_value = [{"_id": n, "symbol": f"STOCK{n}"} for n in range(501)]
        response = await main.get_stocks()
        self.assertEqual(len(response["stocks"]), 501)
        self.cursor.to_list.assert_awaited_once_with(length=None)
        self.collection.find.assert_called_once_with({}, {"_id": 1, "stockName": 1, "symbol": 1, "category": 1, "sector": 1, "observationCategory": 1,
                                                        "transactionType": 1, "purchaseDate": 1, "quantity": 1})

    async def test_filters_are_forwarded_and_only_summary_fields_returned(self):
        self.cursor.to_list.return_value = [{"_id": "note", "stockName": "Example", "category": "observation", "description": "Private notes"}]
        response = await main.get_stocks("observation", "Example")
        self.assertEqual(self.collection.find.call_args.args[0], stock_query("observation", "Example"))
        self.assertEqual(response["stocks"], [{"id": "note", "stockName": "Example", "category": "observation"}])

    async def test_trade_history_query_excludes_observations_and_retains_buy_sell_details(self):
        self.cursor.to_list.return_value = [
            {"_id": "buy", "symbol": "TCS", "category": "delivery", "transactionType": "buy", "quantity": 10, "purchaseDate": "2026-11-01"},
            {"_id": "sell", "symbol": "TCS", "category": "intraday", "transactionType": "sell", "quantity": 2, "purchaseDate": "2026-11-02"},
            {"_id": "legacy", "symbol": "Old Stock"},
        ]
        result = await main.get_stocks("trades", "TCS")
        self.assertEqual(self.collection.find.call_args.args[0], {"$and": [
            {"$or": [{"category": {"$in": ["delivery", "intraday"]}}, {"category": {"$exists": False}}]},
            {"$or": [{"stockName": {"$regex": "TCS", "$options": "i"}}, {"symbol": {"$regex": "TCS", "$options": "i"}}]},
        ]})
        self.assertEqual(result["stocks"][0]["transactionType"], "buy")
        self.assertEqual(result["stocks"][1]["transactionType"], "sell")
        self.assertEqual(result["stocks"][1]["quantity"], 2)
        self.assertEqual(result["stocks"][1]["purchaseDate"], "2026-11-02")
        self.assertEqual(result["stocks"][2]["category"], "delivery")

    async def test_observation_details_include_notes_and_listing_date(self):
        # Opening a record must continue to retain the user's notes.
        stock_id = ObjectId()
        self.collection.find_one.return_value = {
            "_id": stock_id, "stockName": "TCS", "category": "observation",
            "listingDate": "2004-08-25", "description": "My research",
            "createdAt": datetime(2026, 11, 7, 10, 0), "userId": "old-account",
        }
        response = await main.get_stock(str(stock_id))
        stock = response["stock"]
        self.collection.find_one.assert_awaited_once_with({"_id": {"$in": [stock_id, str(stock_id)]}})
        self.assertEqual(stock["id"], str(stock_id))
        self.assertEqual(stock["listingDate"], "2004-08-25")
        self.assertEqual(stock["description"], "My research")
        self.assertTrue(stock["createdAt"].endswith("+00:00"))
        self.assertNotIn("_id", stock)
        self.assertNotIn("userId", stock)

    async def test_list_includes_saved_sector_for_purchases_and_observations(self):
        self.cursor.to_list.return_value = [
            {"_id": "purchase", "symbol": "Example", "category": "delivery", "sector": "Pharmaceuticals"},
            {"_id": "note", "stockName": "TCS", "category": "observation", "observationCategory": "IT"},
        ]
        response = await main.get_stocks()
        self.assertEqual([stock["sector"] for stock in response["stocks"]], ["Pharmaceuticals", "IT"])

    async def test_purchase_details_include_amounts_and_legacy_category(self):
        self.collection.find_one.return_value = {"_id": "legacy-record", "symbol": "TCS", "quantity": 2,
                                                 "buyPrice": 100, "totalAmount": 200, "purchaseDate": "2026-11-07"}
        stock = (await main.get_stock("legacy-record"))["stock"]
        self.collection.find_one.assert_awaited_once_with({"_id": "legacy-record"})
        self.assertEqual(stock["stockName"], "TCS")
        self.assertEqual(stock["category"], "delivery")
        self.assertEqual(stock["totalAmount"], 200)
        self.assertEqual(stock["quantity"], 2)

    async def test_missing_record_returns_404(self):
        with self.assertRaises(HTTPException) as caught:
            await main.get_stock(str(ObjectId()))
        self.assertEqual(caught.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
