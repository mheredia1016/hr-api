from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from functools import lru_cache
from pathlib import Path
import json
import math
import os

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
CACHE_DIR = Path(os.getenv("CACHE_DIR", "/tmp/hr_api_cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

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
    r = SESSION.get(url, timeout=30)
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
        if value in (None, "", "-.--", "null"):
            return default
        n = float(value)
        if math.isnan(n):
            return default
        return n
    except Exception:
        return default

def safe_int(value, default=0):
    try:
        if value in (None, "", "-.--", "null"):
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

def is_barrel(ev, la):
    """
    Approx barrel classifier for Statcast batted ball rows.
    This is not exact Savant barrel logic, but tracks the same idea:
    high EV with HR-friendly launch angle.
    """
    ev = safe_float(ev, 0)
    la = safe_float(la, -999)
    return ev >= 98 and 8 <= la <= 32

def is_sweet_spot(la):
    la = safe_float(la, -999)
    return 8 <= la <= 32

def statcast_cache_file(start_date, end_date):
    return CACHE_DIR / f"statcast_batters_{start_date}_{end_date}.json"

def try_load_cache(path: Path):
    if not path.exists():
        return None
    try:
        # Use cache for 18 hours.
        if (datetime.now().timestamp() - path.stat().st_mtime) > (18 * 3600):
            return None
        return json.loads(path.read_text())
    except Exception:
        return None

def save_cache(path: Path, data):
    try:
        path.write_text(json.dumps(data))
    except Exception:
        pass

def fetch_statcast_rows(start_date, end_date):
    """
    Pulls Baseball Savant Statcast CSV directly.
    Avoids depending on pybaseball's heavy pandas stack on Railway.
    """
    cache_path = statcast_cache_file(start_date, end_date)
    cached = try_load_cache(cache_path)
    if cached is not None:
        return cached

    url = "https://baseballsavant.mlb.com/statcast_search/csv"
    params = {
        "all": "true",
        "hfPT": "",
        "hfAB": "",
        "hfGT": "R|",
        "hfPR": "",
        "hfZ": "",
        "stadium": "",
        "hfBBL": "",
        "hfNewZones": "",
        "hfPull": "",
        "hfC": "",
        "hfSea": str(current_season()) + "|",
        "hfSit": "",
        "player_type": "batter",
        "hfOuts": "",
        "opponent": "",
        "pitcher_throws": "",
        "batter_stands": "",
        "hfSA": "",
        "game_date_gt": start_date,
        "game_date_lt": end_date,
        "hfInfield": "",
        "team": "",
        "position": "",
        "hfOutfield": "",
        "hfRO": "",
        "home_road": "",
        "hfFlag": "",
        "metric_1": "",
        "hfInn": "",
        "min_pitches": "0",
        "min_results": "0",
        "group_by": "name",
        "sort_col": "pitches",
        "player_event_sort": "api_p_release_speed",
        "sort_order": "desc",
        "min_pas": "0",
        "type": "details",
    }

    try:
        r = SESSION.get(url, params=params, timeout=60)
        r.raise_for_status()
        text = r.text
        rows = csv_to_dicts(text)
        save_cache(cache_path, rows)
        return rows
    except Exception:
        save_cache(cache_path, [])
        return []

def csv_to_dicts(text):
    import csv
    from io import StringIO
    if not text or "player_name" not in text[:5000]:
        return []
    return list(csv.DictReader(StringIO(text)))

def build_statcast_profiles(days=21):
    """
    Aggregates real Baseball Savant batted-ball data by batter id.
    Uses dates before today only.
    """
    end = day_str(1)
    start = (now_ct() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = fetch_statcast_rows(start, end)

    profiles = {}
    for row in rows:
        batter_id = safe_int(row.get("batter"), 0)
        if not batter_id:
            continue

        p = profiles.setdefault(batter_id, {
            "pitches": 0,
            "bip": 0,
            "ev_sum": 0.0,
            "ev_count": 0,
            "max_ev": None,
            "la_sum": 0.0,
            "la_count": 0,
            "hard_hit": 0,
            "sweet": 0,
            "fb": 0,
            "barrels": 0,
            "pulled_barrels": 0,
            "near_hr": 0,
            "recent_hr": 0,
            "last_hr_ev": None,
            "xwoba_sum": 0.0,
            "xwoba_count": 0,
            "xwobacon_sum": 0.0,
            "xwobacon_count": 0,
            "swinging_strikes": 0,
        })

        p["pitches"] += 1

        desc = (row.get("description") or "").lower()
        if "swinging_strike" in desc or desc in {"swinging_strike", "swinging_strike_blocked"}:
            p["swinging_strikes"] += 1

        ev = safe_float(row.get("launch_speed"), None)
        la = safe_float(row.get("launch_angle"), None)
        dist = safe_float(row.get("hit_distance_sc"), None)
        event = (row.get("events") or "").lower()

        xwoba = safe_float(row.get("estimated_woba_using_speedangle"), None)
        if xwoba is not None:
            p["xwoba_sum"] += xwoba
            p["xwoba_count"] += 1

        if ev is not None and la is not None:
            p["bip"] += 1
            p["ev_sum"] += ev
            p["ev_count"] += 1
            p["max_ev"] = ev if p["max_ev"] is None else max(p["max_ev"], ev)
            p["la_sum"] += la
            p["la_count"] += 1

            if ev >= 95:
                p["hard_hit"] += 1
            if is_sweet_spot(la):
                p["sweet"] += 1
            if la >= 15:
                p["fb"] += 1
            if is_barrel(ev, la):
                p["barrels"] += 1
                if la >= 15:
                    p["pulled_barrels"] += 1
            if event == "home_run":
                p["recent_hr"] += 1
                p["last_hr_ev"] = ev
            elif ev >= 102 and 22 <= la <= 38 and dist is not None and dist >= 375:
                p["near_hr"] += 1

            if xwoba is not None:
                p["xwobacon_sum"] += xwoba
                p["xwobacon_count"] += 1

    out = {}
    for pid, p in profiles.items():
        bip = p["bip"]
        pitches = p["pitches"]
        out[pid] = {
            "pitches": pitches,
            "BIP": bip,
            "avgEV": round(p["ev_sum"] / p["ev_count"], 1) if p["ev_count"] else None,
            "maxEV": round(p["max_ev"], 1) if p["max_ev"] is not None else None,
            "LA": round(p["la_sum"] / p["la_count"], 1) if p["la_count"] else None,
            "HH": round((p["hard_hit"] / bip) * 100, 1) if bip else None,
            "sweetSpot": round((p["sweet"] / bip) * 100, 1) if bip else None,
            "FB": round((p["fb"] / bip) * 100, 1) if bip else None,
            "brlBip": round((p["barrels"] / bip) * 100, 1) if bip else None,
            "pulledBrl": round((p["pulled_barrels"] / bip) * 100, 1) if bip else None,
            "nearHR": p["near_hr"],
            "recentHR": p["recent_hr"],
            "lastHREV": round(p["last_hr_ev"], 1) if p["last_hr_ev"] is not None else None,
            "xwOBA": round(p["xwoba_sum"] / p["xwoba_count"], 3) if p["xwoba_count"] else None,
            "xwOBAcon": round(p["xwobacon_sum"] / p["xwobacon_count"], 3) if p["xwobacon_count"] else None,
            "swStr": round((p["swinging_strikes"] / pitches) * 100, 1) if pitches else None,
        }
    return out

def fallback_profile_from_season(stat):
    ab = safe_float(stat.get("atBats"), 0)
    hr = safe_float(stat.get("homeRuns"), 0)
    hits = safe_float(stat.get("hits"), 0)
    doubles = safe_float(stat.get("doubles"), 0)
    triples = safe_float(stat.get("triples"), 0)
    so = safe_float(stat.get("strikeOuts"), 0)
    tb = safe_float(stat.get("totalBases"), hits + doubles + (2 * triples) + (3 * hr))
    iso = ((tb / ab) - (hits / ab)) if ab else 0
    hr_rate = (hr / ab) if ab else 0

    return {
        "pitches": safe_int(stat.get("numberOfPitches"), 0),
        "BIP": max(0, int(ab - so)),
        "HH": round(min(55, max(25, 30 + iso * 85)), 1),
        "FB": round(min(48, max(25, 30 + hr_rate * 260)), 1),
        "brlBip": round(min(18, max(3, iso * 45)), 1),
        "sweetSpot": round(min(45, max(28, 32 + iso * 35)), 1),
        "pulledBrl": round(min(11, max(2, min(18, max(3, iso * 45)) * 0.55)), 1),
        "LA": round(min(24, max(10, 12 + iso * 35)), 1),
        "nearHR": 0,
        "recentHR": 0,
        "maxEV": None,
        "lastHREV": None,
        "xwOBA": None,
        "xwOBAcon": None,
        "swStr": None,
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

    max_ev = safe_float(profile.get("maxEV"), 0)
    last_hr_ev = safe_float(profile.get("lastHREV"), 0)
    hh = safe_float(profile.get("HH"), 0)
    fb = safe_float(profile.get("FB"), 0)
    brl = safe_float(profile.get("brlBip"), 0)
    pull_brl = safe_float(profile.get("pulledBrl"), 0)
    sweet = safe_float(profile.get("sweetSpot"), 0)
    la = safe_float(profile.get("LA"), 0)
    near = safe_float(profile.get("nearHR"), 0)
    recent_hr = safe_float(profile.get("recentHR"), 0)
    xwoba = profile.get("xwOBA")
    xwobacon = profile.get("xwOBAcon")
    p_hr9 = safe_float(opp_pitcher_hr9, 1.0)

    score = 15
    score += min(20, iso * 75)
    score += min(12, hr * 0.55)
    score += min(12, recent_hr * 3)
    score += min(8, near * 2)
    score += min(10, max(0, max_ev - 98) * 0.85)
    score += min(7, max(0, last_hr_ev - 98) * 0.65)
    score += min(12, brl * 0.7)
    score += min(8, pull_brl * 0.8)
    score += min(7, hh * 0.10)
    score += min(5, fb * 0.10)
    score += min(4, sweet * 0.08)
    if 13 <= la <= 28:
        score += 4
    if xwoba:
        score += min(5, max(0, (xwoba - .300) * 35))
    if xwobacon:
        score += min(5, max(0, (xwobacon - .340) * 30))
    score += min(7, max(0, p_hr9 - 0.8) * 5)

    khr = round(max(0, min(100, score)), 3)

    fallback_xwoba = round(max(0.250, min(0.450, 0.260 + (ops * 0.12) + (iso * 0.25))), 3) if ab else None
    fallback_xwobacon = round(max(0.280, min(0.500, 0.300 + (slg * 0.18) + (iso * 0.30))), 3) if ab else None

    return {
        "ISO": round(iso, 3) if ab else None,
        "xwOBA": xwoba if xwoba is not None else fallback_xwoba,
        "xwOBAcon": xwobacon if xwobacon is not None else fallback_xwobacon,
        "matchup": round(max(0, min(100, khr + (p_hr9 * 1.5) - 3)), 3),
        "testScore": khr,
        "ceiling": round(max(0, min(100, khr + min(12, (max_ev - 100) if max_ev else 0) + min(8, near * 2))), 3),
        "zoneFit": round(max(0.030, min(0.120, 0.045 + (brl * 0.002) + (sweet * 0.0005) + (0.010 if 13 <= la <= 28 else 0))), 3),
        "kHR": khr,
        "likely": round(max(1, min(99, khr * 0.72)), 0),
    }

def hitter_row(player, team, opp_pitcher, statcast_profiles):
    pid = player.get("playerId")
    stat = hitter_season_stats(int(pid), current_season()) if pid else {}
    profile = statcast_profiles.get(int(pid)) if pid else None
    profile_source = "Statcast 21-day"
    if not profile:
        profile = fallback_profile_from_season(stat)
        profile_source = "Season estimate fallback"

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

        "pitches": profile.get("pitches") or safe_int(stat.get("numberOfPitches"), 0),
        "BIP": profile.get("BIP"),
        "ISO": scores["ISO"],
        "xwOBA": scores["xwOBA"],
        "xwOBAcon": scores["xwOBAcon"],
        "swStr": profile.get("swStr") if profile.get("swStr") is not None else (round((so / pa) * 100, 1) if pa else None),
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
        "hrForm": f"{profile.get('recentHR', 0)} HR / {profile.get('nearHR', 0)} near",
        "kHR": scores["kHR"],
        "likely": scores["likely"],
        "status": profile_source,
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

    # One Statcast pull/cache per request cycle. Cached for the day.
    statcast_profiles = build_statcast_profiles(days=21)

    rows = []
    for p in away_roster:
        rows.append(hitter_row(p, away, home_pitcher, statcast_profiles))
    for p in home_roster:
        rows.append(hitter_row(p, home, away_pitcher, statcast_profiles))

    rows.sort(key=lambda r: (-safe_float(r.get("kHR")), r.get("team", ""), r.get("name", "")))
    return rows

@app.get("/")
def root():
    return {"status": "ok", "message": "HR API running. Uses Statcast 21-day batted-ball profiles when available."}

@app.get("/games")
@app.get("/api/games")
def games():
    d = day_str(0)
    return {"date": d, "games": get_games_raw(d)}

@app.get("/game/{game_pk}")
@app.get("/api/game/{game_pk}")
def game_detail(game_pk: int):
    hitters = collect_hitter_rows(game_pk)
    return {
        "gamePk": game_pk,
        "count": len(hitters),
        "source": "Statcast 21-day historical batted-ball profile + MLB season stats + probable pitcher HR/9",
        "hitters": hitters,
    }

@app.get("/api/cache/status")
def cache_status():
    files = []
    for path in CACHE_DIR.glob("*.json"):
        files.append({
            "file": path.name,
            "size": path.stat().st_size,
            "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
        })
    return {"cacheDir": str(CACHE_DIR), "files": files}
