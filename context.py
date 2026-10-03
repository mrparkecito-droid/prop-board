"""Game context the box scores can't see: betting lines, weather, injuries, news and buzz.

Runs after fetch.py and writes data/context.json. engine.py reads it and:
  - gives each prop a small context nudge, at most +/-4% of its score (CTX_WEIGHT in engine.py)
  - writes a short "why" line for each card
  - drops players listed OUT from the board

Sources (all public, no keys; each one is optional - if a source fails the rest still work):
  ESPN scoreboard : point spread, over/under, dome or not, kickoff time
  Open-Meteo      : wind, rain chance and temperature at kickoff for outdoor games
  ESPN injuries   : this week's Out / Doubtful / Questionable list for every team
  ESPN news       : recent headlines that mention a player
  Sleeper         : most-added players in the last 48 hours (fantasy hype)
  Reddit          : how many hot posts on r/fantasyfootball and r/sportsbook mention a player
"""
import datetime as dt, json, os, re, time
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "context.json")
ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
UA = {"User-Agent": "prop-board/1.0 (+github actions)"}

TEAM_FIX = {"JAC": "JAX", "LA": "LAR", "WSH": "WAS", "LVR": "LV", "OAK": "LV", "SD": "LAC", "STL": "LAR"}
T = lambda t: TEAM_FIX.get((t or "").upper(), (t or "").upper())

# Home stadium coordinates (for weather) and covered stadiums (weather doesn't matter there)
STADIUM = {"ARI": (33.528, -112.263), "ATL": (33.755, -84.401), "BAL": (39.278, -76.623), "BUF": (42.774, -78.787),
           "CAR": (35.226, -80.853), "CHI": (41.862, -87.617), "CIN": (39.095, -84.516), "CLE": (41.506, -81.700),
           "DAL": (32.748, -97.093), "DEN": (39.744, -105.020), "DET": (42.340, -83.046), "GB": (44.501, -88.062),
           "HOU": (29.685, -95.411), "IND": (39.760, -86.164), "JAX": (30.324, -81.637), "KC": (39.049, -94.484),
           "LV": (36.091, -115.184), "LAC": (33.953, -118.339), "LAR": (33.953, -118.339), "MIA": (25.958, -80.239),
           "MIN": (44.974, -93.258), "NE": (42.091, -71.264), "NO": (29.951, -90.081), "NYG": (40.813, -74.074),
           "NYJ": (40.813, -74.074), "PHI": (39.901, -75.168), "PIT": (40.447, -80.016), "SF": (37.403, -121.970),
           "SEA": (47.595, -122.332), "TB": (27.976, -82.503), "TEN": (36.166, -86.771), "WAS": (38.908, -76.864)}
COVERED = {"ARI", "ATL", "DAL", "DET", "HOU", "IND", "LV", "LAC", "LAR", "MIN", "NO"}

DB = {"CB", "S", "FS", "SS", "DB", "NB", "SAF"}
FRONT = {"DT", "DE", "NT", "LB", "ILB", "OLB", "MLB", "EDGE", "DL"}


def norm(n):
    n = re.sub(r"[^a-z ]", "", (n or "").lower().replace("-", " "))
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return " ".join(n.split())


def get(url, params=None, tries=3, timeout=20):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=timeout)
            if r.status_code == 429:
                time.sleep(2 + 3 * i); continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if i == tries - 1:
                raise
            time.sleep(2 + 2 * i)


# ------------------------------------------------------------------ parsers (pure, tested)
def parse_scoreboard(j):
    games = {}
    for ev in j.get("events", []):
        c = (ev.get("competitions") or [{}])[0]
        side = {x.get("homeAway"): T((x.get("team") or {}).get("abbreviation")) for x in c.get("competitors", [])}
        home, away = side.get("home"), side.get("away")
        if not home or not away:
            continue
        g = {"home": home, "away": away, "kick": c.get("date") or ev.get("date"),
             "indoor": bool((c.get("venue") or {}).get("indoor")) or home in COVERED,
             "neutral": bool(c.get("neutralSite")), "spread": {}, "total": None}
        o = (c.get("odds") or [None])[0]
        if o:
            g["total"] = o.get("overUnder")
            m = re.match(r"\s*([A-Z]{2,4})\s+([-+]?\d+(?:\.\d+)?)", o.get("details") or "")
            if m:
                fav, pts = T(m.group(1)), abs(float(m.group(2)))
                dog = away if fav == home else home
                g["spread"] = {fav: -pts, dog: pts}
            elif (o.get("details") or "").upper().startswith("EVEN"):
                g["spread"] = {home: 0.0, away: 0.0}
        w = c.get("weather") or ev.get("weather")
        if w:
            g["espn_wx"] = {"desc": w.get("displayValue"), "temp": w.get("temperature")}
        games[away + home] = g
    return games


def parse_weather(j, kick_iso):
    """Average the 3 game hours starting at kickoff."""
    h = j.get("hourly") or {}
    times = h.get("time") or []
    k = dt.datetime.fromisoformat(kick_iso.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    start = k.strftime("%Y-%m-%dT%H:00")
    if start not in times:
        return None
    i = times.index(start)
    sl = lambda key: [v for v in (h.get(key) or [])[i:i + 3] if v is not None]
    wind, gust, pop, rain, temp = sl("wind_speed_10m"), sl("wind_gusts_10m"), sl("precipitation_probability"), sl("precipitation"), sl("temperature_2m")
    if not wind:
        return None
    return {"wind": round(sum(wind) / len(wind)), "gust": round(max(gust)) if gust else None,
            "pop": round(max(pop)) if pop else None, "rain": round(sum(rain), 2) if rain else 0,
            "temp": round(sum(temp) / len(temp)) if temp else None}


def parse_injuries(j, team_names):
    out = {}
    for t in j.get("injuries", []):
        for x in t.get("injuries", []):
            a = x.get("athlete") or {}
            tm = T((a.get("team") or {}).get("abbreviation")) or team_names.get(t.get("displayName"))
            if not tm:
                continue
            st = x.get("status") or ""
            if st not in ("Out", "Doubtful", "Questionable"):
                continue            # IR / PUP players have been gone for a while; past games already reflect it
            out.setdefault(tm, []).append({"name": a.get("displayName") or x.get("displayName"),
                                           "pos": ((a.get("position") or {}).get("abbreviation") or "").upper(), "status": st})
    return out


def parse_news(j, names, now):
    """norm name -> newest headline that mentions the player (last 5 days)."""
    out = {}
    for a in j.get("articles", []):
        pub = a.get("published") or ""
        try:
            age = (now - dt.datetime.fromisoformat(pub.replace("Z", "+00:00"))).days
        except ValueError:
            age = 0
        if age > 5:
            continue
        text = f'{a.get("headline", "")} {a.get("description", "")}'
        tagged = {norm(c.get("description")) for c in a.get("categories", []) if c.get("type") == "athlete"}
        if len(tagged) > 2:
            tagged = set()      # roundup stories tag lots of players; only trust a tag on a story about 1-2 players
        low = norm(a.get("headline", ""))
        for n in names:
            if n in tagged or (len(n.split()) >= 2 and re.search(rf"\b{re.escape(n)}\b", low)):
                if n not in out:
                    out[n] = {"h": a.get("headline", "").strip(), "t": pub}
    return out


def parse_reddit(listings, names):
    cnt = {}
    for j in listings:
        for p in (j.get("data") or {}).get("children", []):
            d = p.get("data") or {}
            low = norm(f'{d.get("title", "")} {d.get("selftext", "")[:1500]}')
            for n in names:
                if re.search(rf"\b{re.escape(n)}\b", low):
                    cnt[n] = cnt.get(n, 0) + 1
    return cnt


# ------------------------------------------------------------------ scoring (pure, tested)
ROLEW = {"QB": "QBs", "RB1": "lead RBs", "RB2": "No. 2 RBs", "WR1": "WR1s", "WR2": "WR2s", "WR3": "WR3s", "TE1": "TE1s", "TE2": "TE2s"}


def assess(r, C, proj_status=None):
    """Context for one prop. Returns (ctx in -1..1, list of (weight, reason), status)."""
    if not C:
        return 0.0, [], None
    team, opp, stat, pos = r["team"], r["opp"], r["stat"], r["pos"]
    passy = stat in ("pass", "rec", "recyds")
    rs = []
    add = lambda w, t: rs.append((round(w, 2), t))
    g = (C.get("games") or {}).get(r["game"])
    if g:
        sp, tot = g.get("spread", {}).get(team), g.get("total")
        if sp is not None and tot:
            itt = tot / 2 - sp / 2
            if itt >= 26.5:
                add(0.25, f"{team} projected for {itt:.0f} pts (O/U {tot:g})")
            elif itt <= 18.5:
                add(-0.25, f"{team} projected for only {itt:.0f} pts (O/U {tot:g})")
        if sp is not None:
            if stat == "rush" and pos == "RB":
                if sp <= -6.5:
                    add(0.3, f"{team} favored by {-sp:g}, should lean on the run with a lead")
                elif sp >= 6.5:
                    add(-0.25, f"{team} a {sp:g}-pt underdog, may have to abandon the run")
            elif passy:
                if sp >= 6.5:
                    add(0.2, f"{team} a {sp:g}-pt underdog, trailing teams throw more")
                elif sp <= -9.5:
                    add(-0.15, f"{team} favored by {-sp:g}, may not need to throw late")
        wx = g.get("wx")
        if wx and not g.get("indoor"):
            if wx["wind"] >= 15:
                if passy:
                    add(-0.3, f"Windy: about {wx['wind']} mph at kickoff")
                elif stat == "rush":
                    add(0.1, f"Windy ({wx['wind']} mph) should mean more runs")
            elif (wx.get("pop") or 0) >= 60 and (wx.get("rain") or 0) >= 1:
                if passy:
                    add(-0.15, f"Rain likely ({wx['pop']}% chance)")
                elif stat == "rush":
                    add(0.05, "Rain likely, should mean more runs")
            if wx.get("temp") is not None and wx["temp"] <= 25 and passy:
                add(-0.1, f"Cold: {wx['temp']}°F at kickoff")
    inj = C.get("inj") or {}
    me = norm(r["player"])
    status = next((x["status"] for x in inj.get(team, []) if norm(x["name"]) == me), None) or proj_status
    if status == "Questionable":
        add(-0.35, "Listed questionable this week")
    elif status in ("Doubtful", "Out"):
        add(-1.0, f"Listed {status.lower()} this week")
    gone = [x for x in inj.get(opp, []) if x["status"] in ("Out", "Doubtful")]
    if passy:
        d = [x for x in gone if x["pos"] in DB]
        if d:
            add(min(0.15 * len(d), 0.35), f"{opp} secondary missing " + ", ".join(f'{x["pos"]} {x["name"]}' for x in d[:3]))
    elif stat == "rush" and pos == "RB":
        d = [x for x in gone if x["pos"] in FRONT]
        if d:
            add(min(0.12 * len(d), 0.3), f"{opp} front missing " + ", ".join(f'{x["pos"]} {x["name"]}' for x in d[:3]))
    mates = [x for x in inj.get(team, []) if x["status"] in ("Out", "Doubtful") and norm(x["name"]) != me]
    if stat in ("rec", "recyds") and pos in ("WR", "TE"):
        m = [x for x in mates if x["pos"] in ("WR", "TE")]
        if m:
            add(0.2, f"{m[0]['name']} out, more targets to go around")
    elif stat == "rush" and pos == "RB":
        m = [x for x in mates if x["pos"] == "RB"]
        if m:
            add(0.3, f"{m[0]['name']} out, more carries for him")
    hype = 0.0
    t = (C.get("trend") or {}).get(me)
    if t and t["rank"] <= 25:
        hype += 0.08; rs.append((0.08, f"Trending on Sleeper (#{t['rank']} most added)"))
    b = (C.get("buzz") or {}).get(me, 0)
    if b >= 3:
        hype += 0.05; rs.append((0.05, f"Buzz on Reddit ({b} hot posts)"))
    n = (C.get("news") or {}).get(me)
    if n:
        rs.append((0.0, f'In the news: "{n["h"]}"'))
    ctx = max(-1.0, min(1.0, sum(w for w, _ in rs)))
    return round(ctx, 2), rs, status


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def why(r, rs):
    """One or two short sentences for the card."""
    bits = [f"Cleared {r['line']}+ in {r['over']} of {r['n']} games" + (f" ({r['po']} of {r['pn']} last season)" if r.get("pn") else "")]
    if r["proj"] >= r["line"]:
        bits.append(f"Sleeper projects {r['proj']:g}")
    if r.get("dn"):
        bits.append(f"{r['opp']} let {r['dhit']} of {r['dn']} {ROLEW.get(r['role'], r['role'] + 's')} reach it")
    if r["most"] <= 10:
        bits.append(f"{r['opp']} allows the {ordinal(r['most']) + ' ' if r['most'] > 1 else ''}most {r['dstat']}")
    s = ", ".join(bits) + "."
    u = r.get("uinfo") or {}
    if u.get("note"):
        s += " " + u["note"]
    good = [t for w, t in sorted(rs, key=lambda x: -x[0]) if w > 0][:2]
    bad = [t for w, t in sorted(rs, key=lambda x: x[0]) if w < 0][:2]
    news = [t for w, t in rs if w == 0][:1]
    if good:
        s += " " + ". ".join(good) + "."
    if bad:
        s += " Watch: " + "; ".join(bad) + "."
    return s, (news[0] if news else "")


# ------------------------------------------------------------------ pull
def main():
    data = json.load(open(os.path.join(HERE, "data", "data.json")))
    now = dt.datetime.now(dt.timezone.utc)
    C = {"generated": now.isoformat(timespec="seconds"), "games": {}, "inj": {}, "news": {}, "trend": {}, "buzz": {}, "sources": {}}
    names = {norm(m["name"]) for m in data["markets"]}
    from fetch import PFR_NAMES

    try:
        sb = get(f"{ESPN}/scoreboard", params={"seasontype": 2, "week": data["week"], "dates": data["season"]})
        C["games"] = {k: v for k, v in parse_scoreboard(sb).items() if k in {g["key"] for g in data["games"]}}
        C["sources"]["lines"] = f"{sum(1 for g in C['games'].values() if g['spread'])} games"
    except Exception as e:
        print("ESPN scoreboard failed:", e); C["sources"]["lines"] = "failed"

    nwx = 0
    for k, g in C["games"].items():
        if g["indoor"] or g["neutral"] or not g.get("kick") or g["home"] not in STADIUM:
            continue
        lat, lon = STADIUM[g["home"]]
        day = g["kick"][:10]
        try:
            j = get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": lat, "longitude": lon, "hourly": "temperature_2m,precipitation_probability,precipitation,wind_speed_10m,wind_gusts_10m",
                "wind_speed_unit": "mph", "temperature_unit": "fahrenheit", "precipitation_unit": "mm", "timezone": "UTC",
                "start_date": day, "end_date": (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()})
            g["wx"] = parse_weather(j, g["kick"])
            nwx += bool(g["wx"])
        except Exception as e:
            print("weather failed for", k, e)
    C["sources"]["weather"] = f"{nwx} outdoor games"

    try:
        C["inj"] = parse_injuries(get(f"{ESPN}/injuries"), PFR_NAMES)
        C["sources"]["injuries"] = f"{sum(len(v) for v in C['inj'].values())} players listed"
    except Exception as e:
        print("ESPN injuries failed:", e); C["sources"]["injuries"] = "failed"

    try:
        C["news"] = parse_news(get(f"{ESPN}/news", params={"limit": 150}), names, now)
        C["sources"]["news"] = f"{len(C['news'])} players in headlines"
    except Exception as e:
        print("ESPN news failed:", e); C["sources"]["news"] = "failed"

    try:
        tr = get("https://api.sleeper.app/v1/players/nfl/trending/add", params={"lookback_hours": 48, "limit": 50})
        ids = {str(p.get("id")): norm(p["name"]) for p in data["proj"] if p.get("id")}
        for i, x in enumerate(tr, 1):
            n = ids.get(str(x.get("player_id")))
            if n:
                C["trend"][n] = {"rank": i, "adds": x.get("count")}
        C["sources"]["sleeper_trending"] = f"{len(C['trend'])} board players trending"
    except Exception as e:
        print("Sleeper trending failed:", e); C["sources"]["sleeper_trending"] = "failed"

    listings = []
    for sub in ("fantasyfootball", "sportsbook"):
        try:
            listings.append(get(f"https://www.reddit.com/r/{sub}/hot.json", params={"limit": 100}, tries=1))
        except Exception as e:
            print(f"Reddit r/{sub} unavailable:", e)
    C["buzz"] = parse_reddit(listings, names)
    C["sources"]["reddit"] = f"{len(listings)} of 2 subreddits" if listings else "unavailable"

    json.dump(C, open(OUT, "w"), indent=1)
    print("context:", C["sources"])


if __name__ == "__main__":
    main()
