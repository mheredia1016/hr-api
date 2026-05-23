from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
from datetime import datetime

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TEAM_LOGO_SLUGS = {
    "AZ": "ari", "ATL": "atl", "BAL": "bal", "BOS": "bos", "CHC": "chc", "CWS": "chw",
    "CIN": "cin", "CLE": "cle", "COL": "col", "DET": "det", "HOU": "hou", "KC": "kc",
    "LAA": "laa", "LAD": "lad", "MIA": "mia", "MIL": "mil", "MIN": "min", "NYM": "nym",
    "NYY": "nyy", "ATH": "oak", "OAK": "oak", "PHI": "phi", "PIT": "pit", "SD": "sd",
    "SEA": "sea", "SF": "sf", "STL": "stl", "TB": "tb", "TEX": "tex", "TOR": "tor", "WSH": "wsh",
}

def team_logo(abbr):
    slug = TEAM_LOGO_SLUGS.get(abbr.upper())
    if not slug:
        return None
    return f"https://a.espncdn.com/i/teamlogos/mlb/500/{slug}.png"

@app.get("/")
def root():
    return {"status": "ok"}

@app.get("/games")
@app.get("/api/games")
def games():
    today = datetime.utcnow().strftime("%Y-%m-%d")

    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}"
    data = requests.get(url, timeout=20).json()

    rows = []

    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            away = ((game.get("teams") or {}).get("away") or {}).get("team", {})
            home = ((game.get("teams") or {}).get("home") or {}).get("team", {})

            away_abbr = away.get("abbreviation") or away.get("teamName") or "AWAY"
            home_abbr = home.get("abbreviation") or home.get("teamName") or "HOME"

            rows.append({
                "gamePk": game.get("gamePk"),
                "label": f"{away_abbr} @ {home_abbr}",
                "away": away,
                "home": home,
                "awayLogo": team_logo(away_abbr),
                "homeLogo": team_logo(home_abbr),
                "status": ((game.get("status") or {}).get("detailedState")) or "Scheduled"
            })

    return rows
