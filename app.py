from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from functools import lru_cache
from pathlib import Path
import csv
import json
import math
import os
from io import StringIO
import threading
import time

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

CACHE_MAX_AGE_HOURS = int(os.getenv("CACHE_MAX_AGE_HOURS", "18"))
STATCAST_DAYS = int(os.getenv("STATCAST_DAYS", "30"))

cache_build_lock = threading.Lock()
cache_build_started_at = None
last_cache_error = None

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

def now_ct():
    return datetime.now(TZ)

def current_season():
    return now_ct().year

def day_str(days_ago=0):
    return (now_ct() - timedelta(days=days_ago)).strftime("%Y-%m-%d")

def get_json(url, timeout=20):
    r = SESSION.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()

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

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def scale(v, lo, hi):
    if v is None:
        return 0
    return clamp((safe_float(v) - lo) / (hi - lo), 0, 1)

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
            rows.append({"playerId": pid, "name": person.get("fullName", "Unknown"), "position": pos})
        if rows:
            return rows
    return rows

@lru_cache(maxsize=4096)
def hitter_season_stats(player_id: int, season: int):
    try:
        url = f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats?stats=season&group=hitting&season={season}"
        data = get_json(url)
        splits = (data.get("stats") or [{}])[0].get("splits") or []
        return (splits[0].get("stat") or {}) if splits else {}
    except Exception:
        return {}

@lru_cache(maxsize=4096)
def pitcher_season_stats(player_id: int, season: int):
    try:
        url = f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats?stats=season&group=pitching&season={season}"
        data = get_json(url)
        splits = (data.get("stats") or [{}])[0].get("splits") or []
        return (splits[0].get("stat") or {}) if splits else {}
    except Exception:
        return {}

def pitcher_hr9(pitcher_id):
    if not pitcher_id:
        return None
    stat = pitcher_season_stats(int(pitcher_id), current_season())
    hr = safe_float(stat.get("homeRuns"), 0)
    ip = safe_float(stat.get("inningsPitched"), 0)
    return round((hr / ip) * 9, 2) if ip > 0 else None

def cache_file():
    return CACHE_DIR / f"statcast_profiles_v14_{current_season()}_{day_str(0)}.json"

def cache_is_fresh():
    path = cache_file()
    if not path.exists():
        return False
    age_hours = (time.time() - path.stat().st_mtime) / 3600
    return age_hours <= CACHE_MAX_AGE_HOURS

def load_statcast_cache():
    path = cache_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return {int(k): v for k, v in data.get("profiles", {}).items()}
    except Exception:
        return {}

def cache_meta():
    path = cache_file()
    base = {
        "building": cache_build_lock.locked(),
        "buildStartedAt": cache_build_started_at,
        "lastError": last_cache_error,
    }
    if not path.exists():
        return {**base, "exists": False, "fresh": False}
    try:
        data = json.loads(path.read_text())
        age_hours = round((time.time() - path.stat().st_mtime) / 3600, 2)
        return {
            **base,
            "exists": True,
            "fresh": cache_is_fresh(),
            "ageHours": age_hours,
            "profileCount": len(data.get("profiles", {})),
            "dateRange": data.get("dateRange"),
            "source": data.get("source"),
            "file": path.name,
        }
    except Exception:
        return {**base, "exists": True, "fresh": False, "error": "Could not read cache"}

def savant_csv_rows(start_date, end_date, timeout=90):
    # Important: Baseball Savant date params are inclusive-ish; end_date is yesterday.
    url = "https://baseballsavant.mlb.com/statcast_search/csv"
    params = {
        "all": "true",
        "hfGT": "R|",
        "hfSea": f"{current_season()}|",
        "player_type": "batter",
        "game_date_gt": start_date,
        "game_date_lt": end_date,
        "group_by": "name",
        "min_pitches": "0",
        "min_results": "0",
        "min_pas": "0",
        "type": "details",
    }
    r = SESSION.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    text = r.text
    if "player_name" not in text[:5000]:
        raise RuntimeError(f"Savant CSV returned unexpected response: {text[:120]}")
    return list(csv.DictReader(StringIO(text)))

def is_barrel(ev, la):
    return safe_float(ev) >= 98 and 8 <= safe_float(la, -999) <= 32

def empty_raw():
    return {
        "pitches": 0, "bip": 0, "ev_sum": 0.0, "ev_count": 0, "max_ev": None,
        "la_sum": 0.0, "la_count": 0, "hard_hit": 0, "sweet": 0, "fb": 0,
        "barrels": 0, "pulled_barrels": 0, "near_hr": 0, "recent_hr": 0,
        "last_hr_ev": None, "xwoba_sum": 0.0, "xwoba_count": 0,
        "xwobacon_sum": 0.0, "xwobacon_count": 0, "swinging_strikes": 0,
    }

def add_row_to_raw(p, row):
    p["pitches"] += 1
    desc = (row.get("description") or "").lower()
    if "swinging_strike" in desc:
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
        if 8 <= la <= 32:
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

def finalize_raw(p):
    bip = p["bip"]
    pitches = p["pitches"]
    return {
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

def save_statcast_cache(profiles, start_date, end_date):
    payload = {
        "source": "Baseball Savant Statcast CSV, rolling 30/14/7 pregame windows",
        "generatedAt": datetime.now(TZ).isoformat(),
        "dateRange": {"start": start_date, "end": end_date},
        "profiles": {str(k): v for k, v in profiles.items()},
    }
    cache_file().write_text(json.dumps(payload))

def build_statcast_cache(days=30):
    global cache_build_started_at, last_cache_error
    if not cache_build_lock.acquire(blocking=False):
        return {"ok": False, "message": "Already building"}
    cache_build_started_at = datetime.now(TZ).isoformat()
    last_cache_error = None
    try:
        end_date = day_str(1)
        start_date = (now_ct() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = savant_csv_rows(start_date, end_date)

        raw = {}
        now = now_ct()
        for row in rows:
            batter_id = safe_int(row.get("batter"), 0)
            if not batter_id:
                continue
            gd = row.get("game_date")
            try:
                age = (now.date() - datetime.strptime(gd, "%Y-%m-%d").date()).days
            except Exception:
                age = 999

            group = raw.setdefault(batter_id, {"r30": empty_raw(), "r14": empty_raw(), "r7": empty_raw()})
            if age <= 30:
                add_row_to_raw(group["r30"], row)
            if age <= 14:
                add_row_to_raw(group["r14"], row)
            if age <= 7:
                add_row_to_raw(group["r7"], row)

        profiles = {}
        for pid, group in raw.items():
            r30 = finalize_raw(group["r30"])
            r14 = finalize_raw(group["r14"])
            r7 = finalize_raw(group["r7"])
            profiles[pid] = {
                **r30,
                "r14": r14,
                "r7": r7,
                "recentHR": r14.get("recentHR", 0),
                "nearHR": r14.get("nearHR", 0),
                "formHR7": r7.get("recentHR", 0),
                "formNear7": r7.get("nearHR", 0),
            }

        save_statcast_cache(profiles, start_date, end_date)
        return {"ok": True, "profileCount": len(profiles), "start": start_date, "end": end_date, "rawRows": len(rows)}
    except Exception as exc:
        last_cache_error = str(exc)
        return {"ok": False, "error": str(exc)}
    finally:
        cache_build_lock.release()

def ensure_cache_background():
    if cache_is_fresh() or cache_build_lock.locked():
        return False
    thread = threading.Thread(target=build_statcast_cache, daemon=True)
    thread.start()
    return True

def season_iso(stat):
    ab = safe_float(stat.get("atBats"), 0)
    if not ab:
        return None
    hits = safe_float(stat.get("hits"), 0)
    doubles = safe_float(stat.get("doubles"), 0)
    triples = safe_float(stat.get("triples"), 0)
    hr = safe_float(stat.get("homeRuns"), 0)
    tb = safe_float(stat.get("totalBases"), hits + doubles + (2 * triples) + (3 * hr))
    return round((tb / ab) - (hits / ab), 3)

def profile_from_season(stat):
    ab = safe_float(stat.get("atBats"), 0)
    pa = safe_float(stat.get("plateAppearances"), 0)
    hr = safe_float(stat.get("homeRuns"), 0)
    hits = safe_float(stat.get("hits"), 0)
    doubles = safe_float(stat.get("doubles"), 0)
    triples = safe_float(stat.get("triples"), 0)
    so = safe_float(stat.get("strikeOuts"), 0)
    tb = safe_float(stat.get("totalBases"), hits + doubles + (2 * triples) + (3 * hr))
    iso = ((tb / ab) - (hits / ab)) if ab else 0
    hr_rate = (hr / ab) if ab else 0
    brl = min(20, max(2.5, iso * 48))
    return {
        "pitches": safe_int(stat.get("numberOfPitches"), 0),
        "BIP": max(0, int(ab - so)),
        "HH": round(min(58, max(24, 30 + iso * 90)), 1),
        "FB": round(min(50, max(23, 29 + hr_rate * 280)), 1),
        "brlBip": round(brl, 1),
        "sweetSpot": round(min(46, max(27, 32 + iso * 38)), 1),
        "pulledBrl": round(min(12, max(1.8, brl * 0.58)), 1),
        "LA": round(min(25, max(9, 11 + iso * 42)), 1),
        "nearHR": 0,
        "recentHR": 0,
        "formHR7": 0,
        "formNear7": 0,
        "maxEV": None,
        "lastHREV": None,
        "xwOBA": None,
        "xwOBAcon": None,
        "swStr": round((so / pa) * 100, 1) if pa else None,
    }

def calibrated_scores(stat, profile, opp_hr9, cache_hit):
    iso = season_iso(stat) or 0
    hr = safe_float(stat.get("homeRuns"), 0)
    ab = safe_float(stat.get("atBats"), 0)
    hr_rate = (hr / ab) if ab else 0
    slg = safe_float(stat.get("slg"), 0)
    ops = safe_float(stat.get("ops"), 0)

    brl = safe_float(profile.get("brlBip"), 0)
    pulled = safe_float(profile.get("pulledBrl"), 0)
    hh = safe_float(profile.get("HH"), 0)
    fb = safe_float(profile.get("FB"), 0)
    sweet = safe_float(profile.get("sweetSpot"), 0)
    la = safe_float(profile.get("LA"), 0)
    max_ev = safe_float(profile.get("maxEV"), 0)
    xwoba = profile.get("xwOBA")
    xwobacon = profile.get("xwOBAcon")
    recent_hr = safe_float(profile.get("recentHR"), 0)
    near = safe_float(profile.get("nearHR"), 0)
    p_hr9 = safe_float(opp_hr9, 1.0)

    # Calibrated closer to reference: mid bats 35-55, top bats 55-70.
    base = 30
    power = (
        scale(iso, .060, .260) * 12 +
        scale(hr_rate, .005, .070) * 7 +
        scale(brl, 3, 15) * 10 +
        scale(pulled, 1, 8) * 6 +
        scale(hh, 28, 55) * 7 +
        scale(fb, 22, 48) * 5
    )
    quality = (
        scale(sweet, 25, 45) * 4 +
        (3 if 12 <= la <= 28 else 0) +
        scale(max_ev, 95, 113) * 5
    )
    form = min(6, recent_hr * 1.8) + min(4, near * 1.2)
    pitcher = min(5, max(0, p_hr9 - 0.80) * 4)

    expected = 0
    if xwoba:
        expected += min(3, max(0, (xwoba - .300) * 24))
    if xwobacon:
        expected += min(3, max(0, (xwobacon - .340) * 20))

    raw = base + power + quality + form + pitcher + expected

    bip = safe_float(profile.get("BIP"), 0)
    confidence = clamp(bip / 120, 0.40 if cache_hit else 0.62, 1.0)
    anchor = 42 if cache_hit else 38
    khr = round(clamp((raw * confidence) + (anchor * (1 - confidence)), 12, 74), 3)

    # Reference columns have Matchup/Test Score slightly above/below kHR.
    matchup = round(clamp(khr + pitcher + scale(iso, .080, .260) * 2.5 - 1.8, 0, 80), 3)
    test_score = round(clamp(khr + scale(brl, 3, 15) * 2.2 - 1.4, 0, 80), 3)
    ceiling = round(clamp(
        18 + scale(max_ev, 95, 113) * 22 + scale(iso, .060, .260) * 18 + scale(brl, 3, 15) * 12 + min(5, near * 1.2),
        10, 99
    ), 3)

    # IMPORTANT: Reference screenshot zone is 0.500 style.
    # Scale to 0.000-0.500ish. Average/unknown fallback lands 0.050-0.180, great spots 0.300-0.500.
    zone_fit = round(clamp(
        (scale(brl, 3, 15) * 0.180) +
        (scale(pulled, 1, 8) * 0.130) +
        (scale(sweet, 25, 45) * 0.080) +
        (0.060 if 12 <= la <= 28 else 0.015) +
        (scale(max_ev, 95, 113) * 0.070),
        0.015, 0.500
    ), 3)

    likely = round(clamp(khr * 0.82, 1, 70), 0)

    fallback_xwoba = round(max(0.250, min(0.450, 0.260 + (ops * 0.12) + (iso * 0.25))), 3) if ab else None
    fallback_xwobacon = round(max(0.280, min(0.500, 0.300 + (slg * 0.18) + (iso * 0.30))), 3) if ab else None

    return {
        "ISO": round(iso, 3) if ab else None,
        "xwOBA": xwoba if xwoba is not None else fallback_xwoba,
        "xwOBAcon": xwobacon if xwobacon is not None else fallback_xwobacon,
        "matchup": matchup,
        "testScore": test_score,
        "ceiling": ceiling,
        "zoneFit": zone_fit,
        "kHR": khr,
        "likely": likely,
    }

def hr_form(stat, profile, source):
    if source == "Statcast cache":
        recent_hr = safe_int(profile.get("recentHR"), 0)
        near = safe_int(profile.get("nearHR"), 0)
        form7 = safe_int(profile.get("formHR7"), 0)
        form_near7 = safe_int(profile.get("formNear7"), 0)
        score = clamp(34 + recent_hr * 7 + near * 3 + form7 * 9 + form_near7 * 4, 18, 82)
        arrow = "↑" if form7 or form_near7 else ("→" if recent_hr or near else "↓")
        return f"{int(score)}% {arrow}"

    ab = safe_float(stat.get("atBats"), 0)
    hr = safe_float(stat.get("homeRuns"), 0)
    rate = hr / ab if ab else 0
    score = clamp(28 + rate * 520 + min(12, hr * .35), 18, 74)
    arrow = "↑" if rate >= .055 else ("→" if rate >= .030 else "↓")
    return f"{int(score)}% {arrow}"

def hitter_row(player, team, opp_pitcher, profiles):
    pid = player.get("playerId")
    stat = hitter_season_stats(int(pid), current_season()) if pid else {}
    cache_hit = bool(pid and int(pid) in profiles)
    profile = profiles.get(int(pid)) if cache_hit else profile_from_season(stat)
    source = "Statcast cache" if cache_hit else "Season fallback"

    opp_hr9 = pitcher_hr9(opp_pitcher.get("id")) if opp_pitcher else None
    scores = calibrated_scores(stat, profile, opp_hr9, cache_hit)

    pa = safe_int(stat.get("plateAppearances"), 0)
    so = safe_int(stat.get("strikeOuts"), 0)
    return {
        "playerId": pid,
        "name": player.get("name", "Unknown"),
        "team": team.get("abbreviation", "MLB"),
        "teamLogo": team_logo(team.get("abbreviation", "MLB")),
        "headshot": player_headshot(pid),
        "pitcher": (opp_pitcher or {}).get("fullName") or (opp_pitcher or {}).get("name") or "TBD",
        "AB": safe_int(stat.get("atBats"), 0),
        "PA": pa,
        "H": safe_int(stat.get("hits"), 0),
        "HR": safe_int(stat.get("homeRuns"), 0),
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
        "nearHR": safe_int(profile.get("nearHR"), 0),
        "maxEV": profile.get("maxEV"),
        "lastHREV": profile.get("lastHREV"),
        "matchup": scores["matchup"],
        "testScore": scores["testScore"],
        "ceiling": scores["ceiling"],
        "zoneFit": scores["zoneFit"],
        "hrForm": hr_form(stat, profile, source),
        "kHR": scores["kHR"],
        "likely": scores["likely"],
        "status": source,
        "cacheHit": cache_hit,
    }

def collect_hitter_rows(game_pk: int):
    ensure_cache_background()
    game = get_game_by_pk(game_pk)
    if not game:
        return []
    away = normalize_team(game["away"])
    home = normalize_team(game["home"])
    away_pitcher = game.get("awayProbablePitcher") or {}
    home_pitcher = game.get("homeProbablePitcher") or {}
    profiles = load_statcast_cache()

    rows = []
    for p in active_roster(int(away.get("id") or 0)):
        rows.append(hitter_row(p, away, home_pitcher, profiles))
    for p in active_roster(int(home.get("id") or 0)):
        rows.append(hitter_row(p, home, away_pitcher, profiles))
    rows.sort(key=lambda r: (-safe_float(r.get("kHR")), r.get("team", ""), r.get("name", "")))
    return rows

@app.get("/")
def root():
    ensure_cache_background()
    return {"status": "ok", "message": "HR API v14 calibrated + debug", "cache": cache_meta()}

@app.get("/games")
@app.get("/api/games")
def games():
    ensure_cache_background()
    d = day_str(0)
    return {"date": d, "games": get_games_raw(d), "cache": cache_meta()}

@app.get("/game/{game_pk}")
@app.get("/api/game/{game_pk}")
def game_detail(game_pk: int):
    hitters = collect_hitter_rows(game_pk)
    cache_hits = sum(1 for h in hitters if h.get("cacheHit"))
    return {
        "gamePk": game_pk,
        "count": len(hitters),
        "cacheHits": cache_hits,
        "cache": cache_meta(),
        "source": "v14 calibrated: Statcast cache if available; fallback if not",
        "hitters": hitters,
    }

@app.get("/api/cache/build")
@app.post("/api/cache/build")
def cache_build(days: int = 30):
    result = build_statcast_cache(days=days)
    return {**result, "cache": cache_meta()}

@app.get("/api/cache/build-background")
@app.post("/api/cache/build-background")
def cache_build_background():
    started = ensure_cache_background()
    return {"ok": True, "started": started, "cache": cache_meta()}

@app.get("/api/cache/status")
def cache_status():
    ensure_cache_background()
    return cache_meta()

@app.get("/api/debug/player/{player_id}")
def debug_player(player_id: int):
    profiles = load_statcast_cache()
    stat = hitter_season_stats(player_id, current_season())
    profile = profiles.get(player_id)
    return {
        "playerId": player_id,
        "cacheHit": player_id in profiles,
        "seasonStats": stat,
        "statcastProfile": profile,
        "cache": cache_meta(),
    }
