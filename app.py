import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any

import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

TIMEZONE = os.getenv("TIMEZONE", "America/Chicago")
TZ = ZoneInfo(TIMEZONE)
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "300"))
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}&hydrate=probablePitcher"
LIVE_FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{gamePk}/feed/live"
BOXSCORE_URL = "https://statsapi.mlb.com/api/v1/game/{gamePk}/boxscore"
TEAM_ROSTER_URL = "https://statsapi.mlb.com/api/v1/teams/{teamId}/roster?rosterType=active"
PEOPLE_URL = "https://statsapi.mlb.com/api/v1/people?personIds={personIds}"
PITCHER_STATS_URL = "https://statsapi.mlb.com/api/v1/people/{personId}/stats?stats=season&group=pitching&season={season}"

session = requests.Session()
_cache: dict[str, tuple[float, Any]] = {}

app = FastAPI(title="MLB HR Dashboard API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if FRONTEND_ORIGIN == "*" else [FRONTEND_ORIGIN],
    allow_credentials=False,
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

PARK_BOOST_BY_HOME_ABBR = {
    "COL": 12, "CIN": 8, "NYY": 7, "PHI": 5, "BOS": 4, "CWS": 4, "BAL": 3,
    "HOU": 3, "AZ": 2, "TEX": 2, "LAD": 1, "ATL": 1, "SF": -6, "SEA": -4,
    "DET": -3, "NYM": -3, "MIA": -2, "SD": -2, "TB": -2,
}


def cached(key: str, fn, ttl: int = CACHE_SECONDS):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = fn()
    _cache[key] = (now, value)
    return value


def get_json(url: str) -> dict:
    r = session.get(url, timeout=25)
    r.raise_for_status()
    return r.json()


def today_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def day_str(days_ago: int) -> str:
    return (datetime.now(TZ) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def season() -> str:
    return datetime.now(TZ).strftime("%Y")


def safe_float(value, default=0.0) -> float:
    try:
        if value in (None, "", "-.--"):
            return default
        return float(value)
    except Exception:
        return default


def team_logo(abbr: str | None) -> str | None:
    slug = TEAM_LOGO_SLUGS.get(abbr or "")
    return f"https://a.espncdn.com/i/teamlogos/mlb/500/{slug}.png" if slug else None


def player_headshot(player_id: int | None) -> str | None:
    return f"https://img.mlbstatic.com/mlb-photos/image/upload/w_180/v1/people/{player_id}/headshot/67/current" if player_id else None


def is_home_run(play: dict) -> bool:
    result = play.get("result", {}) or {}
    event_type = (result.get("eventType") or "").lower()
    text = " ".join(str(result.get(k) or "") for k in ("event", "description")).lower()
    return event_type == "home_run" or "home run" in text or "homers" in text or "grand slam" in text


def get_metrics(play: dict) -> dict | None:
    for event in reversed(play.get("playEvents", []) or []):
        if event.get("hitData"):
            return event["hitData"]
    return None


def is_near_hr(play: dict) -> bool:
    if is_home_run(play):
        return False
    m = get_metrics(play)
    if not m:
        return False
    ev = safe_float(m.get("launchSpeed"))
    la = safe_float(m.get("launchAngle"))
    dist = safe_float(m.get("totalDistance"))
    return ev >= 102 and 22 <= la <= 38 and dist >= 375


def get_games_for_date(date: str) -> list[dict]:
    data = cached(f"schedule:{date}", lambda: get_json(SCHEDULE_URL.format(date=date)), 180)
    games = []
    for block in data.get("dates", []):
        for g in block.get("games", []):
            away = ((g.get("teams") or {}).get("away") or {})
            home = ((g.get("teams") or {}).get("home") or {})
            away_team = away.get("team") or {}
            home_team = home.get("team") or {}
            games.append({
                "gamePk": g.get("gamePk"),
                "gameDate": g.get("gameDate"),
                "status": (g.get("status") or {}).get("detailedState", ""),
                "away": away_team,
                "home": home_team,
                "awayProbablePitcher": away.get("probablePitcher"),
                "homeProbablePitcher": home.get("probablePitcher"),
                "label": f"{away_team.get('abbreviation') or away_team.get('teamName')} @ {home_team.get('abbreviation') or home_team.get('teamName')}",
            })
    return games


def get_active_roster_player_ids(team_id: int) -> list[int]:
    data = cached(f"roster:{team_id}", lambda: get_json(TEAM_ROSTER_URL.format(teamId=team_id)), 1800)
    return [r.get("person", {}).get("id") for r in data.get("roster", []) if r.get("person", {}).get("id")]


def get_people_by_ids(player_ids: list[int]) -> list[dict]:
    people = []
    for i in range(0, len(player_ids), 100):
        ids = ",".join(str(x) for x in player_ids[i:i + 100])
        data = cached(f"people:{ids}", lambda ids=ids: get_json(PEOPLE_URL.format(personIds=ids)), 1800)
        people.extend(data.get("people", []))
    return people


def get_pitcher_hr9(pitcher_id: int | None) -> float | None:
    if not pitcher_id:
        return None
    def load():
        data = get_json(PITCHER_STATS_URL.format(personId=pitcher_id, season=season()))
        splits = ((data.get("stats") or [{}])[0].get("splits") or [])
        if not splits:
            return None
        stat = splits[0].get("stat") or {}
        hr = safe_float(stat.get("homeRuns"))
        ip = safe_float(stat.get("inningsPitched"))
        return round((hr / ip) * 9, 2) if ip > 0 else None
    try:
        return cached(f"pitcherhr9:{pitcher_id}:{season()}", load, 3600)
    except Exception:
        return None


def collect_recent_contact(player_ids: list[int], days: int = 3) -> dict[int, dict]:
    wanted = {p for p in player_ids if p}
    out = {pid: {"near_hr": 0, "max_ev": None, "last_hr_ev": None, "hard_hits": 0, "bip": 0, "sweet_spot": 0, "fly_balls": 0} for pid in wanted}
    if not wanted:
        return out
    for d in range(days, 0, -1):
        for game in get_games_for_date(day_str(d)):
            game_pk = game.get("gamePk")
            if not game_pk:
                continue
            try:
                data = cached(f"feed:{game_pk}", lambda game_pk=game_pk: get_json(LIVE_FEED_URL.format(gamePk=game_pk)), 3600)
                plays = (((data.get("liveData") or {}).get("plays") or {}).get("allPlays") or [])
            except Exception:
                continue
            for play in plays:
                batter_id = (((play.get("matchup") or {}).get("batter") or {}).get("id"))
                if batter_id not in wanted:
                    continue
                row = out.setdefault(batter_id, {"near_hr": 0, "max_ev": None, "last_hr_ev": None, "hard_hits": 0, "bip": 0, "sweet_spot": 0, "fly_balls": 0})
                m = get_metrics(play)
                if not m:
                    continue
                ev = m.get("launchSpeed")
                la = m.get("launchAngle")
                if ev is not None:
                    ev = safe_float(ev)
                    row["max_ev"] = ev if row["max_ev"] is None else max(row["max_ev"], ev)
                    if ev >= 95:
                        row["hard_hits"] += 1
                if la is not None:
                    la = safe_float(la)
                    row["bip"] += 1
                    if 8 <= la <= 32:
                        row["sweet_spot"] += 1
                    if la >= 20:
                        row["fly_balls"] += 1
                if is_near_hr(play):
                    row["near_hr"] += 1
                if is_home_run(play) and ev is not None:
                    row["last_hr_ev"] = ev
    return out


def build_game_dashboard(game_pk: int) -> dict:
    games = get_games_for_date(today_str())
    game = next((g for g in games if int(g.get("gamePk")) == int(game_pk)), None)
    if not game:
        # allows old / direct game ids to still work enough
        game = {"gamePk": game_pk, "label": str(game_pk), "away": {}, "home": {}}

    rows = []
    team_context = []
    for side, opp_side in (("away", "home"), ("home", "away")):
        team = game.get(side) or {}
        opp = game.get(opp_side) or {}
        opp_pitcher = game.get(f"{opp_side}ProbablePitcher") or {}
        team_context.append((team, opp, opp_pitcher))

    people_by_team = []
    all_ids = []
    for team, opp, opp_pitcher in team_context:
        ids = get_active_roster_player_ids(team.get("id")) if team.get("id") else []
        all_ids.extend(ids)
        people_by_team.append((team, opp, opp_pitcher, get_people_by_ids(ids)))

    contact = collect_recent_contact(all_ids, days=3)
    home_abbr = (game.get("home") or {}).get("abbreviation")
    park_boost = PARK_BOOST_BY_HOME_ABBR.get(home_abbr, 0)

    for team, opp, opp_pitcher, people in people_by_team:
        pitcher_hr9 = get_pitcher_hr9(opp_pitcher.get("id"))
        for p in people:
            pid = p.get("id")
            c = contact.get(pid, {})
            max_ev = safe_float(c.get("max_ev"), 0)
            last_hr_ev = safe_float(c.get("last_hr_ev"), 0)
            near_hr = int(c.get("near_hr") or 0)
            bip = max(int(c.get("bip") or 0), 1)
            hard_hit_pct = round((int(c.get("hard_hits") or 0) / bip) * 100, 1)
            sweet_spot_pct = round((int(c.get("sweet_spot") or 0) / bip) * 100, 1)
            fb_pct = round((int(c.get("fly_balls") or 0) / bip) * 100, 1)
            ev_source = max(last_hr_ev, max_ev)
            barrel_proxy = min(100, max(0, (ev_source - 95) * 4 + near_hr * 10))
            matchup = min(100, max(0, 45 + (pitcher_hr9 or 1.0) * 12 + park_boost + near_hr * 4 + (ev_source - 100) * 1.2))
            ceiling = min(100, max(0, 30 + (ev_source - 95) * 4.5 + near_hr * 6 + park_boost))
            zone_fit = min(100, max(0, barrel_proxy * .55 + sweet_spot_pct * .25 + fb_pct * .20))
            form = min(100, max(0, near_hr * 22 + (last_hr_ev - 95) * 2 if last_hr_ev else near_hr * 18 + (max_ev - 100) * 1.5))
            khr = round(matchup * .25 + ceiling * .20 + zone_fit * .20 + form * .15 + hard_hit_pct * .10 + max(0, park_boost) * .5 + ((pitcher_hr9 or 0) * 4), 1)
            if khr < 20 and max_ev <= 0 and near_hr == 0:
                continue
            rows.append({
                "playerId": pid,
                "player": p.get("fullName", "Unknown"),
                "team": team.get("abbreviation") or team.get("teamName"),
                "opponent": opp.get("abbreviation") or opp.get("teamName"),
                "pitcher": opp_pitcher.get("fullName") or "TBD",
                "pitcherHr9": pitcher_hr9,
                "matchup": round(matchup, 1),
                "testScore": round(barrel_proxy, 1),
                "ceiling": round(ceiling, 1),
                "zoneFit": round(zone_fit, 1),
                "hrForm": round(form, 1),
                "khr": khr,
                "maxEV": round(max_ev, 1) if max_ev else None,
                "lastHrEV": round(last_hr_ev, 1) if last_hr_ev else None,
                "nearHR": near_hr,
                "bip": bip if c.get("bip") else 0,
                "hardHitPct": hard_hit_pct if c.get("bip") else None,
                "sweetSpotPct": sweet_spot_pct if c.get("bip") else None,
                "fbPct": fb_pct if c.get("bip") else None,
                "parkBoost": park_boost,
                "headshot": player_headshot(pid),
            })

    rows.sort(key=lambda r: r["khr"], reverse=True)
    return {"game": game, "updatedAt": datetime.now(TZ).isoformat(), "rows": rows[:80]}


@app.get("/health")
def health():
    return {"ok": True, "time": datetime.now(TZ).isoformat()}


@app.get("/games")
def games():
    rows = get_games_for_date(today_str())
    for g in rows:
        g["awayLogo"] = team_logo((g.get("away") or {}).get("abbreviation"))
        g["homeLogo"] = team_logo((g.get("home") or {}).get("abbreviation"))
    return {"date": today_str(), "games": rows}


@app.get("/game/{game_pk}")
def game(game_pk: int):
    return cached(f"dashboard:{game_pk}", lambda: build_game_dashboard(game_pk), CACHE_SECONDS)


@app.get("/top-hr")
def top_hr(limit: int = 25):
    out = []
    for g in get_games_for_date(today_str()):
        try:
            data = cached(f"dashboard:{g['gamePk']}", lambda g=g: build_game_dashboard(g["gamePk"]), CACHE_SECONDS)
            for row in data.get("rows", []):
                row = dict(row)
                row["gamePk"] = g.get("gamePk")
                row["gameLabel"] = g.get("label")
                out.append(row)
        except Exception:
            continue
    out.sort(key=lambda r: r.get("khr", 0), reverse=True)
    return {"date": today_str(), "updatedAt": datetime.now(TZ).isoformat(), "rows": out[:limit]}

# Compatibility routes for dashboard/API testing
@app.get('/')
def root():
    return {
        "ok": True,
        "name": "MLB HR Dashboard API",
        "routes": ["/health", "/games", "/game/{game_pk}", "/top-hr", "/api/games", "/api/game/{game_pk}", "/api/top-hr"],
    }

@app.get('/api/health')
def api_health():
    return health()

@app.get('/api/games')
def api_games():
    return games()

@app.get('/api/game/{game_pk}')
def api_game(game_pk: int):
    return game(game_pk)

@app.get('/api/top-hr')
def api_top_hr(limit: int = 25):
    return top_hr(limit)
