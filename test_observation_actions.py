"""Observation edits and deletion must never target saved trade records."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
os.environ.setdefault("DATABASE_NAME", "test")
os.environ.setdefault("PURCHASES_COLLECTION", "records")

from bson import ObjectId
from fastapi import HTTPException
import main


class ObservationActionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_edit_preserves_category_and_metadata(self):
        collection = MagicMock()
        collection.update_one = AsyncMock(return_value=SimpleNamespace(matched_count=1))
        data = main.ObservationInput(stockName=" TCS ", observationCategory=" IT ",
                                     listingDate="2004-08-25", description=" Updated notes ", category="delivery")
        with patch.object(main.app.state, "purchases", collection, create=True):
            result = await main.update_observation("imported-id", data)
        query, update = collection.update_one.call_args.args
        self.assertEqual(query, {"_id": {"$in": ["imported-id"]}, "category": "observation"})
        self.assertEqual(update, {"$set": {"stockName": "TCS", "observationCategory": "IT",
                                         "listingDate": "2004-08-25", "description": "Updated notes"}})
        self.assertEqual(result["category"], "observation")
        self.assertEqual(result["id"], "imported-id")

    async def test_delete_supports_object_and_string_ids_and_only_observations(self):
        collection = MagicMock()
        collection.delete_one = AsyncMock(return_value=SimpleNamespace(deleted_count=1))
        record_id = "507f1f77bcf86cd799439011"
        with patch.object(main.app.state, "purchases", collection, create=True):
            result = await main.delete_observation(record_id)
        collection.delete_one.assert_awaited_once_with({
            "_id": {"$in": [ObjectId(record_id), record_id]}, "category": "observation",
        })
        self.assertEqual(result, {"id": record_id, "deleted": True})

    async def test_missing_or_non_observation_records_return_404(self):
        collection = MagicMock()
        collection.update_one = AsyncMock(return_value=SimpleNamespace(matched_count=0))
        collection.delete_one = AsyncMock(return_value=SimpleNamespace(deleted_count=0))
        data = main.ObservationInput(stockName="TCS", listingDate="2004-08-25", description="Notes")
        with patch.object(main.app.state, "purchases", collection, create=True):
            for operation in (lambda: main.update_observation("trade-id", data),
                              lambda: main.delete_observation("trade-id")):
                with self.assertRaises(HTTPException) as raised:
                    await operation()
                self.assertEqual(raised.exception.status_code, 404)

    async def test_unchanged_edit_is_successful(self):
        collection = MagicMock()
        collection.update_one = AsyncMock(return_value=SimpleNamespace(matched_count=1, modified_count=0))
        data = main.ObservationInput(stockName="TCS", listingDate="2004-08-25", description="Notes")
        with patch.object(main.app.state, "purchases", collection, create=True):
            result = await main.update_observation("note-id", data)
        self.assertEqual(result["stockName"], "TCS")
