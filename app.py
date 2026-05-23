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

def today():
    return datetime.now(TZ)

def current_season():
    return today().year

def date_str(dt):
    return dt.strftime("%Y-%m-%d")

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

def get_games_raw(date):
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}&hydrate=probablePitcher"
    data = get_json(url)
    out = []
    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            away_block = ((game.get("teams") or {}).get("away") or {})
            home_block = ((game.get("teams") or {}).get("home") or {})
            away = normalize_team(away_block.get("team", {}) or {})
            home = normalize_team(home_block.get("team", {}) or {})
            away_abbr = away["abbreviation"]
            home_abbr = home["abbreviation"]
            out.append({
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
            })
    return out

@lru_cache(maxsize=256)
def active_roster(team_id: int):
    data = get_json(f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster?rosterType=active")
    rows = []
    for item in data.get("roster", []) or []:
        person = item.get("person") or {}
        pos = (item.get("position") or {}).get("abbreviation", "")
        if pos == "P":
            continue
        rows.append({
            "playerId": person.get("id"),
            "name": person.get("fullName", "Unknown"),
            "position": pos,
        })
    return rows

@lru_cache(maxsize=2048)
def hitter_season_stats(player_id: int, season: int):
    """
    Season hitting stats from MLB StatsAPI. This is the stable pregame profile.
    """
    url = f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats?stats=season&group=hitting&season={season}"
    try:
        data = get_json(url)
        splits = (data.get("stats") or [{}])[0].get("splits") or []
        if not splits:
            return {}
        return splits[0].get("stat") or {}
    except Exception:
        return {}

@lru_cache(maxsize=2048)
def pitcher_season_stats(player_id: int, season: int):
    url = f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats?stats=season&group=pitching&season={season}"
    try:
        data = get_json(url)
        splits = (data.get("stats") or [{}])[0].get("splits") or []
        if not splits:
            return {}
        return splits[0].get("stat") or {}
    except Exception:
        return {}

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

@lru_cache(maxsize=2048)
def recent_contact_profile(player_id: int, end_date: str, days: int = 14):
    """
    Looks back across completed games before today and builds recent HR/contact profile.
    Uses prior data only, never today's live/finished result.
    """
    end = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=TZ)
    near_hr = 0
    hr = 0
    bip = 0
    hard_hit = 0
    fly = 0
    sweet = 0
    barrels = 0
    pulled_barrels = 0
    max_ev = None
    last_hr_ev = None
    la_values = []

    for i in range(1, days + 1):
        d = date_str(end - timedelta(days=i))
        try:
            games = get_games_raw(d)
        except Exception:
            continue
        for g in games:
            game_pk = g.get("gamePk")
            if not game_pk:
                continue
            try:
                live = get_json(f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live")
                plays = (((live.get("liveData") or {}).get("plays") or {}).get("allPlays") or [])
            except Exception:
                continue

            for play in plays:
                batter_id = (((play.get("matchup") or {}).get("batter") or {}).get("id"))
                if batter_id != player_id:
                    continue

                metrics = get_metrics(play)
                if metrics:
                    ev = safe_float(metrics.get("launchSpeed"), 0)
                    la = safe_float(metrics.get("launchAngle"), 0)
                    dist = safe_float(metrics.get("totalDistance"), 0)
                    if ev > 0:
                        bip += 1
                        max_ev = ev if max_ev is None else max(max_ev, ev)
                    if ev >= 95:
                        hard_hit += 1
                    if la >= 15:
                        fly += 1
                    if 8 <= la <= 32:
                        sweet += 1
                    if ev >= 98 and 8 <= la <= 32:
                        barrels += 1
                    if ev >= 98 and 15 <= la <= 32:
                        pulled_barrels += 1
                    if la:
                        la_values.append(la)
                    if ev >= 102 and 22 <= la <= 38 and dist >= 375 and not is_home_run(play):
                        near_hr += 1

                if is_home_run(play):
                    hr += 1
                    if metrics and metrics.get("launchSpeed") is not None:
                        last_hr_ev = safe_float(metrics.get("launchSpeed"), None)

    def pct(n, d):
        return round((n / d) * 100, 1) if d else None

    return {
        "recentHR": hr,
        "nearHR": near_hr,
        "maxEV": max_ev,
        "lastHREV": last_hr_ev,
        "BIP": bip,
        "HH": pct(hard_hit, bip),
        "FB": pct(fly, bip),
        "sweetSpot": pct(sweet, bip),
        "brlBip": pct(barrels, bip),
        "pulledBrl": pct(pulled_barrels, bip),
        "LA": round(sum(la_values) / len(la_values), 1) if la_values else None,
    }

def pitcher_hr9(pitcher_id):
    if not pitcher_id:
        return None
    stat = pitcher_season_stats(int(pitcher_id), current_season())
    hr = safe_float(stat.get("homeRuns"), 0)
    ip = safe_float(stat.get("inningsPitched"), 0)
    return round((hr / ip) * 9, 2) if ip > 0 else None

def score_hitter(season_stat, recent, opp_pitcher_hr9):
    ab = safe_float(season_stat.get("atBats"), 0)
    hr = safe_float(season_stat.get("homeRuns"), 0)
    doubles = safe_float(season_stat.get("doubles"), 0)
    triples = safe_float(season_stat.get("triples"), 0)
    hits = safe_float(season_stat.get("hits"), 0)
    tb = safe_float(season_stat.get("totalBases"), hits + doubles + 2 * triples + 3 * hr)
    iso = ((tb / ab) - (hits / ab)) if ab else 0
    slg = safe_float(season_stat.get("slg"), 0)
    ops = safe_float(season_stat.get("ops"), 0)

    max_ev = safe_float(recent.get("maxEV"), 0)
    last_hr_ev = safe_float(recent.get("lastHREV"), 0)
    hh = safe_float(recent.get("HH"), 0)
    fb = safe_float(recent.get("FB"), 0)
    brl = safe_float(recent.get("brlBip"), 0)
    pull_brl = safe_float(recent.get("pulledBrl"), 0)
    sweet = safe_float(recent.get("sweetSpot"), 0)
    la = safe_float(recent.get("LA"), 0)
    near = safe_float(recent.get("nearHR"), 0)
    recent_hr = safe_float(recent.get("recentHR"), 0)
    p_hr9 = safe_float(opp_pitcher_hr9, 1.0)

    # 0-100 style modeled score using historical/pre-game data.
    score = 18
    score += min(24, iso * 90)
    score += min(12, hr * 0.7)
    score += min(10, recent_hr * 2.5)
    score += min(10, near * 2)
    score += min(10, max(0, max_ev - 98) * 0.9)
    score += min(8, max(0, last_hr_ev - 98) * 0.7)
    score += min(8, brl * 0.55)
    score += min(6, pull_brl * 0.55)
    score += min(6, hh * 0.10)
    score += min(5, fb * 0.10)
    score += min(4, sweet * 0.08)
    if 13 <= la <= 28:
        score += 4
    score += min(6, max(0, p_hr9 - 0.8) * 5)

    khr = round(max(0, min(100, score)), 3)
    return {
        "ISO": round(iso, 3) if ab else None,
        "xwOBA": round(max(0.250, min(0.450, 0.260 + (ops * 0.12) + (iso * 0.25))), 3) if ab else None,
        "xwOBAcon": round(max(0.280, min(0.500, 0.300 + (slg * 0.18) + (iso * 0.30))), 3) if ab else None,
        "matchup": round(max(0, min(100, khr + (p_hr9 or 1.0) * 1.5 - 3)), 3),
        "testScore": round(khr, 3),
        "ceiling": round(max(0, min(100, khr + min(12, (max_ev - 100) if max_ev else 0) + min(8, near * 2))), 3),
        "zoneFit": round(max(0.030, min(0.120, 0.045 + (brl * 0.002) + (sweet * 0.0005) + (0.010 if 13 <= la <= 28 else 0))), 3),
        "kHR": khr,
        "likely": round(max(1, min(99, khr * 0.72)), 0),
    }

def hitter_row(player, team, opp_pitcher):
    pid = player.get("playerId")
    season_stat = hitter_season_stats(int(pid), current_season()) if pid else {}
    recent = recent_contact_profile(int(pid), date_str(today()), 14) if pid else {}
    opp_hr9 = pitcher_hr9(opp_pitcher.get("id")) if opp_pitcher else None
    scores = score_hitter(season_stat, recent, opp_hr9)

    ab = safe_int(season_stat.get("atBats"), 0)
    hr = safe_int(season_stat.get("homeRuns"), 0)
    so = safe_int(season_stat.get("strikeOuts"), 0)

    return {
        "playerId": pid,
        "name": player.get("name", "Unknown"),
        "team": team.get("abbreviation", "MLB"),
        "teamLogo": team_logo(team.get("abbreviation", "MLB")),
        "headshot": player_headshot(pid),
        "pitcher": (opp_pitcher or {}).get("fullName") or (opp_pitcher or {}).get("name") or "TBD",

        # Historical season/sample stats.
        "AB": ab,
        "PA": safe_int(season_stat.get("plateAppearances"), 0),
        "H": safe_int(season_stat.get("hits"), 0),
        "HR": hr,
        "RBI": safe_int(season_stat.get("rbi"), 0),
        "BB": safe_int(season_stat.get("baseOnBalls"), 0),
        "SO": so,

        # Pregame profile data only: recent past + season.
        "pitches": safe_int(season_stat.get("numberOfPitches"), 0),
        "BIP": recent.get("BIP") or max(0, ab - so),
        "ISO": scores["ISO"],
        "xwOBA": scores["xwOBA"],
        "xwOBAcon": scores["xwOBAcon"],
        "swStr": round((so / safe_float(season_stat.get("plateAppearances"), 1)) * 100, 1) if safe_float(season_stat.get("plateAppearances"), 0) else None,
        "pulledBrl": recent.get("pulledBrl"),
        "brlBip": recent.get("brlBip"),
        "sweetSpot": recent.get("sweetSpot"),
        "FB": recent.get("FB"),
        "HH": recent.get("HH"),
        "LA": recent.get("LA"),
        "nearHR": recent.get("nearHR"),
        "maxEV": recent.get("maxEV"),
        "lastHREV": recent.get("lastHREV"),

        "matchup": scores["matchup"],
        "testScore": scores["testScore"],
        "ceiling": scores["ceiling"],
        "zoneFit": scores["zoneFit"],
        "hrForm": f"{recent.get('recentHR', 0)} HR / {recent.get('nearHR', 0)} near",
        "kHR": scores["kHR"],
        "likely": scores["likely"],
        "status": "Pregame historical profile",
    }

def collect_hitter_rows(game_pk: int):
    game_date = date_str(today())
    games_today = get_games_raw(game_date)
    game = next((g for g in games_today if int(g["gamePk"]) == int(game_pk)), None)
    if not game:
        return []

    away = normalize_team(game["away"])
    home = normalize_team(game["home"])
    away_pitcher = game.get("awayProbablePitcher") or {}
    home_pitcher = game.get("homeProbablePitcher") or {}

    rows = []
    for p in active_roster(int(away["id"])):
        rows.append(hitter_row(p, away, home_pitcher))
    for p in active_roster(int(home["id"])):
        rows.append(hitter_row(p, home, away_pitcher))

    rows.sort(key=lambda r: (-safe_float(r.get("kHR")), r.get("team", ""), r.get("name", "")))
    return rows

@app.get("/")
def root():
    return {"status": "ok", "message": "HR API running. Uses historical pregame profiles only."}

@app.get("/games")
@app.get("/api/games")
def games():
    d = date_str(today())
    return {"date": d, "games": get_games_raw(d)}

@app.get("/game/{game_pk}")
@app.get("/api/game/{game_pk}")
def game_detail(game_pk: int):
    return {"gamePk": game_pk, "hitters": collect_hitter_rows(game_pk)}
