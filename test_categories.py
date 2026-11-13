"""Check category isolation without connecting to MongoDB."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
os.environ.setdefault("DATABASE_NAME", "test")
os.environ.setdefault("PURCHASES_COLLECTION", "records")

from pydantic import ValidationError
import main


class CategoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.previous = getattr(main.app.state, "purchases", None)
        self.collection = MagicMock()
        self.collection.insert_one = AsyncMock(return_value=SimpleNamespace(inserted_id="saved"))
        self.cursor = self.collection.find.return_value.sort.return_value
        self.cursor.to_list = AsyncMock(return_value=[])
        main.app.state.purchases = self.collection

    def tearDown(self):
        main.app.state.purchases = self.previous

    async def test_delivery_includes_legacy_and_excludes_observations(self):
        self.cursor.to_list.return_value = [{"_id": "old", "symbol": "LEGACY"}]
        result = await main.get_purchases("delivery")
        self.collection.find.assert_called_once_with({"$or": [
            {"category": "delivery"}, {"category": {"$exists": False}},
        ]})
        self.assertEqual(result["purchases"][0]["category"], "delivery")
        self.assertEqual(result["purchases"][0]["id"], "old")

    async def test_intraday_filter(self):
        await main.get_purchases("intraday")
        self.collection.find.assert_called_once_with({"category": "intraday"})

    async def test_observation_saved_with_server_assigned_category(self):
        data = main.ObservationInput(stockName=" Stock ", listingDate="2020-02-29",
                                     description=" Research notes ", category="intraday")
        saved = await main.save_observation(data)
        document = self.collection.insert_one.call_args.args[0]
        self.assertEqual(document["category"], "observation")
        self.assertEqual(document["stockName"], "Stock")
        self.assertEqual(document["listingDate"], "2020-02-29")
        self.assertEqual(saved["id"], "saved")
        self.assertEqual(saved["description"], "Research notes")

    async def test_custom_observation_category_saved_without_changing_record_type(self):
        data = main.ObservationInput(stockName="TCS", observationCategory=" IT ",
                                     listingDate="2004-08-25", description="Research")
        saved = await main.save_observation(data)
        document = self.collection.insert_one.call_args.args[0]
        self.assertEqual(document["observationCategory"], "IT")
        self.assertEqual(saved["observationCategory"], "IT")
        self.assertEqual(document["category"], "observation")
        self.assertEqual(main.ObservationInput(stockName="TCS", listingDate="2004-08-25",
                                              description="Research").observationCategory, "")
        with self.assertRaises(ValidationError):
            main.ObservationInput(stockName="TCS", observationCategory="x" * 101,
                                  listingDate="2004-08-25", description="Research")

    async def test_observation_list_filter_and_serialization(self):
        self.cursor.to_list.return_value = [{"_id": "note", "category": "observation", "createdAt": "date"}]
        result = await main.get_observations()
        self.collection.find.assert_called_once_with({"category": "observation"})
        self.assertEqual(result["observations"], [{"id": "note", "category": "observation"}])

    async def test_all_observations_include_sector_and_written_analysis(self):
        self.cursor.to_list.return_value = [
            {"_id": str(index), "stockName": "TCS", "category": "observation",
             "observationCategory": "IT", "description": "My notes\nSecond paragraph",
             "listingDate": "2004-08-25"} for index in range(501)
        ]
        result = await main.get_observations()
        self.cursor.to_list.assert_awaited_once_with(length=None)
        self.assertEqual(len(result["observations"]), 501)
        self.assertEqual(result["observations"][0]["observationCategory"], "IT")
        self.assertEqual(result["observations"][0]["description"], "My notes\nSecond paragraph")

    async def test_invalid_observations(self):
        valid = dict(stockName="Stock", listingDate="2020-02-29", description="Notes")
        for changes in [dict(stockName="   "), dict(description="  "), dict(listingDate="2023-02-29")]:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                main.ObservationInput(**{**valid, **changes})

    async def test_transaction_types_saved_for_cloud_and_device(self):
        data = dict(symbol="tataMotors", quantity=2, buyPrice=100,
                    purchaseDate="2026-11-07", category="delivery")
        for transaction_type in ("buy", "sell"):
            with self.subTest(transaction_type=transaction_type):
                purchase = main.PurchaseInput(**data, transactionType=transaction_type, sector=" Automotive ")
                await main.save_purchase(purchase)
                document = self.collection.insert_one.call_args.args[0]
                self.assertEqual(document["transactionType"], transaction_type)
                self.assertEqual(document["sector"], "Automotive")
                self.assertEqual(document["totalAmount"], 200)
                local = await main.prepare_local_purchase(purchase)
                self.assertEqual(local["transactionType"], transaction_type)
                self.assertEqual(local["sector"], "Automotive")
                self.assertEqual(local["totalAmount"], 200)
        self.assertEqual(main.PurchaseInput(**data).transactionType, "buy")
        self.assertEqual(main.PurchaseInput(**data).sector, "")
        with self.assertRaises(ValidationError):
            main.PurchaseInput(**data, sector="x" * 101)
        with self.assertRaises(ValidationError):
            main.PurchaseInput(**data, transactionType="other")

    async def test_purchase_category(self):
        data = dict(symbol=" tataMotors ", quantity=2, buyPrice=100, totalAmount=999999,
                    purchaseDate="2026-11-07", category="intraday")
        await main.save_purchase(main.PurchaseInput(**data))
        document = self.collection.insert_one.call_args.args[0]
        self.assertEqual(document["category"], "intraday")
        self.assertEqual(document["symbol"], "tataMotors")
        self.assertEqual(document["totalAmount"], 200)
        with self.assertRaises(ValidationError):
            main.PurchaseInput(**{**data, "category": "observation"})


if __name__ == "__main__":
    unittest.main()
