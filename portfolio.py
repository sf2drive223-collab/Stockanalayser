"""Portfolio valuation from saved transactions, using average cost before charges."""
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def amount(value):
    return float(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def transactions(documents):
    result, issues = [], []
    for document in documents:
        if document.get("category", "delivery") not in ("delivery", "intraday"):
            continue
        name = document.get("symbol") or document.get("stockName")
        try:
            if not isinstance(name, str) or not name.strip():
                raise ValueError
            quantity = Decimal(str(document["quantity"]))
            price = Decimal(str(document["buyPrice"]))
            side = document.get("transactionType", "buy")
            day = date.fromisoformat(str(document["purchaseDate"]))
            if not quantity.is_finite() or not price.is_finite() or quantity <= 0 or price <= 0 or quantity != quantity.to_integral_value() or side not in ("buy", "sell"):
                raise ValueError
            result.append({"name": name.strip(), "quantity": quantity, "price": price,
                           "side": side, "category": document.get("category", "delivery"),
                           "order": (day.isoformat(), str(document.get("createdAt", "")), str(document.get("_id", "")))})
        except (KeyError, ValueError, InvalidOperation):
            issues.append(f"Invalid transaction for {name or 'unnamed stock'}. Check its date, quantity and price.")
    return sorted(result, key=lambda item: item["order"]), issues


def positions(entries, instruments):
    holdings = {}
    realized = Decimal(0)
    for entry in entries:
        key = (instruments[entry["name"]], entry["category"])
        holding = holdings.setdefault(key, {"quantity": Decimal(0), "cost": Decimal(0)})
        quantity, cost = holding["quantity"], holding["cost"]
        delta = entry["quantity"] * (1 if entry["side"] == "buy" else -1)
        price = entry["price"]
        if quantity == 0 or (quantity > 0) == (delta > 0):
            holding.update(quantity=quantity + delta, cost=cost + delta * price)
            continue
        closed = min(abs(quantity), abs(delta))
        average = cost / quantity
        realized += closed * (price - average) * (1 if quantity > 0 else -1)
        remaining = quantity + delta
        holding.update(quantity=remaining, cost=remaining * (average if (remaining > 0) == (quantity > 0) else price))
    return holdings, realized


def total_invested(entries):
    """Cumulative buy amount before sales and charges; no quotes required."""
    return amount(sum((entry["quantity"] * entry["price"] for entry in entries
                       if entry["side"] == "buy"), Decimal(0)))


def valuation(holdings, realized, quotes):
    cost = sum((holding["cost"] for holding in holdings.values()), Decimal(0))
    market_value = Decimal(0)
    timestamps, issues = [], []
    for (key, _), holding in holdings.items():
        if not holding["quantity"]:
            continue
        quote = quotes.get(key, {})
        try:
            price = Decimal(str(quote["last_price"]))
            if not price.is_finite() or price <= 0 or not quote.get("timestamp"):
                raise ValueError
            market_value += holding["quantity"] * price
            timestamps.append(quote["timestamp"])
        except (KeyError, ValueError, InvalidOperation):
            issues.append(f"No valid current quote for {key}.")
    unrealized = market_value - cost
    return {"portfolioValue": None if issues else amount(market_value), "investedValue": amount(cost),
            "unrealizedPnl": None if issues else amount(unrealized), "realizedPnl": amount(realized),
            "totalPnl": None if issues else amount(realized + unrealized),
            "quoteAsOf": min(timestamps) if timestamps and not issues else None, "issues": issues}
