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
SESSION = requests.Session()

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

def get_json(url):
    r = SESSION.get(url, timeout=25)
    r.raise_for_status()
    return r.json()

def team_abbr(team: dict) -> str:
    team_id = team.get("id")
    abbr = team.get("abbreviation") or TEAM_ABBR_BY_ID.get(team_id)
    if abbr:
        return str(abbr).upper()
    return "MLB"

def team_logo(abbr: str):
    slug = TEAM_LOGO_SLUGS.get((abbr or "").upper())
    if not slug:
        return None
    return f"https://a.espncdn.com/i/teamlogos/mlb/500/{slug}.png"

def player_headshot(player_id):
    return f"https://img.mlbstatic.com/mlb-photos/image/upload/w_180/v1/people/{player_id}/headshot/67/current" if player_id else None

def normalize_team(team: dict) -> dict:
    abbr = team_abbr(team or {})
    return {
        **(team or {}),
        "abbreviation": abbr,
        "logo": team_logo(abbr),
    }

def safe_float(value, default=0.0):
    try:
        if value in (None, "", "-.--"):
            return default
        return float(value)
    except Exception:
        return default

def is_home_run(play):
    result = play.get("result", {}) or {}
    event_type = (result.get("eventType") or "").lower()
    event = (result.get("event") or "").lower()
    desc = (result.get("description") or "").lower()
    return event_type == "home_run" or "home run" in event or "homers" in desc or "home run" in desc or "grand slam" in desc

def get_metrics(play):
    for event in reversed(play.get("playEvents", []) or []):
        hit_data = event.get("hitData")
        if hit_data:
            return hit_data
    return None

def estimate_hr_parks(distance, ev, launch_angle=None):
    distance = safe_float(distance)
    ev = safe_float(ev)
    la = safe_float(launch_angle)
    if distance >= 430: return 30
    if distance >= 425: return 29
    if distance >= 420: return 27
    if distance >= 415: return 25
    if distance >= 410: return 23
    if distance >= 405: return 20
    if distance >= 400 and ev >= 108: return 18
    if distance >= 395 and ev >= 106: return 15
    if distance >= 390 and ev >= 104: return 12
    if distance >= 375 and ev >= 102 and 15 <= la <= 25: return 6
    return 0

def contact_score(ev, la, distance, hr_count=0):
    ev = safe_float(ev)
    la = safe_float(la)
    distance = safe_float(distance)
    score = 35
    if ev >= 110: score += 28
    elif ev >= 105: score += 20
    elif ev >= 100: score += 12
    elif ev >= 95: score += 6
    if 18 <= la <= 32: score += 14
    elif 10 <= la <= 38: score += 8
    if distance >= 410: score += 18
    elif distance >= 390: score += 12
    elif distance >= 370: score += 7
    score += min(int(hr_count or 0), 3) * 12
    return max(0, min(100, round(score, 1)))

def get_games_raw(date_str):
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date_str}"
    data = get_json(url)
    out = []
    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            away_raw = ((game.get("teams") or {}).get("away") or {}).get("team", {}) or {}
            home_raw = ((game.get("teams") or {}).get("home") or {}).get("team", {}) or {}
            away = normalize_team(away_raw)
            home = normalize_team(home_raw)
            away_abbr = away["abbreviation"]
            home_abbr = home["abbreviation"]
            out.append({
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
    return out

def collect_hitter_rows(game_pk: int):
    box = get_json(f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore")
    rows_by_id = {}

    for side in ("away", "home"):
        team_block = ((box.get("teams") or {}).get(side) or {})
        team = normalize_team(team_block.get("team") or {})
        team_abbr_val = team.get("abbreviation", "MLB")
        players = team_block.get("players") or {}

        for player in players.values():
            person = player.get("person") or {}
            pid = person.get("id")
            batting = ((player.get("stats") or {}).get("batting") or {})
            if not pid:
                continue

            # Include starters / players with PA/AB, and keep bench players if batting stats exist.
            ab = int(batting.get("atBats", 0) or 0)
            pa = int(batting.get("plateAppearances", 0) or 0)
            hits = int(batting.get("hits", 0) or 0)
            hr = int(batting.get("homeRuns", 0) or 0)

            rows_by_id[pid] = {
                "playerId": pid,
                "name": person.get("fullName", "Unknown"),
                "team": team_abbr_val,
                "teamLogo": team_logo(team_abbr_val),
                "headshot": player_headshot(pid),
                "AB": ab,
                "PA": pa,
                "H": hits,
                "HR": hr,
                "RBI": int(batting.get("rbi", 0) or 0),
                "BB": int(batting.get("baseOnBalls", 0) or 0),
                "SO": int(batting.get("strikeOuts", 0) or 0),
                "EV": None,
                "LA": None,
                "Distance": None,
                "HRParks": 0,
                "kHR": 35 + min(hr, 3) * 12,
                "status": "No contact yet" if pa == 0 and ab == 0 else "Boxscore",
            }

    # Add live contact metrics where available.
    try:
        live = get_json(f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live")
        plays = (((live.get("liveData") or {}).get("plays") or {}).get("allPlays") or [])
        best_contact = {}

        for play in plays:
            batter = ((play.get("matchup") or {}).get("batter") or {})
            pid = batter.get("id")
            if not pid:
                continue

            metrics = get_metrics(play)
            hr = is_home_run(play)

            if not metrics and not hr:
                continue

            ev = safe_float((metrics or {}).get("launchSpeed"), 0)
            la = safe_float((metrics or {}).get("launchAngle"), 0)
            dist = safe_float((metrics or {}).get("totalDistance"), 0)
            score = contact_score(ev, la, dist, 1 if hr else 0)

            cur = best_contact.get(pid)
            if not cur or score > cur["score"]:
                best_contact[pid] = {
                    "EV": ev or None,
                    "LA": la if metrics and (metrics or {}).get("launchAngle") is not None else None,
                    "Distance": dist or None,
                    "HRParks": estimate_hr_parks(dist, ev, la),
                    "score": score,
                    "status": "💣 Home Run" if hr else "Live contact",
                }

        for pid, contact in best_contact.items():
            if pid not in rows_by_id:
                rows_by_id[pid] = {
                    "playerId": pid,
                    "name": "Unknown",
                    "team": "MLB",
                    "teamLogo": None,
                    "headshot": player_headshot(pid),
                    "AB": 0, "PA": 0, "H": 0, "HR": 0, "RBI": 0, "BB": 0, "SO": 0,
                    "EV": None, "LA": None, "Distance": None, "HRParks": 0, "kHR": 35,
                    "status": "Live",
                }
            rows_by_id[pid].update({
                "EV": contact["EV"],
                "LA": contact["LA"],
                "Distance": contact["Distance"],
                "HRParks": contact["HRParks"],
                "kHR": max(rows_by_id[pid].get("kHR", 0), contact["score"]),
                "status": contact["status"],
            })

    except Exception as exc:
        # Keep boxscore rows if live feed is unavailable.
        pass

    rows = list(rows_by_id.values())
    rows.sort(key=lambda r: (-safe_float(r.get("kHR")), -int(r.get("HR", 0) or 0), r.get("team", ""), r.get("name", "")))
    return rows

@app.get("/")
def root():
    return {"status": "ok", "message": "HR API running. Use /api/games"}

@app.get("/games")
@app.get("/api/games")
def games():
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    return {"date": today, "games": get_games_raw(today)}

@app.get("/game/{game_pk}")
@app.get("/api/game/{game_pk}")
def game_detail(game_pk: int):
    return {
        "gamePk": game_pk,
        "hitters": collect_hitter_rows(game_pk),
    }
