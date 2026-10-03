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


def load_overrides(path):
    """Manual corrections: name,wk,field,value,source. value may be DNP."""
    out = []
    if os.path.exists(path):
        for r in csv.DictReader(open(path)):
            if r.get("name"):
                out.append(r)
    return out


def run(data, overrides=None):
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

    games = {g["key"]: g for g in data["games"]}
    rows, skipped = [], set()
    for m in data["markets"]:
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
        if m["ask"] <= 0:
            continue
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
        pv = pj[stat]
        pp = Phi((pv - line + 0.5) / sd_for(stat, pos, pv))
        dh_u = dh if dh is not None else 0.5
        p = (0.40 * pp + 0.30 * h + 0.30 * dh_u) if dh is not None else (0.55 * pp + 0.45 * h)
        p = min(p, P_CAP)
        roi = p / (m["ask"] / 100) - 1
        value = min(max(0.5 + roi / 1.0, 0), 1)
        agree = (pp >= 0.6) + (over == n) + (dh is not None and dc == dn)
        spread = (m["ask"] - m["bid"]) if m.get("bid") is not None else 0
        score = 100 * (W_PROJ * pp + W_HIT * h + W_DEF * dh_u + W_RANK * rank_s + W_VALUE * value + W_AGREE * (agree / 3))
        misses_p = n - over; misses_d = (dn - dc) if dh is not None else 0
        score *= (1 - MISS_PENALTY_PLAYER * misses_p - MISS_PENALTY_DEF * misses_d)
        flags = []
        if spread >= SPREAD_LIMIT:
            score *= SPREAD_PENALTY; flags.append("Wide bid/ask spread, thin market")
        rows.append(dict(game=m["game"], glabel=g["label"], player=lg["name"], team=team, opp=opp, pos=pos, role=role,
                         stat=stat, prop=LBL[stat], line=line, ask=m["ask"], bid=m.get("bid"), vals=vals, over=over, n=n,
                         proj=round(pv, 1), al=[[w, t, v] for w, t, v in al],
                         dhit=(dc if dh is not None else None), dn=(dn if dh is not None else None),
                         most=most, per=per, dstat=("pass yds" if idx == 1 else "rush yds"),
                         p=round(p * 100), roi=round(roi * 100), pp=round(pp * 100), hs=round(h * 100),
                         dhs=round(dh_u * 100), rks=round(rank_s * 100), vs=round(value * 100),
                         agree=int(agree), score=round(score, 1), flags=flags))

    lad = {}
    for r in rows:
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
        c = [r for r in L if ASK_MIN <= r["ask"] <= ASK_MAX and not r.get("stale")]
        if c:
            best.append(max(c, key=lambda r: r["score"]))
    best.sort(key=lambda r: -r["score"])
    top, cnt = [], {}
    for r in best:
        if cnt.get(r["player"], 0) >= MAX_PER_PLAYER:
            continue
        cnt[r["player"]] = cnt.get(r["player"], 0) + 1
        top.append(r)
    for i, r in enumerate(top):
        r["rank"] = i + 1
    return {"top": top[:BOARD_SIZE], "rows": len(rows), "ladders": len(lad), "skipped": sorted(skipped)}


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA, "data.json")
    data = json.load(open(src))
    out = run(data, load_overrides(os.path.join(HERE, "overrides.csv")))
    ov = load_overrides(os.path.join(HERE, "overrides.csv"))
    out["meta"] = {k: data[k] for k in ("season", "week", "completed_weeks", "generated", "league_source", "notes", "eyebrow") if k in data}
    out["meta"]["games"] = [g["label"] for g in data["games"]]
    out["meta"]["ovr_note"] = f", with {len(ov)} manual corrections from overrides.csv" if ov else ""
    json.dump(out, open(os.path.join(DATA, "out.json"), "w"), indent=1)
    print(f"scored {out['rows']} props on {out['ladders']} ladders; board has {len(out['top'])}")
    for r in out["top"][:10]:
        print(r["rank"], r["score"], r["player"], r["prop"], f"{r['line']}+", f"{r['ask']}c")
