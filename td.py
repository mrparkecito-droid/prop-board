"""Anytime-touchdown model built the way Jake Venom builds his weekly "NFL Touchdown Watch" picks.

What he looks at (from his Week 5 video and the Venom app screens in it), and how it's done here:

  1. DEFENSE VS THE POSITION. "The Colts have allowed four running back touchdowns already, bottom ten. Only 15
     last season." TDs this defense has allowed to RBs / WRs / TEs this season (rank), last season (total and
     rank), and whether it's an "active leak" (gave one up to that position in each of its last 2 games).
  2. HIS ROLE ("Venom metrics"). Red-zone role (his share of the team's red-zone looks), target share, TD rate,
     touches per game. Sleeper's red-zone counts are used when they exist; otherwise his share of the team's TDs
     stands in for red-zone role.
  3. GAME SCRIPT. Spread, total and implied points ("Venom shout out since we have a high total here"; he
     complains about low-total filler games). Favored teams score more.
  4. DUE / TD DEBT. "He's due for a touchdown with 1.1 touchdown debt": expected TDs from his role minus the TDs
     he actually scored.
  5. COVERAGE SHELL (his yards per target vs single-high / two-high safeties). Not in any free data feed, so
     it's left out.

The chance of scoring uses those same pieces:
    team TDs expected  = implied points / 7 x 0.88 (some points are field goals)
    his share          = blend of red-zone share, share of team TDs (this season + last season at half weight)
                         and share of team touches, pulled toward his role's average
    defense factor     = TDs this defense allows to his position per game vs the league (this season, last
                         season at half weight, shrunk), softened
    lambda = team TDs x his share x defense factor;  chance of 1+ TD = 1 - e^(-lambda)
    then 50/50 with Kalshi's anytime-TD price when Kalshi lists him.

Cheat codes (tags, like the app): Elite red zone role, RZ share top-10, High volume, Target monster,
Soft TD defense, Active leak, Shootout script, Favored, Due. Venom score (0-100) ranks players on the same
signals; the % is the chance.

Lessons from his Week 5 misses (St. Brown, Olave, Tre Tucker, Chase Brown, Dalton Schultz, Tuten): every one
was either on an offense with a low implied total, or a pass-catcher on a team whose red-zone work goes to
someone else. So a leg needs a team implied total of 21+ to go in a parlay, and red-zone role counts more
than the matchup.

Jake's own picks (td_watch.txt or the paste box) get a ranking bonus, never a higher chance.
"""
import datetime as dt
import math
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
WATCH = os.path.join(HERE, "td_watch.txt")

SHARE_PRIOR = {"RB1": 0.20, "RB2": 0.07, "WR1": 0.15, "WR2": 0.10, "WR3": 0.05, "TE1": 0.10, "TE2": 0.04, "QB": 0.07}
SHARE_K = 3.0           # pull his share of team TDs toward his role's average, as if 3 extra team TDs were split at that rate
PREV_W = 0.5            # last season counts half
TD_PER_PT = 0.88 / 7    # team TDs per implied point
TEAM_TD_DEFAULT = 2.4
DEF_K = 3.0             # defense TDs allowed per position: shrink as if 3 average games
DEF_POW = 0.7
RZ_TGT_TD, RZ_ATT_TD, OTHER_TD = 0.20, 0.13, 0.015    # expected TDs per red-zone target / carry / other touch
MKT_W = 0.5
JAKE_BONUS = 0.06       # ranking bonus for one of Jake's own picks
MIN_SAFE = 0.40         # Safest TD legs need 40%+
MIN_LEG = 0.33          # Cheat Code TD legs need 33%+
MIN_PTS = 21.0          # ...and every parlay leg's team must be projected for 21+ points (his misses were low totals)
ROLES = ("RB1", "RB2", "WR1", "WR2", "WR3", "TE1", "TE2", "QB")
KEYWORDS = [("sleeper", "sleeper"), ("red zone|redzone|goal line|goal-line|inside the 10|inside the 5", "red-zone role"),
            ("bad|weak|worst|soft|bottom|leaky|struggl|cooked|slaughter", "soft defense"),
            ("volume|targets|touches|workhorse|bell", "volume"), ("due|debt|regress", "due"),
            ("revenge", "revenge game"), ("lock|hammer|best bet|love", "high confidence"), ("value|plus money|longshot|odds", "value")]
POSW = {"RB": "RB", "WR": "WR", "TE": "TE", "QB": "QB"}


def norm(n):
    """Same as engine.norm."""
    n = (n or "").replace(".", "").replace("'", "").replace("’", "").lower()
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


def _ord(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


# ------------------------------------------------------------------ Jake's list
def read_watch(week, path=WATCH):
    """[(name, reason)] from td_watch.txt. '## Week 6' headers pick the section for this week; lines starting
    with # are comments. Without headers, every line counts."""
    if not os.path.exists(path):
        return []
    sections, cur = {}, None
    for ln in open(path, encoding="utf-8").read().splitlines():
        m = re.match(r"\s*#+\s*week\s*(\d+)", ln, re.I)
        if m:
            cur = int(m.group(1)); sections.setdefault(cur, []); continue
        if not ln.strip() or ln.strip().startswith("#"):
            continue
        sections.setdefault(cur, []).append(ln.strip())
    use = sections.get(week) if any(k is not None for k in sections) else sections.get(None)
    out = []
    for ln in use or []:
        ln = re.sub(r"^[\-\*•\d\.\)\s✅🟢]+", "", ln)
        m = re.split(r"\s+[-–—|]\s+|:\s+|\s+\(", ln, maxsplit=1)
        if m[0].strip():
            out.append((m[0].strip(), (m[1] if len(m) > 1 else "").strip().rstrip(")")))
    return out


def tags_for(reason):
    r = (reason or "").lower()
    return [t for pat, t in KEYWORDS if re.search(pat, r)]


def match_names(picks, players):
    """Map typed names to player keys: exact, then unique last name (+ first initial when given)."""
    by_key = {p["key"]: p for p in players}
    out = {}
    for name, why in picks:
        k = norm(name)
        if k in by_key:
            out[k] = why; continue
        parts = k.split()
        if not parts:
            continue
        cands = [p for p in players if p["key"].split()[-1:] == parts[-1:]]
        if len(parts) > 1:
            cands = [p for p in cands if p["key"][0] == parts[0][0]]      # "T. Warren" must not match Jaylen Warren
        if len(cands) == 1:
            out[cands[0]["key"]] = why
    return out


def team_points(game, team):
    if not game:
        return None, None, None
    sp, tot = (game.get("spread") or {}).get(team), game.get("total")
    if sp is None or not tot:
        return None, sp, tot
    return tot / 2 - sp / 2, sp, tot


# ------------------------------------------------------------------ the model
def _td(s, pos):
    return (s.get("rtd") or 0) + (0 if pos == "QB" else (s.get("ctd") or 0))


def defense_tables(stats, prev):
    """Per (defense, position): TDs allowed this season, games, per-game, rank (1 = most allowed), weekly list;
    last season's total and rank."""
    cur, games, weekly = {}, {}, {}
    for s in stats:
        if "rtd" not in s or not s.get("opp"):
            continue
        pos = s["pos"]
        td = (s.get("rtd") or 0) + ((s.get("ctd") or 0) if pos != "QB" else 0)
        cur[(s["opp"], pos)] = cur.get((s["opp"], pos), 0) + td
        weekly.setdefault((s["opp"], pos), {}).setdefault(s["wk"], 0)
        weekly[(s["opp"], pos)][s["wk"]] += td
        games.setdefault(s["opp"], set()).add(s["wk"])
    last, lgames = {}, {}
    for s in prev:
        if "rtd" not in s or not s.get("opp"):
            continue
        pos = s["pos"]
        last[(s["opp"], pos)] = last.get((s["opp"], pos), 0) + (s.get("rtd") or 0) + ((s.get("ctd") or 0) if pos != "QB" else 0)
        lgames.setdefault(s["opp"], set()).add(s["wk"])
    T = {}
    for pos in ("RB", "WR", "TE", "QB"):
        rows = {d: cur.get((d, pos), 0) for d in games}
        order = sorted(rows, key=lambda d: (-rows[d] / max(len(games[d]), 1), d))
        lrows = {d: last.get((d, pos), 0) for d in lgames}
        lorder = sorted(lrows, key=lambda d: -lrows[d])
        for d in set(games) | set(lgames):
            n = len(games.get(d, ())); ln = len(lgames.get(d, ()))
            wk = weekly.get((d, pos), {})
            recent = [wk.get(w, 0) for w in sorted(games.get(d, ()))[-2:]]
            T[(d, pos)] = {"td": rows.get(d, 0), "n": n, "pg": rows.get(d, 0) / n if n else None,
                           "rank": order.index(d) + 1 if d in rows else None, "nteams": len(order),
                           "ltd": lrows.get(d), "ln": ln, "lrank": lorder.index(d) + 1 if d in lrows else None,
                           "leak": len(recent) == 2 and all(x > 0 for x in recent), "recent": recent}
    lg = {}
    for pos in ("RB", "WR", "TE", "QB"):
        tot = sum(cur.get((d, pos), 0) for d in games); gm = sum(len(v) for v in games.values())
        ltot = sum(last.get((d, pos), 0) for d in lgames); lgm = sum(len(v) for v in lgames.values())
        lg[pos] = ((tot + PREV_W * ltot) / (gm + PREV_W * lgm)) if (gm or lgm) else None
    return T, lg


def build(data, out, venom=None, ladders=None):
    week = data.get("week")
    stats = data.get("stats") or []
    prev = data.get("prev_stats") or []
    has_td = any("rtd" in s for s in stats)
    has_rz = any((s.get("rzt") or 0) + (s.get("rza") or 0) for s in stats)
    ctx = data.get("context") or {}
    cgames = ctx.get("games") or {}
    games = {g["key"]: g for g in data.get("games") or []}
    rows_all = out.get("all") or out.get("top") or []
    live = {r["game"] for r in rows_all}
    roles = {}
    for r in rows_all:
        roles.setdefault(norm(r["player"]), r.get("role"))
    inj = {}
    for tm, L in (ctx.get("inj") or {}).items():
        for x in L:
            inj[norm(x["name"])] = x["status"]
    D, LG = defense_tables(stats, prev)
    gnews = ctx.get("gnews") or {}
    qbhurt = set(ctx.get("qbhurt") or [])
    cnorm = lambda n: " ".join(re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", re.sub(r"[^a-z ]", "", (n or "").lower().replace("-", " "))).split())
    # team totals per game (this season and last)
    team, tgames = {}, {}
    for s in stats:
        t = team.setdefault(s["team"], {"td": 0, "touch": 0, "tgt": 0, "rz": 0})
        t["td"] += _td(s, s["pos"]) if s["pos"] != "QB" else (s.get("rtd") or 0)
        t["touch"] += (s.get("att") or 0) + (s.get("tgt") or 0); t["tgt"] += s.get("tgt") or 0
        t["rz"] += (s.get("rzt") or 0) + (s.get("rza") or 0)
        tgames.setdefault(s["team"], set()).add(s["wk"])
    pteam = {}
    for s in prev:
        if "rtd" in s:
            pteam[s["team"]] = pteam.get(s["team"], 0) + ((s.get("rtd") or 0) + ((s.get("ctd") or 0) if s["pos"] != "QB" else 0))
    logs, plogs = {}, {}
    for s in stats:
        logs.setdefault(norm(s["name"]), []).append(s)
    for s in prev:
        plogs.setdefault(norm(s["name"]), []).append(s)
    mk = {}
    for m in data.get("td_markets") or []:
        if m.get("ask") and 0 < m["ask"] < 99:
            mid = (m["ask"] + m["bid"]) / 2 if m.get("bid") and 0 < m["bid"] < m["ask"] and m["ask"] - m["bid"] <= 10 else m["ask"] - 1.5
            mk[norm(m["name"])] = dict(m, mid=round(mid, 1))
    lad = {}
    for r in (ladders or rows_all):
        if r.get("stale") or r.get("est"):
            continue
        k = norm(r["player"])
        if r.get("pc", 0) >= 0.7 and 55 <= r["ask"] <= 92 and (k not in lad or r["score"] > lad[k]["score"]):
            lad[k] = r
    players = []
    for p in data.get("proj") or []:
        pos = p["pos"]
        if pos not in ("RB", "WR", "TE", "QB"):
            continue
        k = norm(p["name"])
        if inj.get(k) in ("Out", "Doubtful") or (p.get("inj") or "").lower() in ("out", "ir", "doubtful", "pup", "sus"):
            continue
        gn = gnews.get(cnorm(p["name"])) or {}
        if gn.get("cut"):
            continue                    # news says his role is being cut (Week 6: Skattebo)
        g = next((gg for gg in games.values() if p["team"] in (gg["away"], gg["home"])), None)
        if not g or (live and g["key"] not in live):
            continue
        if pos == "QB" and (p.get("rush") or 0) < 15:
            continue
        L = [s for s in logs.get(k, []) if s["team"] == p["team"]] or logs.get(k, [])
        n = len(L)
        role = roles.get(k) or ("QB" if pos == "QB" else None)
        if role is None:
            if n == 0:
                continue
            role = {"RB": "RB2", "WR": "WR3", "TE": "TE2"}[pos]
        if role not in ROLES:
            role = (pos + "2") if pos != "QB" else "QB"
        T = team.get(p["team"], {}); tg = len(tgames.get(p["team"], ())) or 1
        tds = sum(_td(s, pos) for s in L)
        PL = [s for s in plogs.get(k, []) if "rtd" in s]
        ptd = sum(_td(s, pos) for s in PL)
        ptm = pteam.get((PL[-1]["team"] if PL else p["team"]), 0) if PL else 0
        touches = sum((s.get("att") or 0) + (s.get("tgt") or 0) for s in L)
        tgts = sum(s.get("tgt") or 0 for s in L)
        rz = sum((s.get("rzt") or 0) + (s.get("rza") or 0) for s in L)
        prior = SHARE_PRIOR.get(role, 0.05)
        # share of team TDs (this season + last season at half weight), shrunk
        td_share = (tds + PREV_W * ptd + prior * SHARE_K) / ((T.get("td", 0)) + PREV_W * ptm + SHARE_K) if has_td else prior
        touch_share = touches / T["touch"] if T.get("touch") else None
        rz_share = rz / T["rz"] if has_rz and T.get("rz") else None
        tshare = tgts / T["tgt"] if T.get("tgt") else None
        if pos == "QB":
            share = td_share
        elif rz_share is not None:
            share = 0.45 * rz_share + 0.35 * td_share + 0.20 * (touch_share or prior)
        else:
            share = 0.65 * td_share + 0.35 * (touch_share or prior)
        cg = cgames.get(g["key"]) or {}
        pts, sp, tot = team_points(cg, p["team"])
        team_td = pts * TD_PER_PT if pts else (T.get("td", 0) / tg if T.get("td") else TEAM_TD_DEFAULT)
        opp = g["home"] if p["team"] == g["away"] else g["away"]
        dd = D.get((opp, pos)) or {}
        lgp = LG.get(pos)
        if dd and lgp and (dd.get("n") or dd.get("ln")):
            num = dd.get("td", 0) + PREV_W * (dd.get("ltd") or 0) + lgp * DEF_K
            den = dd.get("n", 0) + PREV_W * (dd.get("ln") or 0) + DEF_K
            dfac = max(0.6, min(1.7, (num / den) / lgp)) ** DEF_POW
        else:
            dfac = 1.0
        lam = team_td * share * dfac
        if p["team"] in qbhurt and pos in ("WR", "TE"):
            lam *= 0.85                 # his QB is playing hurt
        pm = 1 - math.exp(-max(lam, 0))
        m = mk.get(k)
        pc = (1 - MKT_W) * pm + MKT_W * m["mid"] / 100 if m else pm
        # TD debt: expected TDs from his role this season minus actual
        if has_rz:
            xtd = sum((s.get("rzt") or 0) * RZ_TGT_TD + (s.get("rza") or 0) * RZ_ATT_TD for s in L) + OTHER_TD * max(touches - rz, 0)
        else:
            xtd = (touch_share or 0) * T.get("td", 0) * (n / tg) if has_td else 0     # volume-based expectation
        debt = round(xtd - tds, 1) if has_td else None
        tpg = touches / n if n else 0
        info = {
            "key": k, "pl": p["name"], "tm": p["team"], "opp": opp, "pos": pos, "role": role, "g": g["key"], "gl": g["label"],
            "dy": dt.date.fromisoformat(g["date"]).strftime("%a") if g.get("date") else "", "id": p.get("id"),
            "lam": round(lam, 2), "pm": round(pm, 3), "p": round(pc, 3),
            "q": m["mid"] if m else None, "ask": m["ask"] if m else None, "tk": m["ticker"] if m else None, "ev": m["event"] if m else None,
            "tds": tds, "n": n, "games_td": sum(1 for s in L if _td(s, pos) > 0),
            "ptd": ptd if PL else None, "pn": len(PL) or None,
            "rz": round(100 * rz_share) if rz_share is not None else None, "tdsh": round(100 * td_share),
            "ts": round(100 * tshare) if tshare is not None and pos != "QB" else None,
            "tdr": round(100 * tds / touches, 1) if touches and has_td else None, "tpg": round(tpg, 1),
            "pts": round(pts, 1) if pts else None, "sp": sp, "tot": tot,
            "d": {kk: dd.get(kk) for kk in ("td", "n", "rank", "nteams", "ltd", "lrank", "leak", "recent")} if dd else None,
            "dfac": round(dfac, 2), "debt": debt, "hist": has_td,
            "inj": inj.get(k) or (p.get("inj") or None) or ("News" if gn.get("inj") else None), "news": gn.get("worst") if gn.get("inj") else None,
        }
        r = lad.get(k)
        if r:
            info["prop"] = {"pr": r["prop"], "ln": r["line"], "p": round(r["pc"], 3), "ask": r["ask"], "sc": r["score"]}
        players.append(info)
    _tags_and_scores(players)
    players.sort(key=lambda x: -x["p"])
    picks = read_watch(week) if venom is None else venom
    vmap = match_names(picks, players)
    for x in players:
        if x["key"] in vmap:
            x["venom"] = 1; x["vwhy"] = vmap[x["key"]]; x["vtags"] = tags_for(vmap[x["key"]])
        x["why"] = reasons(x)
    unmatched = [nm for nm, _ in picks if not match_names([(nm, "")], players)]
    return {"players": [x for x in players if x["p"] >= 0.08 or x.get("venom")], "parlays": parlays(players),
            "facts": game_facts(D, games, live), "venom_n": len(vmap), "venom_missing": unmatched,
            "has_td": has_td, "has_rz": has_rz, "has_mk": bool(mk), "week": week}


def _pct_rank(vals, v):
    vals = [x for x in vals if x is not None]
    if v is None or not vals:
        return 0.5
    return sum(1 for x in vals if x <= v) / len(vals)


def _tags_and_scores(players):
    """Cheat-code tags and a 0-100 Venom-style score on his signals."""
    by_pos = {}
    for x in players:
        by_pos.setdefault(x["pos"], []).append(x)
    for pos, L in by_pos.items():
        rzk = "rz" if any(x["rz"] is not None for x in L) else "tdsh"
        top = sorted([x for x in L if x[rzk] is not None], key=lambda x: -x[rzk])[:10]
        for x in L:
            t = []
            rzv = x["rz"] if x["rz"] is not None else x["tdsh"]
            if pos != "QB" and rzv is not None and rzv >= 25:
                t.append("Elite red zone role")
            if x in top and pos != "QB":
                t.append("RZ share top-10")
            if (pos == "RB" and x["tpg"] >= 15) or (pos in ("WR", "TE") and x["tpg"] >= 7.5):
                t.append("High volume")
            if x.get("ts") and x["ts"] >= 25:
                t.append("Target monster")
            d = x.get("d") or {}
            if d.get("rank") and d.get("nteams") and d["rank"] <= 8 and d.get("td"):
                t.append("Soft TD defense")
            if d.get("leak"):
                t.append("Active leak")
            if (x.get("tot") and x["tot"] >= 47) or (x.get("pts") and x["pts"] >= 26):
                t.append("Shootout script")
            if x.get("sp") is not None and x["sp"] <= -3:
                t.append("Favored")
            if x.get("debt") is not None and x["debt"] >= 1.0:
                t.append("Due")
            x["tags"] = t
            x["low"] = bool(x.get("pts") and x["pts"] < MIN_PTS)
            x["score"] = round(100 * (
                0.25 * _pct_rank([y["rz"] if y["rz"] is not None else y["tdsh"] for y in L], rzv) +
                0.10 * _pct_rank([y["tpg"] for y in L], x["tpg"]) +
                0.10 * _pct_rank([y.get("ts") for y in L], x.get("ts")) +
                0.05 * _pct_rank([y.get("tdr") for y in L], x.get("tdr")) +
                0.20 * _pct_rank([y["dfac"] for y in L], x["dfac"]) +
                0.20 * _pct_rank([y.get("pts") for y in L], x.get("pts")) +
                0.10 * _pct_rank([y.get("debt") for y in L], x.get("debt"))) * 0.6 + 40 * min(x["pm"] / 0.6, 1), 1)


def reasons(x):
    """His talking points, in his order: defense vs the position, his role, script, due."""
    out = []
    d = x.get("d") or {}
    pw = POSW.get(x["pos"], x["pos"])
    if d and d.get("n"):
        s = f"{x['opp']} has allowed {d['td']} {pw} TD{'s' if d['td'] != 1 else ''} in {d['n']} games"
        if d.get("rank"):
            s += (f", the most in the league" if d["rank"] == 1 else f" ({_ord(d['rank'])} most)" if d["rank"] <= 10 else
                  f" ({_ord(d['rank'])} most)")
        if d.get("ltd") is not None:
            s += f"; {d['ltd']} all last season" + (f" ({_ord(d['lrank'])} most)" if d.get("lrank") and d["lrank"] <= 10 else "")
        out.append(s + ".")
        if d.get("leak"):
            out.append(f"Active leak: gave up a {pw} TD in each of its last 2 games.")
    role = []
    if x.get("rz") is not None:
        role.append(f"{x['rz']}% red-zone role")
    elif x.get("hist"):
        role.append(f"{x['tdsh']}% of his team's TDs")
    if x.get("ts") is not None:
        role.append(f"{x['ts']}% target share")
    if x.get("tdr") is not None and x["tds"]:
        role.append(f"{x['tdr']:g}% TD rate")
    if x.get("tpg"):
        role.append(f"{x['tpg']:g} touches a game")
    if role:
        txt = ", ".join(role)
        out.append(txt[0].upper() + txt[1:] + ".")
    if x["n"] and x.get("hist"):
        s = (f"No TDs yet in {x['n']} games" if not x["tds"] else
             f"{x['tds']} TD{'s' if x['tds'] != 1 else ''} in {x['n']} games, scored in {x['games_td']}")
        if x.get("pn"):
            s += f" ({x['ptd']} in {x['pn']} last season)"
        out.append(s + ".")
    if x.get("pts"):
        sc = f"{x['tm']} projected for {x['pts']:.0f} pts"
        if x.get("tot"):
            sc += f" (O/U {x['tot']:g}"
            sc += (f", favored by {-x['sp']:g})" if x.get("sp") is not None and x["sp"] < 0 else
                   f", {x['sp']:g}-pt underdog)" if x.get("sp") else ")")
        out.append(("Low total: " if x.get("low") else "") + sc + ".")
    if x.get("debt") is not None and x["debt"] >= 1.0:
        out.append(f"Due: {x['debt']:g} TD debt (his role says he should have scored more).")
    if x.get("news"):
        out.append(f"Injury news: {x['news']}")
    elif x.get("inj") and x["inj"] not in ("Active",):
        out.append(f"Injury: listed {str(x['inj']).lower()}.")
    if x.get("q") is not None:
        out.append(f"Kalshi {x['q']:.0f}¢ vs model {round(100 * x['pm'])}%.")
    return out


def game_facts(D, games, live):
    """The app's 'Game Cheat Code' lines: a defense that's among the worst in the league against a position."""
    out = {}
    for key, g in games.items():
        if live and key not in live:
            continue
        lines = []
        for d, o in ((g["home"], g["away"]), (g["away"], g["home"])):
            for pos, word in (("RB", "running backs"), ("WR", "wide receivers"), ("TE", "tight ends")):
                x = D.get((d, pos))
                if x and x.get("rank") and x["rank"] <= 3 and x.get("td"):
                    lines.append(f"{d} has allowed {x['td']} TDs to {word} in {x['n']} games, "
                                 + ("the most in the league." if x["rank"] == 1 else f"{_ord(x['rank'])} most in the league."))
        if lines:
            out[key] = lines
    return out


def parlays(players):
    """Safest TD: the 2 likeliest scorers (40%+), different games.
    Cheat Code TD: 3 legs, the best-scoring players with 2+ cheat codes, 33%+, different games.
    Every leg: not injured, and his team projected for 21+ points."""
    ok = [x for x in players if (not x.get("inj") or x["inj"] in ("Active",)) and not x.get("low")]
    def pick(pool, n, minp, key):
        out, gs = [], set()
        for x in sorted(pool, key=key):
            if x["p"] >= minp and x["g"] not in gs:
                out.append(x); gs.add(x["g"])
                if len(out) == n:
                    break
        return [x["key"] for x in out]
    bonus = lambda x: JAKE_BONUS if x.get("venom") else 0
    return {"safe": pick(ok, 2, MIN_SAFE, lambda x: -(x["p"] + bonus(x))),
            "code": pick([x for x in ok if len(x.get("tags") or []) >= 2], 3, MIN_LEG,
                         lambda x: -(x["score"] / 100 + x["p"] + bonus(x)))}
