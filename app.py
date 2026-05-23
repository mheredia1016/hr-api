from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from functools import lru_cache

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

def now_ct():
    return datetime.now(TZ)

def current_season():
    return now_ct().year

def day_str(days_ago=0):
    return (now_ct() - timedelta(days=days_ago)).strftime("%Y-%m-%d")

def safe_float(value, default=0.0):
    try:
        if value in (None, "", "-.--"):
            return default
        return float(value)
    except Exception:
        return default

def safe_int(value, default=0):
    try:
        if value in (None, "", "-.--"):
            return default
        return int(float(value))
    except Exception:
        return default

def team_abbr(team: dict) -> str:
    team_id = team.get("id")
    abbr = team.get("abbreviation") or TEAM_ABBR_BY_ID.get(team_id)
    return str(abbr).upper() if abbr else "MLB"

def team_logo(abbr: str):
    slug = TEAM_LOGO_SLUGS.get((abbr or "").upper())
    return f"https://a.espncdn.com/i/teamlogos/mlb/500/{slug}.png" if slug else None

def player_headshot(player_id):
    return f"https://img.mlbstatic.com/mlb-photos/image/upload/w_180/v1/people/{player_id}/headshot/67/current" if player_id else None

def normalize_team(team: dict) -> dict:
    abbr = team_abbr(team or {})
    return {**(team or {}), "abbreviation": abbr, "logo": team_logo(abbr)}

def normalize_game(game):
    away_block = ((game.get("teams") or {}).get("away") or {})
    home_block = ((game.get("teams") or {}).get("home") or {})
    away = normalize_team(away_block.get("team", {}) or {})
    home = normalize_team(home_block.get("team", {}) or {})
    away_abbr = away["abbreviation"]
    home_abbr = home["abbreviation"]

    return {
        "gamePk": game.get("gamePk"),
        "gameDate": game.get("gameDate"),
        "status": ((game.get("status") or {}).get("detailedState")) or "Scheduled",
        "away": away,
        "home": home,
        "awayProbablePitcher": away_block.get("probablePitcher"),
        "homeProbablePitcher": home_block.get("probablePitcher"),
        "label": f"{away_abbr} @ {home_abbr}",
        "awayLogo": team_logo(away_abbr),
        "homeLogo": team_logo(home_abbr),
    }

def get_games_raw(date):
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}&hydrate=probablePitcher"
    data = get_json(url)
    out = []
    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            out.append(normalize_game(game))
    return out

def get_game_by_pk(game_pk: int):
    # Direct lookup by gamePk first. This prevents empty hitters if frontend has a valid game ID
    # but today schedule filtering misses it.
    urls = [
        f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&gamePk={game_pk}&hydrate=probablePitcher",
        f"https://statsapi.mlb.com/api/v1/schedule?gamePk={game_pk}&hydrate=probablePitcher",
    ]

    for url in urls:
        try:
            data = get_json(url)
            for date_block in data.get("dates", []) or []:
                for game in date_block.get("games", []) or []:
                    if int(game.get("gamePk")) == int(game_pk):
                        return normalize_game(game)
        except Exception:
            pass

    # Fallback: scan a 7 day window around today.
    for offset in range(-2, 5):
        d = (now_ct() + timedelta(days=offset)).strftime("%Y-%m-%d")
        try:
            for game in get_games_raw(d):
                if int(game.get("gamePk")) == int(game_pk):
                    return game
        except Exception:
            continue

    return None

@lru_cache(maxsize=256)
def active_roster(team_id: int):
    rows = []
    if not team_id:
        return rows

    # Active roster is the main source.
    urls = [
        f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster?rosterType=active",
        f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster?rosterType=40Man",
    ]

    seen = set()
    for url in urls:
        try:
            data = get_json(url)
        except Exception:
            continue

        for item in data.get("roster", []) or []:
            person = item.get("person") or {}
            pid = person.get("id")
            if not pid or pid in seen:
                continue

            pos = (item.get("position") or {}).get("abbreviation", "")
            if pos == "P":
                continue

            seen.add(pid)
            rows.append({
                "playerId": pid,
                "name": person.get("fullName", "Unknown"),
                "position": pos,
            })

        if rows:
            return rows

    return rows

@lru_cache(maxsize=4096)
def hitter_season_stats(player_id: int, season: int):
    try:
        url = f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats?stats=season&group=hitting&season={season}"
        data = get_json(url)
        splits = (data.get("stats") or [{}])[0].get("splits") or []
        if not splits:
            return {}
        return splits[0].get("stat") or {}
    except Exception:
        return {}

@lru_cache(maxsize=4096)
def pitcher_season_stats(player_id: int, season: int):
    try:
        url = f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats?stats=season&group=pitching&season={season}"
        data = get_json(url)
        splits = (data.get("stats") or [{}])[0].get("splits") or []
        if not splits:
            return {}
        return splits[0].get("stat") or {}
    except Exception:
        return {}

def pitcher_hr9(pitcher_id):
    if not pitcher_id:
        return None
    stat = pitcher_season_stats(int(pitcher_id), current_season())
    hr = safe_float(stat.get("homeRuns"), 0)
    ip = safe_float(stat.get("inningsPitched"), 0)
    return round((hr / ip) * 9, 2) if ip > 0 else None

def quick_recent_profile_from_season(stat):
    # Fast fallback profile so rows always render even before richer rolling-contact data is added.
    ab = safe_float(stat.get("atBats"), 0)
    hr = safe_float(stat.get("homeRuns"), 0)
    hits = safe_float(stat.get("hits"), 0)
    doubles = safe_float(stat.get("doubles"), 0)
    triples = safe_float(stat.get("triples"), 0)
    so = safe_float(stat.get("strikeOuts"), 0)
    tb = safe_float(stat.get("totalBases"), hits + doubles + (2 * triples) + (3 * hr))

    iso = ((tb / ab) - (hits / ab)) if ab else 0
    hr_rate = (hr / ab) if ab else 0

    # Derived estimates from season power profile.
    hh = min(55, max(25, 30 + iso * 85))
    fb = min(48, max(25, 30 + hr_rate * 260))
    brl = min(18, max(3, iso * 45))
    sweet = min(45, max(28, 32 + iso * 35))
    pulled = min(11, max(2, brl * 0.55))
    la = min(24, max(10, 12 + iso * 35))

    return {
        "BIP": max(0, int(ab - so)),
        "HH": round(hh, 1),
        "FB": round(fb, 1),
        "sweetSpot": round(sweet, 1),
        "brlBip": round(brl, 1),
        "pulledBrl": round(pulled, 1),
        "LA": round(la, 1),
        "recentHR": 0,
        "nearHR": 0,
        "maxEV": None,
        "lastHREV": None,
    }

def score_hitter(stat, profile, opp_pitcher_hr9):
    ab = safe_float(stat.get("atBats"), 0)
    hr = safe_float(stat.get("homeRuns"), 0)
    hits = safe_float(stat.get("hits"), 0)
    doubles = safe_float(stat.get("doubles"), 0)
    triples = safe_float(stat.get("triples"), 0)
    tb = safe_float(stat.get("totalBases"), hits + doubles + (2 * triples) + (3 * hr))
    slg = safe_float(stat.get("slg"), 0)
    ops = safe_float(stat.get("ops"), 0)
    iso = ((tb / ab) - (hits / ab)) if ab else 0

    hh = safe_float(profile.get("HH"), 0)
    fb = safe_float(profile.get("FB"), 0)
    brl = safe_float(profile.get("brlBip"), 0)
    pull_brl = safe_float(profile.get("pulledBrl"), 0)
    sweet = safe_float(profile.get("sweetSpot"), 0)
    la = safe_float(profile.get("LA"), 0)
    p_hr9 = safe_float(opp_pitcher_hr9, 1.0)

    score = 20
    score += min(26, iso * 95)
    score += min(15, hr * 0.75)
    score += min(10, brl * 0.7)
    score += min(7, pull_brl * 0.7)
    score += min(7, hh * 0.11)
    score += min(6, fb * 0.12)
    score += min(5, sweet * 0.09)
    if 13 <= la <= 28:
        score += 4
    score += min(7, max(0, p_hr9 - 0.8) * 5)

    khr = round(max(0, min(100, score)), 3)

    return {
        "ISO": round(iso, 3) if ab else None,
        "xwOBA": round(max(0.250, min(0.450, 0.260 + (ops * 0.12) + (iso * 0.25))), 3) if ab else None,
        "xwOBAcon": round(max(0.280, min(0.500, 0.300 + (slg * 0.18) + (iso * 0.30))), 3) if ab else None,
        "matchup": round(max(0, min(100, khr + (p_hr9 * 1.5) - 3)), 3),
        "testScore": khr,
        "ceiling": round(max(0, min(100, khr + min(10, brl * 0.45) + min(8, iso * 20))), 3),
        "zoneFit": round(max(0.030, min(0.120, 0.045 + (brl * 0.002) + (sweet * 0.0005) + (0.010 if 13 <= la <= 28 else 0))), 3),
        "kHR": khr,
        "likely": round(max(1, min(99, khr * 0.72)), 0),
    }

def hitter_row(player, team, opp_pitcher):
    pid = player.get("playerId")
    stat = hitter_season_stats(int(pid), current_season()) if pid else {}
    profile = quick_recent_profile_from_season(stat)
    opp_hr9 = pitcher_hr9(opp_pitcher.get("id")) if opp_pitcher else None
    scores = score_hitter(stat, profile, opp_hr9)

    ab = safe_int(stat.get("atBats"), 0)
    pa = safe_int(stat.get("plateAppearances"), 0)
    so = safe_int(stat.get("strikeOuts"), 0)
    hr = safe_int(stat.get("homeRuns"), 0)

    return {
        "playerId": pid,
        "name": player.get("name", "Unknown"),
        "team": team.get("abbreviation", "MLB"),
        "teamLogo": team_logo(team.get("abbreviation", "MLB")),
        "headshot": player_headshot(pid),
        "pitcher": (opp_pitcher or {}).get("fullName") or (opp_pitcher or {}).get("name") or "TBD",

        "AB": ab,
        "PA": pa,
        "H": safe_int(stat.get("hits"), 0),
        "HR": hr,
        "RBI": safe_int(stat.get("rbi"), 0),
        "BB": safe_int(stat.get("baseOnBalls"), 0),
        "SO": so,

        "pitches": safe_int(stat.get("numberOfPitches"), 0),
        "BIP": profile.get("BIP"),
        "ISO": scores["ISO"],
        "xwOBA": scores["xwOBA"],
        "xwOBAcon": scores["xwOBAcon"],
        "swStr": round((so / pa) * 100, 1) if pa else None,
        "pulledBrl": profile.get("pulledBrl"),
        "brlBip": profile.get("brlBip"),
        "sweetSpot": profile.get("sweetSpot"),
        "FB": profile.get("FB"),
        "HH": profile.get("HH"),
        "LA": profile.get("LA"),
        "nearHR": profile.get("nearHR"),
        "maxEV": profile.get("maxEV"),
        "lastHREV": profile.get("lastHREV"),

        "matchup": scores["matchup"],
        "testScore": scores["testScore"],
        "ceiling": scores["ceiling"],
        "zoneFit": scores["zoneFit"],
        "hrForm": f"{hr} season HR",
        "kHR": scores["kHR"],
        "likely": scores["likely"],
        "status": "Pregame historical profile",
    }

def collect_hitter_rows(game_pk: int):
    game = get_game_by_pk(game_pk)
    if not game:
        return []

    away = normalize_team(game["away"])
    home = normalize_team(game["home"])
    away_pitcher = game.get("awayProbablePitcher") or {}
    home_pitcher = game.get("homeProbablePitcher") or {}

    away_roster = active_roster(int(away.get("id") or 0))
    home_roster = active_roster(int(home.get("id") or 0))

    rows = []
    for p in away_roster:
        rows.append(hitter_row(p, away, home_pitcher))
    for p in home_roster:
        rows.append(hitter_row(p, home, away_pitcher))

    rows.sort(key=lambda r: (-safe_float(r.get("kHR")), r.get("team", ""), r.get("name", "")))
    return rows

@app.get("/")
def root():
    return {"status": "ok", "message": "HR API running. Uses historical pregame profiles."}

@app.get("/games")
@app.get("/api/games")
def games():
    d = day_str(0)
    return {"date": d, "games": get_games_raw(d)}

@app.get("/game/{game_pk}")
@app.get("/api/game/{game_pk}")
def game_detail(game_pk: int):
    hitters = collect_hitter_rows(game_pk)
    return {"gamePk": game_pk, "count": len(hitters), "hitters": hitters}
