# MLB HR Dashboard API

Railway-ready FastAPI backend for the HR dashboard.

## Deploy
1. Create a new Railway project.
2. Upload this folder or connect GitHub.
3. Railway should run: `uvicorn app:app --host 0.0.0.0 --port $PORT`.
4. Open `/health` to confirm it works.
5. Copy your Railway URL and add it to the Netlify dashboard as `VITE_API_BASE_URL`.

## Endpoints
- `/health`
- `/games`
- `/game/{gamePk}`
- `/top-hr`

## Notes
This uses public MLB StatsAPI feeds. Some advanced Statcast-style columns are modeled from live/recent contact until you plug in a richer source.
