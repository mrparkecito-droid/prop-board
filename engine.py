"""Scores every Kalshi player prop in data/data.json and writes data/out.json.

This is the same model used to build the Week 4 board:

  score = 100 * ( 0.28 * Sleeper projection chance
                + 0.20 * player hit rate        (shrunk: (over+1)/(n+2))
                + 0.20 * defense vs this line   (same role, opponent's games, shrunk)
                + 0.10 * defense rank           (pass or rush yds allowed per game)
                + 0.17 * price value            (model chance vs Kalshi ask)
                + 0.05 * agreement bonus )      (projection, history and defense all say yes)
  then  x (1 - 0.08 per player miss - 0.05 per defense miss)
        x 0.90 if the bid/ask spread is 15c or wider
        x 0.85 if the rung is priced below a higher rung on the same ladder (stale)

Best line = top score on each player/stat ladder priced 35c-88c and not stale.
Board = best lines sorted by score, max 2 props per player, top 50.
"""
import csv, json, math, os, re, sys
import context
import usage
import script
import matchup

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

# ---------------- tunable constants (kept identical to the original run) ----------------
W_PROJ, W_HIT, W_DEF, W_RANK, W_VALUE, W_AGREE = 0.28, 0.20, 0.20, 0.10, 0.17, 0.05
MISS_PENALTY_PLAYER, MISS_PENALTY_DEF = 0.08, 0.05
SPREAD_LIMIT, SPREAD_PENALTY = 15, 0.90
STALE_GAP, STALE_PENALTY = 3, 0.85
ASK_MIN, ASK_MAX = 35, 88
MAX_PER_PLAYER, BOARD_SIZE = 2, 50
MIN_GAMES = 2
P_CAP = 0.97
MARKET_BLEND = 0.30    # final chance = 70% model + 30% Kalshi's price (Week 4: the blend was better calibrated than either)
CTX_WEIGHT = 0.04      # game context (lines, weather, injuries, news, buzz) moves a score at most +/-4%
SLEEPER_STATUS = {"Questionable": "Questionable", "Doubtful": "Doubtful", "Out": "Out", "IR": "Out", "PUP": "Out", "Sus": "Out"}

DEFAULT_W = {"proj": W_PROJ, "hit": W_HIT, "def": W_DEF, "rank": W_RANK}

Phi = lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2)))
STATKEY = {"pass": "pass_yd", "rush": "rush_yd", "rec": "rec", "recyds": "rec_yd"}
LBL = {"pass": "Passing Yards", "rush": "Rushing Yards", "rec": "Receptions", "recyds": "Receiving Yards"}


def norm(n):
    n = n.replace(".", "").replace("'", "").replace("’", "").lower()
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


def sd_for(stat, pos, pv):
    if stat == "pass":
        return max(0.22 * pv, 45)
    if stat == "rec":
        return max(0.45 * pv, 1.2)
    if stat == "rush":
        return max(0.55 * pv, 12) if pos != "QB" else max(0.6 * pv, 10)
    return max(0.55 * pv, 12)


def role_group(pos, role):
    if pos in ("QB", "TE"):
        return pos
    if role in ("RB1", "WR1", "WR2"):
        return role
    return "RB2" if pos == "RB" else "WR3"


def logit(x):
    x = min(max(x, 0.02), 0.98)
    return math.log(x / (1 - x))


def calibrate(p, ask, stat, rg, learned):
    """Learned correction of the model chance (identity until results have been graded)."""
    c = (learned or {}).get("calib")
    if not c:
        return p
    z = c["a"] + c["b"] * logit(p) + c["c"] * logit(ask / 100) + c["stat"].get(stat, 0) + c["role"].get(rg, 0)
    return min(1 / (1 + math.exp(-z)), P_CAP)


def load_overrides(path):
    """Manual corrections: name,wk,field,value,source. value may be DNP."""
    out = []
    if os.path.exists(path):
        for r in csv.DictReader(open(path)):
            if r.get("name"):
                out.append(r)
    return out


def run(data, overrides=None, learned=None):
    W = dict(DEFAULT_W)
    W.update((learned or {}).get("weights") or {})
    weeks = data["completed_weeks"]
    # ---------- game logs ----------
    logs, teamgames = {}, {}
    for s in data["stats"]:
        k = norm(s["name"])
        teamgames[(s["team"], s["wk"])] = s["opp"]
        e = logs.setdefault(k, {"name": s["name"], "team": s["team"], "pos": s["pos"], "wk": {}})
        e["team"] = s["team"]
        e["wk"][s["wk"]] = {f: float(s.get(f) or 0) for f in ("pass_yd", "rush_yd", "rec", "rec_yd")}
    for g in data.get("team_games", []):
        teamgames[(g["team"], g["wk"])] = g["opp"]
    dnp = set()
    for r in overrides or []:
        k = norm(r["name"]); e = logs.get(k)
        if not e:
            continue
        wk = int(r["wk"])
        if str(r["value"]).upper() == "DNP":
            dnp.add((k, wk)); continue
        e["wk"].setdefault(wk, {})[r["field"]] = float(r["value"])

    # ---------- projections ----------
    proj = {}
    for p in data["proj"]:
        proj[norm(p["name"])] = p

    # ---------- defense by role ----------
    by_tg = {}
    for k, e in logs.items():
        for wk, s in e["wk"].items():
            by_tg.setdefault((e["team"], wk), []).append((e["pos"], s))

    def role_vals(team, wk, pos, stat, rank):
        key = STATKEY[stat]
        vals = sorted([s.get(key, 0) for p, s in by_tg.get((team, wk), []) if p == pos], reverse=True)
        return vals[rank - 1] if len(vals) >= rank else 0.0

    def allowed(defense, pos, stat, rank):
        out = []
        for (team, wk), opp in sorted(teamgames.items(), key=lambda x: x[0][1]):
            if opp == defense and wk in weeks:
                out.append((wk, team, role_vals(team, wk, pos, stat, rank)))
        return out

    league = data["league"]  # team -> [games, pass_yds, rush_yds]

    def drank(team, idx):
        per = lambda v: v[idx] / v[0]
        me = per(league[team])
        most = 1 + sum(1 for v in league.values() if per(v) > me + 1e-9)
        return most, round(me, 1)

    # league average for each role (what a WR2, TE1... gets in a typical game) and team offense per game
    _ravg = {}

    def role_avg(pos, stat, rank):
        kk = (pos, stat, rank)
        if kk not in _ravg:
            xs = [role_vals(t, w, pos, stat, rank) for (t, w) in teamgames if w in weeks]
            _ravg[kk] = sum(xs) / len(xs) if xs else None
        return _ravg[kk]

    off_tot = {}
    for (t, w), L in by_tg.items():
        if w not in weeks:
            continue
        o = off_tot.setdefault(t, {"g": 0, "pass": 0.0, "rush": 0.0})
        o["g"] += 1
        o["pass"] += sum(s_.get("pass_yd", 0) for p_, s_ in L if p_ == "QB")
        o["rush"] += sum(s_.get("rush_yd", 0) for p_, s_ in L)
    OFF = {}
    for kind in ("pass", "rush"):
        pg = {t: o[kind] / o["g"] for t, o in off_tot.items() if o["g"]}
        avg = sum(pg.values()) / len(pg) if pg else None
        order = sorted(pg, key=lambda t: -pg[t])
        OFF[kind] = {"pg": pg, "avg": avg, "rank": {t: i + 1 for i, t in enumerate(order)}}
    STATWORD = {"pass": "passing yds", "rush": "rushing yds", "rec": "catches", "recyds": "receiving yds"}

    # usage (targets/carries/snaps) and last season, when the data has them
    U = usage.build(data["stats"], norm) if usage.has_usage(data["stats"]) else None
    prev_rows = {}
    for s in data.get("prev_stats") or []:
        prev_rows.setdefault(norm(s["name"]), []).append(s)
    prev_tot = usage.totals(data.get("prev_stats") or [], norm)
    # quarterbacks: who started each game, who usually starts, who starts this week
    QB = None
    if U:
        C = data.get("context") or {}
        status_of = {}
        for tm_, L in (C.get("inj") or {}).items():
            for x in L:
                status_of[norm(x["name"])] = x["status"]
        for p_ in data["proj"]:
            st_ = SLEEPER_STATUS.get(p_.get("inj") or "")
            if st_ and norm(p_["name"]) not in status_of:
                status_of[norm(p_["name"])] = st_
        out_names = {n for n, v in status_of.items() if v in ("Out", "Doubtful")}
        QB = {"st": script.starters(data["stats"], norm), "pst": script.starters(data.get("prev_stats") or [], norm),
              "status": status_of, "out": out_names, "now": {}, "usual": {}}

    games = {g["key"]: g for g in data["games"]}
    est_asks = fill_missing_asks(data["markets"], proj)
    rows, skipped = [], set()
    # games that have already kicked off are left off: Kalshi keeps trading them live, so their prices are in-game
    started = set()
    _now = data.get("generated")
    if _now:
        import datetime as _dt
        try:
            nowdt = _dt.datetime.fromisoformat(_now)
            for gk, gv in (((data.get("context") or {}).get("games")) or {}).items():
                kick = (gv or {}).get("kick")
                if kick and nowdt >= _dt.datetime.fromisoformat(kick.replace("Z", "+00:00")):
                    started.add(gk)
        except ValueError:
            pass
    for m in data["markets"]:
        if m["game"] in started:
            continue
        k = norm(m["name"]); lg = logs.get(k); pj = proj.get(k)
        g = games.get(m["game"])
        if not g or not lg or not pj:
            skipped.add(m["name"]); continue
        stat = m["series"]; pos = lg["pos"]; team = lg["team"]
        if team not in (g["away"], g["home"]):
            skipped.add(m["name"]); continue
        opp = g["home"] if team == g["away"] else g["away"]
        if stat not in pj or pj[stat] is None:
            continue
        est = False
        if no_ask(m):
            if id(m) not in est_asks:
                continue
            m = dict(m, ask=est_asks[id(m)], bid=None); est = True
        # player log. QB missing = DNP; other positions missing = counted as a miss (shown as -)
        vals = []
        for wk in weeks:
            if (team, wk) not in teamgames:
                vals.append(None); continue
            if (k, wk) in dnp:
                vals.append(None); continue
            s = lg["wk"].get(wk)
            if s is None:
                vals.append(None if pos == "QB" else -1)
            else:
                vals.append(s.get(STATKEY[stat], 0))
        played = [v for v in vals if v is not None]
        if len(played) < MIN_GAMES:
            continue
        line = m["line"]
        over = sum(1 for v in played if v >= line); n = len(played)
        h = (over + 1) / (n + 2)
        po = pn = 0
        if prev_rows.get(k):
            wo, wn, po, pn = usage.prev_hits(prev_rows[k], STATKEY[stat], line, pos)
            h = (over + wo + 1) / (n + wn + 2)
        # cushion: by how much he cleared (or missed) the line, not just yes/no
        cu, avgm = matchup.cushion(vals, line, sd_for(stat, pos, max(line, 1)))
        if cu is not None:
            h = 0.5 * h + 0.5 * cu
        # role on depth chart, by Sleeper projection within team and position
        if pos in ("WR", "TE", "RB"):
            keyf = "recyds" if pos != "RB" else "rush"
            mates = [(v.get(keyf) or 0) for v in proj.values() if v["team"] == team and v["pos"] == pos]
            me = pj.get(keyf) or 0
            rank = 1 + sum(1 for x in mates if x > me + 1e-9)
        else:
            rank = 1
        if stat in ("rec", "recyds") and pos == "RB":
            rank = 1
        role = f"{pos}{rank}" if pos != "QB" else "QB"
        dh = None; al = []; dc = dn = 0
        if not (pos == "QB" and stat == "rush"):
            al = allowed(opp, pos, stat, rank)
            if al:
                dc = sum(1 for _, _, v in al if v >= line); dn = len(al)
                dh = (dc + 1) / (dn + 2)
        idx = 1 if stat in ("pass", "rec", "recyds") else 2
        most, per = drank(opp, idx)
        rank_s = (32 - most) / 31
        mx, mnote = matchup.matchup([v for _, _, v in al], role_avg(pos, stat, rank) if al else None, rank_s, opp,
                                    context.ROLEW.get(role, role + "s"), STATWORD[stat])
        # game script (spread + over/under) and, for receivers, the quarterback situation
        sx, srs = script.game_script(team, stat, pos, ((data.get("context") or {}).get("games") or {}).get(m["game"]))
        qf, qnote, qinfo = 0.0, "", None
        if QB and stat in ("rec", "recyds") and pos in ("WR", "TE", "RB"):
            if team not in QB["now"]:
                QB["now"][team] = script.this_week(team, data["proj"], QB["out"], norm)
                QB["usual"][team] = script.usual(team, weeks, QB["st"])
                uq_ = QB["usual"][team]
                if QB["now"][team] is None and uq_ and uq_[0] in QB["out"]:
                    QB["now"][team] = ("?", "the backup")        # starter out, backup not projected yet
            uq = QB["usual"][team]
            qf, qnote, qinfo = script.receiver_qb(
                {w: lg["wk"][w].get(STATKEY[stat], 0) for w in weeks if w in lg["wk"]}, team, line, weeks, QB["st"],
                QB["now"][team], {r_["wk"]: r_.get(STATKEY[stat], 0) or 0 for r_ in prev_rows.get(k, []) if r_["team"] == team},
                QB["pst"], uq, QB["status"].get(uq[0], "") if uq else "")
        pv = pj[stat]
        pp = Phi((pv - line + 0.5) / sd_for(stat, pos, pv))
        pp_raw, pu, uinfo = pp, None, None
        uc = usage.chance(k, pos, team, stat, line, U, prev_tot, sd_for)
        if uc:
            pu, _, uinfo = uc
            pp = 0.5 * pp + 0.5 * pu          # projection signal = Sleeper projection + usage model
        dfac, dnote = matchup.depth(role, pos, stat, uinfo)
        okind = "rush" if stat == "rush" else "pass"
        newqb = ""
        if stat == "pass" and pos == "QB" and data.get("prev_stats") is not None:
            prev_teams = {r_["team"] for r_ in prev_rows.get(k, []) if (r_.get("patt") or 0) >= 10}
            if not prev_teams:
                newqb = "First season as a regular starter."
            elif team not in prev_teams:
                newqb = f"First season with {team}, so last year's numbers carry less."
        off, onote = matchup.offense(team, stat, pos, OFF[okind]["pg"].get(team), OFF[okind]["avg"],
                                     OFF[okind]["rank"].get(team, 0), newqb, context.ordinal)
        envf = matchup.env(mx, sx)
        dh_u = dh if dh is not None else 0.5
        p = (0.40 * pp + 0.30 * h + 0.30 * dh_u) if dh is not None else (0.55 * pp + 0.45 * h)
        p = p * (1 + matchup.ENV_P * envf) * (1 + script.P_QB * qf) * (1 + matchup.DEPTH_P * dfac) * (1 + matchup.OFF_P * off)
        p = min(p, P_CAP)
        p_raw = p
        rg = role_group(pos, role)
        p = calibrate(p_raw, m["ask"], stat, rg, learned)
        if data.get("market_blend", True):
            p = (1 - MARKET_BLEND) * p + MARKET_BLEND * (mid_price(m) / 100)
        roi = p / (m["ask"] / 100) - 1
        value = min(max(0.5 + roi / 1.0, 0), 1)
        agree = (pp >= 0.6) + (over == n) + (dh is not None and dc == dn)
        spread = (m["ask"] - m["bid"]) if m.get("bid") is not None else 0
        score = 100 * (W["proj"] * pp + W["hit"] * h + W["def"] * dh_u + W["rank"] * rank_s + W_VALUE * value + W_AGREE * (agree / 3))
        misses_p = n - over; misses_d = (dn - dc) if dh is not None else 0
        score *= (1 - MISS_PENALTY_PLAYER * misses_p - MISS_PENALTY_DEF * misses_d)
        ctx, reasons, status = context.assess(
            {"game": m["game"], "team": team, "opp": opp, "stat": stat, "pos": pos, "player": lg["name"]},
            data.get("context"), SLEEPER_STATUS.get(pj.get("inj") or ""))
        score *= 1 + CTX_WEIGHT * ctx
        score *= (1 + matchup.ENV_W * envf) * (1 + script.QB_W * qf) * (1 + matchup.DEPTH_W * dfac) * (1 + matchup.OFF_W * off)
        reasons = reasons + srs
        if mnote:
            reasons.append((round(0.5 * mx, 2), mnote))
        if dnote:
            reasons.append((round(dfac, 2) or -0.01, dnote))
        if onote:
            reasons.append((round(off, 2) or -0.01, onote))
        flags = []
        if status in ("Out", "Doubtful"):
            flags.append(f"Listed {status.lower()} this week")
        if spread >= SPREAD_LIMIT:
            score *= SPREAD_PENALTY; flags.append("Wide bid/ask spread, thin market")
        rows.append(dict(game=m["game"], glabel=g["label"], player=lg["name"], team=team, opp=opp, pos=pos, role=role,
                         stat=stat, prop=LBL[stat], line=line, ask=m["ask"], bid=m.get("bid"), vals=vals, over=over, n=n,
                         proj=round(pv, 1), al=[[w, t, v] for w, t, v in al],
                         dhit=(dc if dh is not None else None), dn=(dn if dh is not None else None),
                         most=most, per=per, dstat=("pass yds" if idx == 1 else "rush yds"),
                         p=round(p * 100), roi=round(roi * 100), pp=round(pp * 100), hs=round(h * 100),
                         dhs=round(dh_u * 100), rks=round(rank_s * 100), vs=round(value * 100),
                         agree=int(agree), score=round(score, 1), flags=flags,
                         pc=round(p, 4), praw=round(p_raw, 4), rg=rg, est=est,
                         mid=mid_price(m), tk=m.get("ticker"), ev=m.get("event"),
                         ctx=ctx, cxs=round(50 + 50 * ctx), reasons=reasons, status=status,
                         ppj=round(pp_raw * 100), pu=(round(pu * 100) if pu is not None else None), uinfo=uinfo,
                         po=po, pn=pn, pid=pj.get("id"),
                         sx=sx, sxs=round(50 + 50 * sx), mx=mx, mxs=round(50 + 50 * mx), env=envf, dfac=dfac, dfs=round(50 + 50 * dfac),
                         off=off, ofs=round(50 + 50 * off), cu=(round(cu * 100) if cu is not None else None),
                         avgm=(round(avgm, 1) if avgm is not None else None), qf=qf, qfs=round(50 + 50 * qf), qnote=qnote, qinfo=qinfo))

    lad = {}
    for r in rows:
        if r["est"]:
            continue          # estimated prices are for parlays only, never the board
        lad.setdefault((r["player"], r["stat"]), []).append(r)
    for L in lad.values():
        L.sort(key=lambda x: x["line"])
        for i, a in enumerate(L):
            if any(b["ask"] > a["ask"] + STALE_GAP for b in L[i + 1:]):
                a["score"] = round(a["score"] * STALE_PENALTY, 1)
                a["flags"].append("Priced below a higher line, likely stale")
                a["stale"] = True
    best = []
    for L in lad.values():
        c = [r for r in L if ASK_MIN <= r["ask"] <= ASK_MAX and not r.get("stale") and r["status"] != "Out"]
        if c:
            safe = max(c, key=lambda r: r["score"])
            pick = balanced_pick(safe, c)
            if pick is not safe:
                pick["safer"] = safe
            higher = [r for r in c if r["line"] > pick["line"] and r["pc"] >= STRETCH_MIN_P and r["roi"] >= STRETCH_MIN_ROI]
            if higher:
                pick["bigger"] = max(higher, key=lambda r: r["line"])
            best.append(pick)
    best.sort(key=lambda r: -r["score"])
    top, cnt = [], {}
    for r in best:
        if cnt.get(r["player"], 0) >= MAX_PER_PLAYER:
            continue
        cnt[r["player"]] = cnt.get(r["player"], 0) + 1
        top.append(r)
    alt = lambda a: {"line": a["line"], "ask": a["ask"], "bid": a.get("bid"), "p": a["p"], "pc": a["pc"], "roi": a["roi"],
                     "score": a["score"], "mid": a["mid"], "tk": a.get("tk")}
    for r in best:
        for kk in ("safer", "bigger"):
            if kk in r:
                r[kk] = alt(r[kk])
    for i, r in enumerate(top):
        r["rank"] = i + 1
        r["why"], r["news"] = context.why(r, r["reasons"])
    return {"top": top[:BOARD_SIZE], "rows": len(rows), "ladders": len(lad), "skipped": sorted(skipped), "all": rows}


# ---------------- picking the line on each ladder: safe, but not needlessly low ----------------
# The safest rung is often far below what the player is expected to get (e.g. 30+ when he's projected 59).
# When the matchup/game script is good or the projection leaves lots of room, the board steps up to a higher
# line, as long as it's still very likely (model >= BAL_MIN_P), priced fairly (model >= Kalshi price) and its
# score is within a few points of the safest line. The stronger the case, the more score it may give up.
BAL_MIN_P = 0.72
BAL_TOL_BASE, BAL_TOL_STRENGTH = 2.0, 6.0
STRETCH_MIN_P, STRETCH_MIN_ROI = 0.55, 5      # "bigger payout" alternative shown on the card


def balanced_pick(safe, rungs):
    headroom = (safe["proj"] - safe["line"]) / sd_for(safe["stat"], safe["pos"], max(safe["proj"], 1))
    strength = max(0.0, min(1.0, 0.5 * max(safe.get("env", 0), 0) + 0.5 * max(0.0, min(1.0, headroom))))
    tol = BAL_TOL_BASE + BAL_TOL_STRENGTH * strength
    ok = [r for r in rungs if r["line"] > safe["line"] and r["pc"] >= BAL_MIN_P and r["roi"] >= 0
          and r["score"] >= safe["score"] - tol]
    if not ok:
        return safe
    pick = max(ok, key=lambda r: r["line"])
    pick["stepup"] = round(strength, 2)
    return pick


PARLAY_ASK_MAX = 92


def parlay_legs(out):
    """Rungs the parlay builder may use: every fair-priced rung on the ladders that made the board."""
    on_board = {(r["player"], r["stat"]) for r in out["top"]}
    legs = []
    for r in out["all"]:
        if (r["player"], r["stat"]) not in on_board or r.get("stale"):
            continue
        if not (ASK_MIN <= r["ask"] <= PARLAY_ASK_MAX) or r["pc"] < r["mid"] / 100 - 0.02:
            continue
        legs.append({"pl": r["player"], "tm": r["team"], "g": r["game"], "gl": r["glabel"], "st": r["stat"],
                     "pr": r["prop"], "ln": r["line"], "ask": r["ask"], "q": r["mid"], "p": r["pc"], "sc": r["score"],
                     "est": 1 if r["est"] else 0, "tk": r["tk"], "ev": r["ev"],
                     "wy": context.why(r, r.get("reasons") or [])[0], "id": r.get("pid"),
                     "cx": r.get("ctx", 0), "nw": context.why(r, r.get("reasons") or [])[1]})
    return legs


def no_ask(m):
    """No sellers showing: Kalshi reports 0 (or 100) when a rung has no yes offers."""
    return m["ask"] <= 0 or m["ask"] >= 99


def mid_price(m):
    """Fair price of one leg in cents: bid/ask midpoint when the market is tight, else just under the ask."""
    a, b = m["ask"], m.get("bid")
    if b and 0 < b < a and a - b <= 10:
        return round((a + b) / 2, 1)
    return round(max(a - 1.5, 1), 1)


def _ppf(p):
    """Inverse normal CDF (Acklam), accurate to ~1e-9."""
    p = min(max(p, 1e-6), 1 - 1e-6)
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    if p < 0.02425:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > 1 - 0.02425:
        return -_ppf(1 - p)
    q = p - 0.5; r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def fill_missing_asks(markets, proj):
    """Estimate a price for rungs with no ask from the rest of the same ladder.

    Each priced rung says how likely the player is to clear that line. On a normal-curve scale those
    chances fall along a straight line as the yardage goes up, so:
      - a rung between two priced rungs gets the point between them (the 'middle', weighted by yardage),
      - a rung above or below all priced rungs follows the slope of the whole ladder,
      - a ladder with only one priced rung uses the model's usual spread for that stat.
    Returns {id(market): estimated ask in cents}."""
    lad = {}
    for m in markets:
        lad.setdefault((m["game"], m["series"], norm(m["name"])), []).append(m)
    out = {}
    for (g, stat, k), L in lad.items():
        miss = [m for m in L if no_ask(m)]
        known = sorted([m for m in L if not no_ask(m)], key=lambda m: m["line"])
        if not miss or not known:
            continue
        pts = [(m["line"], _ppf(min(max(m["ask"], 2), 98) / 100)) for m in known]
        if len(pts) >= 2:
            n = len(pts); mx = sum(x for x, _ in pts) / n; my = sum(y for _, y in pts) / n
            sxx = sum((x - mx) ** 2 for x, _ in pts)
            slope = sum((x - mx) * (y - my) for x, y in pts) / sxx if sxx else 0
        else:
            slope = 0
        if slope >= 0:   # prices should fall as the line rises; fall back to the stat's usual spread
            pj = proj.get(k) or {}
            pv = pj.get(stat) or known[0]["line"]
            slope = -1 / sd_for(stat, pj.get("pos", ""), pv)
        for m in miss:
            x = m["line"]
            lo = [p for p in pts if p[0] < x]; hi = [p for p in pts if p[0] > x]
            if lo and hi:
                (x0, y0), (x1, y1) = lo[-1], hi[0]
                z = y0 + (y1 - y0) * (x - x0) / (x1 - x0)
            else:
                x0, y0 = (lo[-1] if lo else hi[0])
                z = y0 + slope * (x - x0)
            out[id(m)] = int(round(min(max(Phi(z) * 100, 3), 97)))
    return out


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA, "data.json")
    data = json.load(open(src))
    ppath = os.path.join(DATA, "prev_season.json")
    if os.path.exists(ppath) and len(sys.argv) <= 1:
        try:
            P = json.load(open(ppath))
            if P.get("season") == data["season"] - 1:
                data["prev_stats"] = P["stats"]
        except (OSError, ValueError, KeyError):
            pass
    cpath = os.path.join(DATA, "context.json")
    if os.path.exists(cpath) and len(sys.argv) <= 1:
        C = json.load(open(cpath))
        try:      # never use a stale context file (e.g. if context.py failed this run)
            import datetime as _dt
            age = abs(_dt.datetime.fromisoformat(data["generated"]) - _dt.datetime.fromisoformat(C["generated"]))
            if age < _dt.timedelta(hours=12):
                data["context"] = C
        except Exception:
            pass
    ov = load_overrides(os.path.join(HERE, "overrides.csv"))
    import learn
    learned, track = learn.prepare(data, ov)        # grade past picks, refit weights + calibration
    out = run(data, ov, learned)
    learn.snapshot(data, out)                        # save this week's picks so they get graded later
    out["parlay"] = parlay_legs(out)
    out["track"] = track
    out["meta"] = {k: data[k] for k in ("season", "week", "completed_weeks", "generated", "league_source", "notes", "eyebrow") if k in data}
    out["meta"]["games"] = [g["label"] for g in data["games"]]
    out["meta"]["ctx_sources"] = (data.get("context") or {}).get("sources")
    out["meta"]["ovr_note"] = f", with {len(ov)} manual corrections from overrides.csv" if ov else ""
    out.pop("all", None)
    json.dump(out, open(os.path.join(DATA, "out.json"), "w"), indent=1)
    print(f"scored {out['rows']} props on {out['ladders']} ladders; board has {len(out['top'])}")
    for r in out["top"][:10]:
        print(r["rank"], r["score"], r["player"], r["prop"], f"{r['line']}+", f"{r['ask']}c")
