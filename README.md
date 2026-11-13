# Tradingapp Python backend

The Expo frontend remains in `../Tradingapp`. This FastAPI service stores your personal purchases and observations in the database and collection configured by `DATABASE_NAME` and `PURCHASES_COLLECTION` in `.env`.

For manual Render and Expo Hosting deployment, see [DEPLOYMENT.md](DEPLOYMENT.md).
The included Dockerfile and `start.py` use the host's `PORT` and exclude local secrets.

## Run locally on Windows

1. Create a virtual environment and install dependencies:

   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

2. Set `MONGODB_URI` to your Atlas connection string in `.env`. Set `DATABASE_NAME=TradingAPP` and `PURCHASES_COLLECTION=IOS` (or your preferred names). No app account or JWT secret is required.

3. Start the API:

   ```powershell
   uvicorn main:app --reload --host 0.0.0.0 --port 8000
   ```

Interactive API documentation is available at `http://localhost:8000/docs` while the server is running.

For a physical phone, configure the frontend with the computer's reachable LAN address (for example `http://192.168.x.x:8000`). Use an HTTPS URL when hosting the API publicly. Set `CORS_ORIGINS` to the web frontend's origins.

Selecting MongoDB Cloud loads purchase history directly through the API, without registration or sign-in. MongoDB credentials stay on the server. The purchase API has no authentication; anyone who can reach it can read and add purchases, so keep it accessible only to you.

## Screens and record categories

- Analysis: available in the screen dropdown. Lists all saved MongoDB observations in a table with Stock name and Category (the chosen stock sector). Tap a name to read the original description/analysis, sector, and listing date; use Back to all observations to return to the table. Older observations without a sector show Uncategorized. The list reloads on entry and supports Refresh. `GET /observations` returns all observations without the old 500-record limit.

- Add observations: `POST /observations` accepts `stockName`, optional `observationCategory` (up to 100 characters), `listingDate` (`YYYY-MM-DD`), and `description`. The Category field is a stock-sector dropdown above the listing date, with choices such as Banking, IT, Energy, and Other. It starts unselected and clears after saving. The server always assigns the record type `category: "observation"`; the selected sector is stored separately as `observationCategory`. Previously saved custom categories remain readable. `GET /observations` returns only observations.
- Profit and loss screen: existing Delivery calculator and purchase storage. New purchases have `category: "delivery"`.
- Intraday History: existing Intraday calculator and purchase storage. New purchases have `category: "intraday"`.
- Get stocks: shows MongoDB **Buy/Sell order history only**, across Delivery and Intraday, with stock name and sector. Each row also shows its side, transaction date, and share quantity. The screen requests `GET /stocks?category=trades&search=`; the backend includes Delivery, Intraday, and legacy uncategorized purchases and excludes observations before sector detection. Observations remain on Add observations and Analysis. The sector dropdown filters the returned orders locally. Saved sectors take priority over optional NVIDIA `sectorSuggestion` values, which are labelled AI suggested; unknown sectors show Unclassified. Each order remains a separate row, and portfolio totals remain independent of table filters. Other API clients can still explicitly request `category=all` or `category=observation`.
- Purchase history: available from the screen dropdown, with Delivery/Intraday and device/cloud choices. A Sector dropdown below Stock Name uses the same choices as Add observations. The selected sector is saved with Buy/Sell orders to either storage option, appears in history and Get stocks, and takes priority over AI suggestions. It starts blank and clears on entry and after successful saves. The API accepts optional `sector` text up to 100 characters, preserving compatibility with older orders.

Click a stock name in Get stocks to open the selected record's details. `GET /stocks/{id}` returns the saved observation or purchase fields, including notes, listing date, quantities, prices, dates, and any additional saved fields. A missing record returns 404. Returning to the stock table restores its category and name filters.

`GET /purchases?category=delivery` and `GET /purchases?category=intraday` keep the two purchase lists separate. Old purchases without a category are treated as Delivery; existing documents are not modified. Observations do not appear in purchase history.

Restart the backend after changes. In the frontend run `npx expo start --clear` after installing the navigation dependencies.

## Python business logic

### NVIDIA sector suggestions

Configure `NVIDIA_API_KEY`, `NVIDIA_MODEL`, and `NVIDIA_INVOKE_URL` in the backend `.env`, then restart the backend. The model and endpoint are read from the environment; no model/endpoint defaults are hardcoded in Python. `.env.example` contains the selected model and NVIDIA HTTPS endpoint. Missing or invalid settings show a configuration message without contacting the provider. Never configure the API key in the Expo frontend.

Get stocks requests suggestions for records without a saved sector from `https://integrate.api.nvidia.com/v1/chat/completions`. Only company names/symbols are sent; observation notes, transaction quantities/prices, and credentials are not included in the prompt. Requests are non-streaming with thinking disabled and temperature zero. Responses are validated as complete JSON against the same sector list used in Add observations. Ambiguous names should return Unclassified. These suggestions use the model's knowledge; no current company-data lookup or verification is performed.

Manual sectors take priority. Suggestions are returned as `sectorSuggestion` and labelled **AI suggested** in the table; they do not overwrite MongoDB records or observation sectors. Treat them as suggestions and verify important classifications. Exact duplicate names reuse manual sectors in the returned list. Model results (including abstentions) are cached in memory for 24 hours, limited to 2,000 names, and reset when the backend restarts. A request classifies at most 20 unique uncached names; Refresh processes the next batch. Provider errors, malformed output, and missing/dummy keys leave stocks Unclassified with a message while preserving the list and portfolio overview.

### Live portfolio summary

Get stocks shows an overview of all saved **MongoDB** Delivery/Intraday transactions, independent of table filters. Observations and device-only records are excluded. `GET /portfolio` uses Upstox V3 full quote snapshots and automatically refreshes on the screen every 30 seconds. Quote timestamps are shown; outside market hours these are the latest available quotes, not new trades.

The **Total invested** metric (`totalInvested`) is the cumulative quantity × price of all saved Buy entries, before sales and charges. Legacy entries without a transaction type count as buys. This metric remains available without an Upstox token, live quotes, or stock-name matching; it differs from the remaining holdings' cost (`investedValue`). Invalid transaction data leaves it unavailable rather than displaying an incomplete total.

Set `UPSTOX_ACCESS_TOKEN` in the backend `.env` to an Upstox read-only Analytics Token (or a valid access token), then restart the backend. Never put this token in the Expo `.env`. Generate it at Upstox Developer Apps → Analytics: https://upstox.com/developer/api-documentation/analytics-token/ . Only instrument downloads and market quote GET requests are made; no orders are placed.

Names match the Upstox equity catalog by exact symbol, company name, or short name, ignoring case, spaces, and punctuation. NSE is the default; `BSE:SYMBOL` explicitly selects BSE. Ambiguous or unknown names are reported instead of guessed. For custom names, add a JSON mapping to the backend `.env`, e.g. `UPSTOX_INSTRUMENT_MAP='{"myStock":"NSE_EQ|<verified-ISIN>"}'`, using verified keys from https://upstox.com/developer/api-documentation/instruments/ . Aliases mapped to the same instrument are combined.

Portfolio value is remaining signed quantity × last traded price. Open short positions have negative market value. Unrealized P/L is market value minus remaining average cost; realized P/L uses moving weighted average cost, with Delivery and Intraday accounted for separately. Transactions are processed by transaction date, then creation time and record ID. Legacy records without a side are buys. These figures are before brokerage/taxes and do not adjust for corporate actions. Missing prices, unrecognized names, and invalid transactions leave affected totals unavailable and show an explanation; they are never silently valued at zero. Instrument catalogs are cached for 24 hours and quote snapshots for 20 seconds.

`tools.py` owns the existing trading formulas: brokerage, tax/charge breakdown, gross and net profit/loss, break-even price, and purchase value. The migration preserves the previous formulas; it does not update rates.

- `POST /calculate` accepts `quantity`, `buyPrice`, `sellPrice`, and `type` (`Delivery` or `Intraday`). It returns `buyValue`, `result`, and `breakEven`. Omit `sellPrice` to preview purchase value without a P&L result.
- `POST /purchases/prepare` validates a device purchase and returns its normalized fields, generated ID, and server-calculated `totalAmount`. It does not write to MongoDB.
- `POST /purchases` also calculates `totalAmount` on the server. A client-supplied total is ignored.

The frontend handles forms, display formatting, requests, and device persistence. It requests calculations after a brief pause in typing, discards outdated responses, and shows loading/error/retry states. The Python backend must be reachable for calculations and new saves to either storage option. Previously saved device history can still be read locally.

Run backend checks with `python -m unittest discover -v`. The tests use mocks and do not connect to MongoDB. `calculation_reference.json` contains reference results captured from the JavaScript formulas before migration.
