"""FastAPI backend for Tradingapp purchase records."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Literal
import os
from uuid import uuid4
from asyncio import to_thread

from dotenv import load_dotenv
from bson import ObjectId
from fastapi import FastAPI, HTTPException, Path, Query, status
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from pymongo import AsyncMongoClient
from tools import break_even_price, calculate, purchase_value, stock_details, stock_query, stock_summary
from state import ObservationInput, PurchaseInput, StockCategoryFilter, TradeInput
from portfolio import transactions, positions, valuation, total_invested
from upstox import market, MarketDataError
from sectors import enrich_sectors

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI", "")
DATABASE_NAME = os.getenv("DATABASE_NAME", "").strip()
PURCHASES_COLLECTION = os.getenv("PURCHASES_COLLECTION", "").strip()

if not MONGODB_URI:
    raise RuntimeError("Set MONGODB_URI in the backend environment.")
if not DATABASE_NAME:
    raise RuntimeError("Set DATABASE_NAME in the backend environment.")
if not PURCHASES_COLLECTION:
    raise RuntimeError("Set PURCHASES_COLLECTION in the backend environment.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = AsyncMongoClient(MONGODB_URI)
    await client.admin.command("ping")
    database = client[DATABASE_NAME]
    app.state.purchases = database[PURCHASES_COLLECTION]
    yield
    await client.close()


app = FastAPI(title="Tradingapp API", version="1.0.0", lifespan=lifespan)
cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:8081").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)

def prepare_purchase(purchase: PurchaseInput) -> dict:
    document = purchase.model_dump(mode="json")
    document["totalAmount"] = purchase_value(purchase.quantity, purchase.buyPrice)
    return document


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/calculate")
async def calculate_trade(trade: TradeInput):
    return {
        "buyValue": purchase_value(trade.quantity, trade.buyPrice),
        "result": calculate(trade.quantity, trade.buyPrice, trade.sellPrice, trade.type)
        if trade.sellPrice is not None else None,
        "breakEven": break_even_price(trade.quantity, trade.buyPrice, trade.type),
    }


@app.post("/purchases/prepare")
async def prepare_local_purchase(purchase: PurchaseInput):
    # Validate and calculate a device record without writing anything to MongoDB.
    return {"id": str(uuid4()), **prepare_purchase(purchase)}


@app.get("/purchases")
async def get_purchases(category: Literal["delivery", "intraday"] = "delivery"):
    # Records saved before categories were introduced are delivery purchases.
    query = {"category": category}
    if category == "delivery":
        query = {"$or": [{"category": "delivery"}, {"category": {"$exists": False}}]}
    cursor = app.state.purchases.find(query).sort("createdAt", -1)
    documents = await cursor.to_list(length=500)
    for document in documents:
        document["id"] = str(document.pop("_id"))
        document.pop("userId", None)
        document.pop("createdAt", None)
        document.setdefault("category", "delivery")
    return {"purchases": documents}


@app.post("/purchases", status_code=status.HTTP_201_CREATED)
async def save_purchase(purchase: PurchaseInput):
    document = prepare_purchase(purchase)
    document["createdAt"] = datetime.now(timezone.utc)
    result = await app.state.purchases.insert_one(document)
    return {"id": str(result.inserted_id)}


@app.get("/observations")
async def get_observations():
    cursor = app.state.purchases.find({"category": "observation"}).sort("createdAt", -1)
    documents = await cursor.to_list(length=None)
    for document in documents:
        document["id"] = str(document.pop("_id"))
        document.pop("createdAt", None)
    return {"observations": documents}


@app.get("/stocks")
async def get_stocks(
    category: StockCategoryFilter = "all",
    search: Annotated[str, Query(max_length=200)] = "",
):
    projection = {"_id": 1, "stockName": 1, "symbol": 1, "category": 1, "sector": 1, "observationCategory": 1,
                  "transactionType": 1, "purchaseDate": 1, "quantity": 1}
    cursor = app.state.purchases.find(stock_query(category, search), projection).sort("createdAt", -1)
    documents = await cursor.to_list(length=None)
    stocks = [summary for document in documents if (summary := stock_summary(document)) is not None]
    stocks, sector_message = await to_thread(enrich_sectors, stocks)
    return {"stocks": stocks, "sectorMessage": sector_message}


@app.get("/portfolio")
async def get_portfolio():
    # Portfolio totals always cover all saved cloud trades, independent of list filters.
    cursor = app.state.purchases.find({"$or": [
        {"category": {"$in": ["delivery", "intraday"]}}, {"category": {"$exists": False}},
    ]}, {"symbol": 1, "stockName": 1, "category": 1, "quantity": 1, "buyPrice": 1,
         "transactionType": 1, "purchaseDate": 1, "createdAt": 1}).sort("purchaseDate", 1)
    entries, issues = transactions(await cursor.to_list(length=None))
    unavailable = {"portfolioValue": None, "investedValue": None, "unrealizedPnl": None,
                   "realizedPnl": None, "totalPnl": None, "quoteAsOf": None, "totalInvested": None}
    if issues:
        return {**unavailable, "issues": issues}
    invested = total_invested(entries)
    unavailable["totalInvested"] = invested
    if not entries:
        return {**valuation({}, 0, {}), "totalInvested": invested}
    if not os.getenv("UPSTOX_ACCESS_TOKEN", "").strip():
        return {**unavailable, "issues": ["Live prices are not connected. Configure the Upstox token on the backend."]}
    try:
        instruments, issues = await to_thread(market.resolve, sorted({entry["name"] for entry in entries}))
        if issues:
            return {**unavailable, "issues": issues}
        holdings, realized = positions(entries, instruments)
        keys = sorted({key for (key, _), value in holdings.items() if value["quantity"]})
        try:
            quotes = await to_thread(market.fetch, keys) if keys else {}
        except MarketDataError as error:
            result = valuation(holdings, realized, {})
            result["issues"] = [str(error)]
            return {**result, "totalInvested": invested}
        return {**valuation(holdings, realized, quotes), "totalInvested": invested}
    except MarketDataError as error:
        return {**unavailable, "issues": [str(error)]}


@app.get("/stocks/{stock_id}")
async def get_stock(stock_id: Annotated[str, Path(min_length=1, max_length=200)]):
    # Support normal MongoDB ObjectIds and imported records with string IDs.
    query = {"_id": {"$in": [ObjectId(stock_id), stock_id]}} if ObjectId.is_valid(stock_id) else {"_id": stock_id}
    document = await app.state.purchases.find_one(query)
    if document is None or stock_summary(document) is None:
        raise HTTPException(status_code=404, detail="Stock record not found.")
    return {"stock": jsonable_encoder(stock_details(document), custom_encoder={ObjectId: str})}


@app.post("/observations", status_code=status.HTTP_201_CREATED)
async def save_observation(observation: ObservationInput):
    document = observation.model_dump(mode="json")
    document["category"] = "observation"
    document["createdAt"] = datetime.now(timezone.utc)
    result = await app.state.purchases.insert_one(document)
    return {"id": str(result.inserted_id), **observation.model_dump(mode="json"), "category": "observation"}


def observation_query(observation_id: str) -> dict:
    ids = [ObjectId(observation_id), observation_id] if ObjectId.is_valid(observation_id) else [observation_id]
    return {"_id": {"$in": ids}, "category": "observation"}


@app.put("/observations/{observation_id}")
async def update_observation(
    observation_id: Annotated[str, Path(min_length=1, max_length=200)],
    observation: ObservationInput,
):
    fields = observation.model_dump(mode="json")
    result = await app.state.purchases.update_one(observation_query(observation_id), {"$set": fields})
    if not result.matched_count:
        raise HTTPException(status_code=404, detail="Observation not found.")
    return {"id": observation_id, **fields, "category": "observation"}


@app.delete("/observations/{observation_id}")
async def delete_observation(observation_id: Annotated[str, Path(min_length=1, max_length=200)]):
    result = await app.state.purchases.delete_one(observation_query(observation_id))
    if not result.deleted_count:
        raise HTTPException(status_code=404, detail="Observation not found.")
    return {"id": observation_id, "deleted": True}
