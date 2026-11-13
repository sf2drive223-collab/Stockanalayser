"""Request models and input validation for the trading API."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

StockCategoryFilter = Literal["all", "trades", "observation", "delivery", "intraday"]


class PurchaseInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, allow_inf_nan=False)

    symbol: str = Field(min_length=1, max_length=20)
    quantity: int = Field(gt=0, le=1_000_000_000)
    buyPrice: float = Field(gt=0, le=1_000_000_000_000)
    purchaseDate: date
    category: Literal["delivery", "intraday"] = "delivery"
    transactionType: Literal["buy", "sell"] = "buy"
    sector: str = Field(default="", max_length=100)


class TradeInput(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    quantity: int = Field(gt=0, le=1_000_000_000)
    buyPrice: float = Field(gt=0, le=1_000_000_000_000)
    sellPrice: float | None = Field(default=None, ge=0, le=1_000_000_000_000)
    type: Literal["Delivery", "Intraday"] = "Delivery"


class ObservationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    stockName: str = Field(min_length=1, max_length=200)
    observationCategory: str = Field(default="", max_length=100)
    listingDate: date
    description: str = Field(min_length=1, max_length=10000)
