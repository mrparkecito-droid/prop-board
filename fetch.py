"""Pulls everything the model needs straight from public APIs and writes data/data.json.

  Kalshi   : open player-prop ladders (passing, rushing, receiving yards, receptions)
  Sleeper  : weekly box-score stats for completed weeks + this week's projections (Rotowire)
  PFR      : team defense (net pass yds and rush yds allowed per game) for the league ranks
             (falls back to totals computed from Sleeper stats if PFR blocks the request)

No API keys needed. Override the week with WEEK=5 python fetch.py
"""
import datetime as dt, io, json, os, re, sys, time
from zoneinfo import ZoneInfo
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
PT = ZoneInfo("America/Los_Angeles")
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
SLEEPER = "https://api.sleeper.app"
SERIES = {"KXNFLPASSYDS": "pass", "KXNFLRSHYDS": "rush", "KXNFLRECYDS": "recyds", "KXNFLREC": "rec"}
SERIES_WORD = {"pass": "passing", "rush": "rushing", "recyds": "receiving yards", "rec": "receptions"}
POSITIONS = ("QB", "RB", "WR", "TE")
# Kalshi / PFR team codes -> Sleeper codes
TEAM_FIX = {"JAC": "JAX", "LA": "LAR", "WSH": "WAS", "LVR": "LV", "OAK": "LV", "SD": "LAC", "STL": "LAR"}
T = lambda t: TEAM_FIX.get((t or "").upper(), (t or "").upper())

S = requests.Session()
S.headers["User-Agent"] = "prop-board/1.0 (+github actions)"


def get(url, params=None, tries=4):
    for i in range(tries):
        try:
            r = S.get(url, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(2 + 3 * i); continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if i == tries - 1:
                raise
            time.sleep(2 + 3 * i)


# ---------------------------------------------------------------- Sleeper
def sleeper_state():
    return get(f"{SLEEPER}/v1/state/nfl").json()


def sleeper_rows(kind, season, week):
    """kind = stats | projections. Returns list of dicts with name/pos/team/opp/stats."""
    params = [("season_type", "regular")] + [("position[]", p) for p in POSITIONS]
    data = get(f"{SLEEPER}/{kind}/nfl/{season}/{week}", params=params).json()
    out = []
    for e in data:
        p = e.get("player") or {}
        pos = (p.get("position") or "").upper()
        if pos == "FB":
            pos = "RB"
        if pos not in POSITIONS:
            continue
        name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
        st = e.get("stats") or {}
        out.append({"name": name, "pos": pos, "team": T(e.get("team") or p.get("team")), "opp": T(e.get("opponent")), "stats": st,
                    "id": e.get("player_id"), "inj": p.get("injury_status")})
    return out


def played(pos, st):
    if pos == "QB":
        return bool(st.get("pass_att") or st.get("rush_att") or st.get("pass_yd") or st.get("rush_yd"))
    keys = ("gp", "off_snp", "rec_tgt", "rec", "rush_att", "rec_yd", "rush_yd")
    return any(st.get(k) for k in keys)


# ---------------------------------------------------------------- Kalshi
def kalshi_paged(path, params, key):
    out, cursor = [], None
    while True:
        q = dict(params)
        if cursor:
            q["cursor"] = cursor
        j = get(f"{KALSHI}{path}", params=q).json()
        out += j.get(key, [])
        cursor = j.get("cursor")
        if not cursor or not j.get(key):
            return out


def cents(m, field):
    v = m.get(field)
    if isinstance(v, (int, float)) and v is not None:
        return int(round(v))
    d = m.get(field + "_dollars")
    if d not in (None, ""):
        return int(round(float(d) * 100))
    return None


MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def event_date(ticker):
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", ticker)
    if not m:
        return None
    return dt.date(2000 + int(m.group(1)), MONTHS[m.group(2)], int(m.group(3)))


def kalshi_markets(today):
    games, markets = {}, []
    # only this NFL week: Tuesday through the following Monday (on a Monday, that's today = Monday night football)
    week_start = today - dt.timedelta(days=(today.weekday() - 1) % 7)
    horizon = week_start + dt.timedelta(days=6)
    for series, stat in SERIES.items():
        events = kalshi_paged("/events", {"series_ticker": series, "status": "open", "limit": 200}, "events")
        for ev in events:
            d = event_date(ev["event_ticker"])
            if not d or d < today or d > horizon:
                continue
            m = re.match(r"\s*([A-Z]{2,4})\s+(?:vs|at|@)\s+([A-Z]{2,4})", ev.get("sub_title") or "")
            if not m:
                continue
            away, home = T(m.group(1)), T(m.group(2))
            key = f"{away}{home}"
            day = d.strftime("%a")
            label = f"{away} @ {home}" + ("" if day == "Sun" else f" ({day})")
            games[key] = {"key": key, "away": away, "home": home, "label": label, "date": d.isoformat()}
            for mk in kalshi_paged("/markets", {"event_ticker": ev["event_ticker"], "status": "open", "limit": 1000}, "markets"):
                sub = mk.get("yes_sub_title") or mk.get("subtitle") or ""
                mm = re.match(r"(.+?):\s*(\d+)\+", sub)
                if not mm:
                    continue
                ask = cents(mk, "yes_ask"); bid = cents(mk, "yes_bid")
                markets.append({"game": key, "series": stat, "name": mm.group(1).strip(), "line": int(mm.group(2)),
                                "ask": ask or 0, "bid": bid, "ticker": mk.get("ticker"), "event": ev["event_ticker"]})
    return list(games.values()), markets


# ---------------------------------------------------------------- PFR team defense
PFR_NAMES = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL", "Buffalo Bills": "BUF", "Carolina Panthers": "CAR",
    "Chicago Bears": "CHI", "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE", "Dallas Cowboys": "DAL", "Denver Broncos": "DEN",
    "Detroit Lions": "DET", "Green Bay Packers": "GB", "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC", "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC", "Los Angeles Rams": "LAR", "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN", "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG", "New York Jets": "NYJ",
    "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT", "San Francisco 49ers": "SF", "Seattle Seahawks": "SEA",
    "Tampa Bay Buccaneers": "TB", "Tennessee Titans": "TEN", "Washington Commanders": "WAS"}


def pfr_league(season):
    import pandas as pd
    html = get(f"https://www.pro-football-reference.com/years/{season}/opp.htm").text
    t = pd.read_html(io.StringIO(html), attrs={"id": "team_stats"})[0]
    cols = {}
    for c in t.columns:
        top, sub = (c if isinstance(c, tuple) else ("", c))
        if sub == "Tm": cols["tm"] = c
        elif sub == "G": cols["g"] = c
        elif top == "Passing" and sub == "Yds": cols["pass"] = c
        elif top == "Rushing" and sub == "Yds": cols["rush"] = c
    out = {}
    for _, r in t.iterrows():
        ab = PFR_NAMES.get(str(r[cols["tm"]]))
        if ab:
            out[ab] = [int(r[cols["g"]]), int(r[cols["pass"]]), int(r[cols["rush"]])]
    if len(out) < 32:
        raise ValueError(f"PFR table had {len(out)} teams")
    return out


def sleeper_league(stats):
    """Fallback: yards allowed by each defense from Sleeper box scores (gross passing yards)."""
    agg = {}
    for s in stats:
        d = agg.setdefault(s["opp"], {"wks": set(), "pass": 0.0, "rush": 0.0})
        d["wks"].add(s["wk"]); d["pass"] += s["pass_yd"]; d["rush"] += s["rush_yd"]
    return {t: [len(v["wks"]), round(v["pass"]), round(v["rush"])] for t, v in agg.items() if t}


def stat_row(wk, r):
    st = r["stats"]
    g = lambda k: st.get(k, 0) or 0
    return {"wk": wk, "name": r["name"], "pos": r["pos"], "team": r["team"], "opp": r["opp"],
            "pass_yd": g("pass_yd"), "rush_yd": g("rush_yd"), "rec": g("rec"), "rec_yd": g("rec_yd"),
            # usage: targets, carries, pass attempts, offensive snaps and the team's offensive snaps
            "tgt": g("rec_tgt"), "att": g("rush_att"), "patt": g("pass_att"), "snp": g("off_snp"), "tsnp": g("tm_off_snp")}


def prev_season(season):
    """Last regular season's weekly box scores. Fetched once and kept in data/prev_season.json."""
    path = os.path.join(HERE, "data", "prev_season.json")
    try:
        old = json.load(open(path))
        if old.get("season") == season - 1 and len(old.get("weeks", [])) >= 18 and old.get("v") == 1:
            return old["stats"]
    except (OSError, ValueError):
        pass
    rows, weeks = [], []
    for wk in range(1, 19):
        try:
            for r in sleeper_rows("stats", season - 1, wk):
                if played(r["pos"], r["stats"]):
                    rows.append(stat_row(wk, r))
            weeks.append(wk)
        except Exception as e:
            print(f"last season week {wk} unavailable:", e)
    if len(weeks) >= 18:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump({"v": 1, "season": season - 1, "weeks": weeks, "stats": rows}, open(path, "w"), separators=(",", ":"))
    print(f"last season: {len(rows)} stat lines from {len(weeks)} weeks")
    return rows


# ---------------------------------------------------------------- main
def main():
    now = dt.datetime.now(PT)
    state = sleeper_state()
    season = int(os.environ.get("SEASON") or state.get("season") or now.year)
    week = int(os.environ.get("WEEK") or state.get("week") or 1)
    completed = list(range(1, week))
    print(f"season {season}, scoring week {week}, history weeks {completed}")

    stats = []
    for wk in completed:
        for r in sleeper_rows("stats", season, wk):
            st = r["stats"]
            if not played(r["pos"], st):
                continue
            stats.append(stat_row(wk, r))
    print("stat lines:", len(stats))
    try:
        prev_season(season)          # cached in data/prev_season.json; engine.py reads it
    except Exception as e:
        print("last season unavailable:", e)

    proj = []
    for r in sleeper_rows("projections", season, week):
        st = r["stats"]
        p = {"name": r["name"], "pos": r["pos"], "team": r["team"], "opp": r["opp"], "id": r["id"], "inj": r["inj"]}
        if r["pos"] == "QB":
            p["pass"] = st.get("pass_yd"); p["rush"] = st.get("rush_yd")
        else:
            p["rush"] = st.get("rush_yd"); p["rec"] = st.get("rec"); p["recyds"] = st.get("rec_yd")
        if any(v for k, v in p.items() if k in ("pass", "rush", "rec", "recyds")):
            proj.append(p)
    print("projections:", len(proj))

    games, markets = kalshi_markets(now.date())
    print("games:", len(games), "markets:", len(markets))

    try:
        league = pfr_league(season); league_source = "Pro Football Reference"
    except Exception as e:
        print("PFR unavailable, using Sleeper totals:", e)
        league = sleeper_league(stats); league_source = "Sleeper box-score totals (PFR was unavailable)"

    notes = []
    for g in sorted(games, key=lambda g: (g["date"], g["label"])):
        have = {m["series"] for m in markets if m["game"] == g["key"] and m["ask"] > 0}
        missing = [SERIES_WORD[s] for s in ("pass", "rush", "recyds", "rec") if s not in have]
        if missing:
            notes.append(f"{g['label']}: no priced {', '.join(missing)} markets yet.")
    if notes:
        notes = ["Some markets weren't open when this was pulled. " + " ".join(notes)]

    dates = sorted({g["date"] for g in games})
    if dates:
        d0, d1 = dt.date.fromisoformat(dates[0]), dt.date.fromisoformat(dates[-1])
        span = d0.strftime("%a %b %-d") + ("" if d0 == d1 else " – " + d1.strftime("%a %b %-d"))
        eyebrow = f"Prop cheat sheet · Week {week} · {span}"
    else:
        eyebrow = f"Prop cheat sheet · Week {week}"

    data = {"season": season, "week": week, "completed_weeks": completed, "generated": now.isoformat(timespec="seconds"),
            "league_source": league_source, "eyebrow": eyebrow, "notes": notes,
            "games": sorted(games, key=lambda g: (g["date"], g["label"])), "markets": markets,
            "stats": stats, "proj": proj, "league": league}
    os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
    json.dump(data, open(os.path.join(HERE, "data", "data.json"), "w"))
    if not games or not markets:
        print("WARNING: no open Kalshi markets found; the page will be empty this run.")


if __name__ == "__main__":
    main()
