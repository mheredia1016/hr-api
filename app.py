from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

app = FastAPI(title="HR Matchup API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TZ = ZoneInfo("America/Chicago")

TEAM_ABBR_BY_ID = {
    108: "LAA", 109: "AZ", 110: "BAL", 111: "BOS", 112: "CHC",
    113: "CIN", 114: "CLE", 115: "COL", 116: "DET", 117: "HOU",
    118: "KC", 119: "LAD", 120: "WSH", 121: "NYM", 133: "ATH",
    134: "PIT", 135: "SD", 136: "SEA", 137: "SF", 138: "STL",
    139: "TB", 140: "TEX", 141: "TOR", 142: "MIN", 143: "PHI",
    144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL",
}

TEAM_LOGO_SLUGS = {
    "AZ": "ari", "ATL": "atl", "BAL": "bal", "BOS": "bos", "CHC": "chc", "CWS": "chw",
    "CIN": "cin", "CLE": "cle", "COL": "col", "DET": "det", "HOU": "hou", "KC": "kc",
    "LAA": "laa", "LAD": "lad", "MIA": "mia", "MIL": "mil", "MIN": "min", "NYM": "nym",
    "NYY": "nyy", "ATH": "oak", "OAK": "oak", "PHI": "phi", "PIT": "pit", "SD": "sd",
    "SEA": "sea", "SF": "sf", "STL": "stl", "TB": "tb", "TEX": "tex", "TOR": "tor", "WSH": "wsh",
}

def team_abbr(team: dict) -> str:
    team_id = team.get("id")
    abbr = team.get("abbreviation") or TEAM_ABBR_BY_ID.get(team_id)
    if abbr:
        return str(abbr).upper()

    name = (team.get("name") or team.get("teamName") or "").lower()
    name_map = {
        "angels": "LAA", "diamondbacks": "AZ", "orioles": "BAL", "red sox": "BOS",
        "cubs": "CHC", "reds": "CIN", "guardians": "CLE", "rockies": "COL",
        "tigers": "DET", "astros": "HOU", "royals": "KC", "dodgers": "LAD",
        "nationals": "WSH", "mets": "NYM", "athletics": "ATH", "pirates": "PIT",
        "padres": "SD", "mariners": "SEA", "giants": "SF", "cardinals": "STL",
        "rays": "TB", "rangers": "TEX", "blue jays": "TOR", "twins": "MIN",
        "phillies": "PHI", "braves": "ATL", "white sox": "CWS", "marlins": "MIA",
        "yankees": "NYY", "brewers": "MIL",
    }
    for key, val in name_map.items():
        if key in name:
            return val
    return "MLB"

def team_logo(abbr: str):
    slug = TEAM_LOGO_SLUGS.get((abbr or "").upper())
    if not slug:
        return None
    return f"https://a.espncdn.com/i/teamlogos/mlb/500/{slug}.png"

def normalize_team(team: dict) -> dict:
    abbr = team_abbr(team)
    return {
        **team,
        "abbreviation": abbr,
        "logo": team_logo(abbr),
    }

@app.get("/")
def root():
    return {"status": "ok", "message": "HR API running. Use /api/games"}

@app.get("/games")
@app.get("/api/games")
def games():
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}"
    data = requests.get(url, timeout=25).json()

    games_out = []

    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            away_raw = ((game.get("teams") or {}).get("away") or {}).get("team", {}) or {}
            home_raw = ((game.get("teams") or {}).get("home") or {}).get("team", {}) or {}

            away = normalize_team(away_raw)
            home = normalize_team(home_raw)

            away_abbr = away["abbreviation"]
            home_abbr = home["abbreviation"]

            games_out.append({
                "gamePk": game.get("gamePk"),
                "gameDate": game.get("gameDate"),
                "status": ((game.get("status") or {}).get("detailedState")) or "Scheduled",
                "away": away,
                "home": home,
                "awayProbablePitcher": ((game.get("teams") or {}).get("away") or {}).get("probablePitcher"),
                "homeProbablePitcher": ((game.get("teams") or {}).get("home") or {}).get("probablePitcher"),
                "label": f"{away_abbr} @ {home_abbr}",
                "awayLogo": team_logo(away_abbr),
                "homeLogo": team_logo(home_abbr),
            })

    return {
        "date": today,
        "games": games_out,
    }

@app.get("/game/{game_pk}")
@app.get("/api/game/{game_pk}")
def game_detail(game_pk: int):
    # Placeholder hitter rows so the dashboard does not render empty while
    # we wire in the full kHR scoring engine.
    return {
        "gamePk": game_pk,
        "hitters": [],
        "message": "Game endpoint is live. Hitter scoring is the next step."
    }
