"""Trading calculations, preserving the formulas previously used by the app."""

import math
import re
from datetime import datetime, timezone


def stock_query(category: str = "all", search: str = "") -> dict:
    conditions = []
    if category == "trades":
        conditions.append({"$or": [
            {"category": {"$in": ["delivery", "intraday"]}}, {"category": {"$exists": False}},
        ]})
    elif category == "delivery":
        conditions.append({"$or": [{"category": "delivery"}, {"category": {"$exists": False}}]})
    elif category != "all":
        conditions.append({"category": category})
    if search.strip():
        pattern = {"$regex": re.escape(search.strip()), "$options": "i"}
        conditions.append({"$or": [{"stockName": pattern}, {"symbol": pattern}]})
    if not conditions:
        return {}
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


def stock_summary(document: dict) -> dict | None:
    name = document.get("stockName") or document.get("symbol")
    if not isinstance(name, str) or not name.strip():
        return None
    summary = {
        "id": str(document["_id"]),
        "stockName": name.strip(),
        "category": document.get("category", "delivery"),
    }
    sector = document.get("sector") or document.get("observationCategory")
    if isinstance(sector, str) and sector.strip():
        summary["sector"] = sector.strip()
    for field in ("transactionType", "purchaseDate", "quantity"):
        if field in document:
            summary[field] = document[field]
    return summary


def stock_details(document: dict) -> dict:
    details = dict(document)
    details["id"] = str(details.pop("_id"))
    details.setdefault("category", "delivery")
    details["stockName"] = (details.get("stockName") or details.get("symbol") or "").strip()
    details.pop("userId", None)
    saved_on = details.get("createdAt")
    if isinstance(saved_on, datetime) and saved_on.tzinfo is None:
        details["createdAt"] = saved_on.replace(tzinfo=timezone.utc)
    return details


def purchase_value(quantity: int, buy_price: float) -> float:
    return quantity * buy_price


def brokerage(turnover: float) -> float:
    if turnover <= 0:
        return 0
    if turnover < 2000:
        return min(5, turnover * 0.025)
    return min(20, turnover * 0.001)


def calculate(quantity: int, buy_price: float, sell_price: float, trading_type: str) -> dict:
    buy_value = purchase_value(quantity, buy_price)
    sell_value = quantity * sell_price
    turnover = buy_value + sell_value
    gross = sell_value - buy_value
    total_brokerage = brokerage(buy_value) + brokerage(sell_value)
    exchange = turnover * 0.0000297
    sebi = turnover * 0.000001
    ipft = turnover * 0.000001
    intraday = trading_type == "Intraday"
    stt = sell_value * 0.00025 if intraday else turnover * 0.001
    stamp_duty = buy_value * (0.00003 if intraday else 0.00015)
    dp = 20 if trading_type == "Delivery" and sell_value >= 100 else 0
    gst = (total_brokerage + exchange + sebi + ipft + dp) * 0.18
    charges = {
        "Brokerage": total_brokerage,
        "STT": stt,
        "Exchange charges": exchange,
        "SEBI fees": sebi,
        "IPFT fees": ipft,
        "Stamp duty": stamp_duty,
        "DP charges": dp,
        "GST": gst,
    }
    total_charges = sum(charges.values())
    return {
        "buyValue": buy_value,
        "sellValue": sell_value,
        "gross": gross,
        "charges": charges,
        "totalCharges": total_charges,
        "net": gross - total_charges,
    }


def break_even_price(quantity: int, buy_price: float, trading_type: str) -> float:
    low = 0
    high = max(buy_price * 2 + 100, 100)
    for _ in range(60):
        mid = (low + high) / 2
        if calculate(quantity, buy_price, mid, trading_type)["net"] >= 0:
            high = mid
        else:
            low = mid
    return math.ceil(high * 100) / 100
