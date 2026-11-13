# Manual deployment: Render backend and Expo frontend

## 1. Deploy the backend on Render

Push the backend source to your GitHub repository, excluding `.env` and virtual environments.
Create a Render **Web Service** connected to that repository.

| Setting | Value |
| --- | --- |
| Language / Runtime | Docker |
| Root Directory | `TradingappBackend` for a repository containing the IOS folder's contents; leave blank for a backend-only repository |
| Dockerfile Path | `./Dockerfile` relative to the Root Directory |
| Docker Build Context Directory | `.` relative to the Root Directory |
| Health Check Path | `/health` |

If the repository contains the entire APPs workspace, use `IOS/TradingappBackend` as the Root Directory.
Leave Docker Command unset; the Dockerfile starts `python start.py` automatically.
The API binds to `0.0.0.0` and uses Render's `PORT`, defaulting to 8000 locally.

In Render's Environment settings, enter the values from your existing **backend** `.env`:

| Variable | Value |
| --- | --- |
| `MONGODB_URI` | Your existing private MongoDB connection string |
| `DATABASE_NAME` | `TradingAPP`, or your existing configured name |
| `PURCHASES_COLLECTION` | `IOS`, or your existing configured collection |
| `CORS_ORIGINS` | Your exact Expo Hosting HTTPS origin, without a trailing slash; multiple origins separated by commas |

Optional: copy `UPSTOX_ACCESS_TOKEN` and `UPSTOX_INSTRUMENT_MAP` for portfolio quotes,
and `NVIDIA_API_KEY`, `NVIDIA_MODEL`, `NVIDIA_INVOKE_URL` for sector suggestions.
The observations and purchase-history screens do not require these optional providers.
Never put backend credentials in frontend variables or commit them to Git.
The Docker build context excludes `.env`, virtual environments, tests, and local data files.

Allow the Render service's outbound addresses in MongoDB Atlas Network Access.
Deploy, then open `https://YOUR-SERVICE.onrender.com/health` and verify `{"status":"ok"}`.
Also check `/observations` to verify database reads.

The API currently has no authentication. Anyone with network access to its public URL can read,
add, edit, and delete observation records and read/add trades. Hosting does not add sign-in.

Alternative Render Python runtime: build command `pip install -r requirements.txt`,
start command `python start.py`, health check `/health`.

## 2. Deploy the frontend on Expo Hosting

From `D:\APPs\IOS\Tradingapp`, set the **actual** deployed backend URL before exporting:

```powershell
$env:EXPO_PUBLIC_API_BASE_URL = "https://YOUR-SERVICE.onrender.com"
npm run lint
npm run typecheck
npx expo export --platform web
npx eas-cli@latest login
npx eas-cli@latest deploy
```

Skip `login` if you are already signed in. The project is already linked in `app.json`.
Use the preview URL printed by the command to test the frontend. Add that exact origin
to Render's `CORS_ORIGINS` and redeploy/restart the backend before testing in a browser.

After verifying the preview, publish to the production URL:

```powershell
npx eas-cli@latest deploy --prod
```

Add the production origin to `CORS_ORIGINS` as well. The API URL is embedded at export time;
after changing it, always export again before deploying. Never export a hosted build with a LAN IP.
The session environment override above preserves the local `.env` for LAN development.
To return to local development in the same terminal:

```powershell
Remove-Item Env:EXPO_PUBLIC_API_BASE_URL
npx expo start --clear
```

## Optional local Docker check

From `D:\APPs\IOS\TradingappBackend` with Docker installed and running:

```powershell
docker build -t tradingapp-backend .
docker run --rm --env-file .env -p 8000:8000 tradingapp-backend
```

Keep port 8000 free before running. Check `http://localhost:8000/health`.

References: [Render Docker](https://render.com/docs/docker),
[Render FastAPI](https://render.com/docs/deploy-fastapi),
[Expo Hosting](https://docs.expo.dev/eas/hosting/get-started/).
