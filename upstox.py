"""Read-only Upstox equity instrument resolution and quote snapshots."""
import gzip
import json
import os
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class MarketDataError(Exception):
    pass


def normalized(value):
    return re.sub(r"[^a-z0-9]", "", value.casefold())


class UpstoxQuotes:
    def __init__(self):
        self.lock = threading.Lock()
        self.instruments = {}
        self.quotes = {}

    def _json(self, url, token=None, compressed=False):
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            with urlopen(Request(url, headers=headers), timeout=15) as response:
                raw = response.read()
            return json.loads(gzip.decompress(raw) if compressed else raw)
        except HTTPError as error:
            error.close()
            if error.code in (401, 403):
                raise MarketDataError("Upstox authorization failed. Update the backend access token.") from None
            if error.code == 429:
                raise MarketDataError("Upstox rate limit reached. Retry shortly.") from None
            raise MarketDataError("Upstox market data is unavailable. Retry shortly.") from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise MarketDataError("Could not load Upstox market data. Retry shortly.") from None

    def _catalog(self, exchange):
        cached = self.instruments.get(exchange)
        if cached and time.monotonic() - cached[0] < 86400:
            return cached[1]
        items = self._json(f"https://assets.upstox.com/market-quote/instruments/exchange/{exchange}.json.gz", compressed=True)
        index = {}
        for item in items:
            if item.get("segment") != f"{exchange}_EQ" or item.get("instrument_type") != "EQ":
                continue
            key = item["instrument_key"]
            for alias in (item.get("trading_symbol"), item.get("name"), item.get("short_name")):
                if alias:
                    index.setdefault(normalized(alias), set()).add(key)
        self.instruments[exchange] = (time.monotonic(), index)
        return index

    def resolve(self, names):
        """Exact matches only; NSE default, BSE: prefix or explicit mapping."""
        try:
            overrides = json.loads(os.getenv("UPSTOX_INSTRUMENT_MAP", "{}"))
            if not isinstance(overrides, dict) or not all(isinstance(v, str) for v in overrides.values()):
                raise ValueError
            overrides = {normalized(k): v for k, v in overrides.items()}
        except (ValueError, AttributeError):
            raise MarketDataError("UPSTOX_INSTRUMENT_MAP must be a JSON object of stock names to instrument keys.") from None
        resolved, issues = {}, []
        with self.lock:
            for name in names:
                if normalized(name) in overrides:
                    resolved[name] = overrides[normalized(name)]
                    continue
                exchange, symbol = (name.split(":", 1) if name.upper().startswith(("NSE:", "BSE:")) else ("NSE", name))
                matches = self._catalog(exchange.upper()).get(normalized(symbol), set())
                if len(matches) == 1:
                    resolved[name] = next(iter(matches))
                else:
                    issues.append(f"Cannot uniquely match {name} to an Upstox equity. Configure its instrument mapping.")
        return resolved, issues

    def fetch(self, keys):
        token = os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()
        if not token:
            raise MarketDataError("Live prices are not connected. Configure the Upstox token on the backend.")
        with self.lock:
            missing = [key for key in keys if key not in self.quotes or time.monotonic() - self.quotes[key][0] >= 20]
            for offset in range(0, len(missing), 500):
                batch = missing[offset:offset + 500]
                payload = self._json("https://api.upstox.com/v3/market-quote/quotes?" + urlencode({"instrument_key": ",".join(batch)}), token)
                if payload.get("status") != "success" or not isinstance(payload.get("data"), dict):
                    raise MarketDataError("Upstox returned an invalid quote response.")
                for quote in payload["data"].values():
                    key = quote.get("instrument_token")
                    if key in batch:
                        self.quotes[key] = (time.monotonic(), quote)
            # Never substitute old quotes when a requested quote is missing.
            return {key: self.quotes[key][1] for key in keys if key in self.quotes and time.monotonic() - self.quotes[key][0] < 20}


market = UpstoxQuotes()
