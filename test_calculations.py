"""Compare the Python migration with captured JavaScript results."""

import json
import math
import os
from pathlib import Path
import unittest

from tools import break_even_price, calculate
from pydantic import ValidationError

os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
os.environ.setdefault("DATABASE_NAME", "test")
os.environ.setdefault("PURCHASES_COLLECTION", "records")
import main


class FormulaTests(unittest.TestCase):
    def test_matches_original_javascript(self):
        cases = json.loads(Path(__file__).with_name("calculation_reference.json").read_text())
        for case in cases:
            quantity, buy, sell, trading_type = case["args"]
            with self.subTest(args=case["args"]):
                actual = calculate(quantity, buy, sell, trading_type)
                for key, expected in case["result"].items():
                    if key == "charges":
                        for charge, amount in expected.items():
                            self.assertTrue(math.isclose(actual[key][charge], amount, rel_tol=1e-12, abs_tol=1e-12))
                    else:
                        self.assertTrue(math.isclose(actual[key], expected, rel_tol=1e-12, abs_tol=1e-12))
                self.assertEqual(break_even_price(quantity, buy, trading_type), case["breakEven"])

    def test_calculation_validation(self):
        valid = dict(quantity=100, buyPrice=100, sellPrice=110, type="Delivery")
        invalid = [dict(quantity=0), dict(quantity=1.5), dict(buyPrice=-1),
                   dict(buyPrice=float("nan")), dict(sellPrice=float("inf")),
                   dict(sellPrice=-0.01), dict(type="Other")]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                main.TradeInput(**{**valid, **changes})


class EndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_calculate_endpoint(self):
        trade = main.TradeInput(quantity=100, buyPrice=100, sellPrice=110, type="Intraday")
        response = await main.calculate_trade(trade)
        self.assertEqual(response["buyValue"], 10000)
        self.assertEqual(response["result"], calculate(100, 100, 110, "Intraday"))
        self.assertEqual(response["breakEven"], break_even_price(100, 100, "Intraday"))

    async def test_purchase_preview_without_sell_price(self):
        response = await main.calculate_trade(main.TradeInput(quantity=2, buyPrice=125.5))
        self.assertEqual(response["buyValue"], 251)
        self.assertIsNone(response["result"])

    async def test_prepare_local_purchase_does_not_trust_client_total(self):
        purchase = main.PurchaseInput(symbol=" tataMotors ", quantity=2, buyPrice=125.5,
                                      purchaseDate="2026-11-07", totalAmount=999999)
        response = await main.prepare_local_purchase(purchase)
        self.assertEqual(response["totalAmount"], 251)
        self.assertEqual(response["symbol"], "tataMotors")
        self.assertEqual(response["purchaseDate"], "2026-11-07")
        self.assertEqual(response["category"], "delivery")
        self.assertTrue(response["id"])

    async def test_blank_symbols_rejected(self):
        with self.assertRaises(ValidationError):
            main.PurchaseInput(symbol="   ", quantity=2, buyPrice=100, purchaseDate="2026-11-07")


if __name__ == "__main__":
    unittest.main()
