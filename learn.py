"""Track record + learning.

Every run:
  1. snapshot()  saves this week's scored props to data/history.json (only games that haven't started).
  2. prepare()   grades every saved week that has finished against Sleeper box scores, then
                 - refits how much each safety signal (projection, hit rate, defense vs line, defense rank)
                   is worth in the score, and
                 - recalibrates the model's chance (overall and by stat and role) so "70%" means 70%.
                 It returns the learned settings for engine.run() and a report for the Results tab.

Both are shrunk toward the original model, so one odd week can only nudge it. The more weeks graded,
the more the results are trusted (capped at 60% data / 40% original).
"""
import datetime as dt, json, math, os
from zoneinfo import ZoneInfo

import engine

HERE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(HERE, "data", "history.json")
LEARNED = os.path.join(HERE, "data", "learned.json")
PT = ZoneInfo("America/Los_Angeles")

SIGNALS = [("proj", "pp", "Sleeper projection"), ("hit", "hs", "Player hit rate"),
           ("def", "dh", "Defense vs line"), ("rank", "rk", "Defense rank")]
SAFETY_BUDGET = sum(engine.DEFAULT_W.values())          # 0.78; value and agreement weights stay fixed
W_MIN, W_MAX = 0.05, 0.36
TRUST_N, TRUST_CAP = 1500, 0.60                         # ladders graded before results get half the say
MIN_LADDERS = 40                                        # below this, nothing is changed
RIDGE = {"a": 150, "b": 150, "c": 150, "stat": 150, "role": 150}
STATS = ["pass", "rush", "recyds", "rec"]
ROLES = ["QB", "RB1", "RB2", "WR1", "WR2", "WR3", "TE"]
STATW = {"pass": "passing", "rush": "rushing", "recyds": "receiving yards", "rec": "receptions"}
STATU = {"pass": "pass yds", "rush": "rush yds", "recyds": "rec yds", "rec": "catches"}
ROLEW = {"QB": "QB", "RB1": "lead RB", "RB2": "backup RB", "WR1": "WR1", "WR2": "WR2", "WR3": "WR3 and deeper", "TE": "TE"}
SHORT = {"pass": "Pass", "rush": "Rush", "recyds": "Rec yds", "rec": "Catches"}


# ------------------------------------------------------------------ history file
COLS = ["pl", "tm", "op", "gm", "gl", "pos", "rl", "rg", "st", "ln", "ask", "p", "pr", "pp", "hs", "dh", "rk", "sc", "proj", "rank"]


def load_history():
    """data/history.json stores each week's rows as compact arrays (column names once)."""
    if not os.path.exists(HIST):
        return {"version": 2, "weeks": {}}
    h = json.load(open(HIST))
    for wk in h["weeks"].values():
        wk["rows"] = [dict(zip(h.get("cols", COLS), r)) for r in wk["rows"]]
    return h


def save_history(h):
    os.makedirs(os.path.dirname(HIST), exist_ok=True)
    out = {"version": 2, "cols": COLS, "weeks": {}}
    for k, wk in h["weeks"].items():
        out["weeks"][k] = dict(wk, rows=[[r.get(c) for c in COLS] for r in wk["rows"]])
    s = json.dumps(out, separators=(",", ":")).replace("],[", "],\n[")
    open(HIST, "w").write(s)


def _open_game(gdate, now):
    """True if the game hasn't kicked off yet, so its prices are pre-game."""
    d = dt.date.fromisoformat(gdate)
    if d > now.date():
        return True
    if d < now.date():
        return False
    cutoff = 17 if d.weekday() in (0, 3) else 10                 # Mon/Thu night games, otherwise morning
    return now.hour < cutoff


def snapshot(data, out, now=None):
    """Save this run's scored props. Rows for a game are replaced only while that game hasn't started,
    so a Thursday run keeps the TNF picks and the Saturday run sets the Sunday/Monday ones."""
    if now is None:
        now = dt.datetime.fromisoformat(data["generated"]) if data.get("generated") else dt.datetime.now(PT)
    if now.tzinfo is None:
        now = now.replace(tzinfo=PT)
    now = now.astimezone(PT)
    hist = load_history()
    key = f'{data["season"]}-{data["week"]}'
    wk = hist["weeks"].setdefault(key, {"season": data["season"], "week": data["week"], "rows": []})
    fresh = {g["key"] for g in data["games"] if not g.get("date") or _open_game(g["date"], now)}
    if not fresh:
        return hist
    keep = [r for r in wk["rows"] if r["gm"] not in fresh]
    rank = {(r["player"], r["stat"], r["line"]): r["rank"] for r in out["top"]}
    new = []
    for r in out["all"]:
        if r["game"] not in fresh or r.get("stale") or r.get("est") or not (20 <= r["ask"] <= 95):
            continue
        new.append({"pl": r["player"], "tm": r["team"], "op": r["opp"], "gm": r["game"], "gl": r["glabel"],
                    "pos": r["pos"], "rl": r["role"], "rg": r["rg"], "st": r["stat"], "ln": r["line"], "ask": r["ask"],
                    "p": r["pc"], "pr": r["praw"], "pp": r["pp"], "hs": r["hs"], "dh": r["dhs"], "rk": r["rks"],
                    "sc": r["score"], "proj": r["proj"], "cx": r.get("ctx", 0), "rank": rank.get((r["player"], r["stat"], r["line"]), 0)})
    wk["rows"] = keep + new
    wk["updated"] = now.isoformat(timespec="seconds")
    save_history(hist)
    return hist


# ------------------------------------------------------------------ grading
def box_scores(data, overrides):
    """(name, wk) -> stat dict, team totals per game, and set of (team, wk) that played."""
    box, teams, dnp = {}, set(), set()
    for s in data["stats"]:
        box[(engine.norm(s["name"]), s["wk"])] = {"team": s["team"], "pos": s["pos"], "pass_yd": s["pass_yd"],
                                                  "rush_yd": s["rush_yd"], "rec": s["rec"], "rec_yd": s["rec_yd"]}
        teams.add((s["team"], s["wk"]))
    for r in overrides or []:
        k = (engine.norm(r["name"]), int(r["wk"]))
        if str(r["value"]).upper() == "DNP":
            dnp.add(k); box.pop(k, None)
        elif k in box:
            box[k][r["field"]] = float(r["value"])
    tot = {}
    for (n, wk), s in box.items():
        t = tot.setdefault((s["team"], wk), {"pass_yd": 0, "rush_yd": 0, "rec": 0, "rec_yd": 0})
        for f in t:
            t[f] += s.get(f, 0) or 0
    return box, tot, teams, dnp


def why_missed(r, actual, wk, box, tot):
    """One plain-English reason for a miss, and a category used to spot patterns."""
    key = engine.STATKEY[r["st"]]
    name = engine.norm(r["pl"])
    line = r["ln"]
    if actual >= 0.85 * line:
        return "Close call", f"Just short: {actual:g} vs {line}+."
    t_now = tot.get((r["tm"], wk), {}).get(key, 0)
    prior = [w for w in range(1, wk) if (r["tm"], w) in tot]
    if r["pos"] == "QB" or r["st"] == "pass":
        mine = [box[(name, w)].get(key, 0) for w in prior if (name, w) in box]
        avg = sum(mine) / len(mine) if mine else None
        if avg and actual < 0.75 * avg:
            return "Offense down", f"Down game: {actual:g} {STATU[r['st']]} vs his {avg:.0f} average."
    else:
        t_avg = sum(tot[(r["tm"], w)][key] for w in prior) / len(prior) if prior else None
        if t_avg and t_now < 0.8 * t_avg:
            return "Offense down", f"Whole {r['tm']} offense was down: {t_now:g} team {STATU[r['st']]} vs {t_avg:.0f} usual."
        shares = [box[(name, w)].get(key, 0) / tot[(r["tm"], w)][key] for w in prior
                  if (name, w) in box and tot[(r["tm"], w)][key] > 0]
        if shares and t_now > 0:
            s_now, s_avg = actual / t_now, sum(shares) / len(shares)
            if s_now < 0.7 * s_avg:
                return "Usage dropped", f"Teammates got the volume: {s_now:.0%} of team {STATU[r['st']]} vs {s_avg:.0%} usual."
    if r.get("proj") and actual < r["proj"] * 0.75:
        return "Below projection", f"Well under Sleeper's {r['proj']:g} projection with {actual:g}."
    return "Defense held", f"{r['op']} held him to {actual:g}; the matchup signals didn't hold."


def grade(hist, data, overrides):
    """Returns graded rows for every saved week that has finished (newest last)."""
    box, tot, teams, dnp = box_scores(data, overrides)
    done = set(data["completed_weeks"])
    graded = []
    for key, wkd in sorted(hist["weeks"].items(), key=lambda x: (x[1]["season"], x[1]["week"])):
        if wkd["season"] != data["season"] or wkd["week"] not in done:
            continue
        wk = wkd["week"]
        for r in wkd["rows"]:
            g = dict(r, wk=wk)
            name = engine.norm(r["pl"])
            if (r["tm"], wk) not in teams:
                g["res"] = "void"; g["note"] = "Game not in the box scores."
            elif (name, wk) in dnp or (name, wk) not in box:
                g["res"] = "void"; g["note"] = "No stat line, likely inactive. Not counted."
            else:
                a = box[(name, wk)].get(engine.STATKEY[r["st"]], 0) or 0
                g["act"] = a
                g["res"] = "hit" if a >= r["ln"] else "miss"
                if g["res"] == "miss" and r["rank"]:
                    g["cat"], g["note"] = why_missed(r, a, wk, box, tot)
            graded.append(g)
    return graded


# ------------------------------------------------------------------ fitting
def _ladder_weights(rows):
    n = {}
    for r in rows:
        k = (r["wk"], r["pl"], r["st"]); n[k] = n.get(k, 0) + 1
    return [1.0 / n[(r["wk"], r["pl"], r["st"])] for r in rows]


def _ridge_logistic(X, y, w, prior, lam, iters=30):
    import numpy as np
    X, y, w, th0, L = map(np.asarray, (X, y, w, prior, lam))
    th = th0.astype(float).copy()
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ th)))
        g = X.T @ (w * (p - y)) + L * (th - th0)
        H = (X * (w * p * (1 - p))[:, None]).T @ X + np.diag(L)
        step = np.linalg.solve(H, g)
        th -= step
        if np.abs(step).max() < 1e-7:
            break
    return th


def fit(graded):
    import numpy as np
    rows = [r for r in graded if r["res"] in ("hit", "miss")]
    w = _ladder_weights(rows)
    n_lad = round(sum(w))
    learned = {"n_ladders": n_lad, "weeks": sorted({r["wk"] for r in rows}), "weights": dict(engine.DEFAULT_W), "calib": None}
    if n_lad < MIN_LADDERS:
        return learned
    y = [1.0 if r["res"] == "hit" else 0.0 for r in rows]

    # 1. which safety signals actually predicted hits -> score weights
    F = np.array([[r[c] / 100 for _, c, _ in SIGNALS] for r in rows])
    mu, sd = F.mean(0), F.std(0) + 1e-6
    Xs = np.hstack([np.ones((len(rows), 1)), (F - mu) / sd])
    beta = _ridge_logistic(Xs, y, w, [0] * 5, [0.01] + [5.0] * 4)[1:]
    imp = np.clip(beta, 0, None)
    prior_share = np.array([engine.DEFAULT_W[k] for k, _, _ in SIGNALS]) / SAFETY_BUDGET
    alpha = min(TRUST_CAP, n_lad / (n_lad + TRUST_N))
    share = prior_share if imp.sum() <= 0 else (1 - alpha) * prior_share + alpha * imp / imp.sum()
    wts = np.clip(share * SAFETY_BUDGET, W_MIN, W_MAX)
    wts = wts * SAFETY_BUDGET / wts.sum()
    learned["weights"] = {k: round(float(v), 4) for (k, _, _), v in zip(SIGNALS, wts)}
    learned["trust"] = round(alpha, 3)

    # 2. calibration: hit ~ a + b*logit(model) + c*logit(price) + stat offset + role offset
    X = [[1, engine.logit(r["pr"]), engine.logit(r["ask"] / 100)] + [1.0 if r["st"] == s else 0.0 for s in STATS]
         + [1.0 if r["rg"] == g else 0.0 for g in ROLES] for r in rows]
    prior = [0, 1, 0] + [0] * (len(STATS) + len(ROLES))
    lam = [RIDGE["a"], RIDGE["b"], RIDGE["c"]] + [RIDGE["stat"]] * len(STATS) + [RIDGE["role"]] * len(ROLES)
    th = _ridge_logistic(X, y, w, prior, lam)
    learned["calib"] = {"a": round(float(th[0]), 4), "b": round(float(th[1]), 4), "c": round(float(th[2]), 4),
                        "stat": {s: round(float(v), 4) for s, v in zip(STATS, th[3:3 + len(STATS)])},
                        "role": {g: round(float(v), 4) for g, v in zip(ROLES, th[3 + len(STATS):])}}
    return learned


# ------------------------------------------------------------------ report for the Results tab
def _rec(rows):
    h = sum(1 for r in rows if r["res"] == "hit"); m = sum(1 for r in rows if r["res"] == "miss")
    return h, m


def report(graded, learned):
    picks = [r for r in graded if r["rank"]]
    weeks = sorted({r["wk"] for r in picks})
    out = {"weeks": [], "last": None, "calib": [], "learn": None}
    for wk in reversed(weeks):
        P = sorted([r for r in picks if r["wk"] == wk], key=lambda r: -r["sc"])
        live = [r for r in P if r["res"] != "void"]
        h, m = _rec(live)
        profit = sum((10 * (100 / r["ask"] - 1)) if r["res"] == "hit" else -10 for r in live)
        top10 = [r for r in P if r["res"] != "void"][:10]
        tiers = []
        for lbl, lo, hi in (("75+", 75, 999), ("68–74", 68, 75), ("60–67", 60, 68), ("Under 60", -1, 60)):
            T = [r for r in live if lo <= round(r["sc"]) < hi]
            if T:
                th, tm = _rec(T); tiers.append({"label": lbl, "hit": th, "miss": tm})
        st = []
        for s in STATS:
            S = [r for r in live if r["st"] == s]
            if S:
                sh, sm = _rec(S); st.append({"label": SHORT[s], "hit": sh, "miss": sm})
        out["weeks"].append({"week": wk, "hit": h, "miss": m, "void": len(P) - len(live),
                             "exp": round(100 * sum(r["p"] for r in live) / len(live)) if live else 0,
                             "profit": round(profit), "staked": 10 * len(live),
                             "top10": list(_rec(top10)), "tiers": tiers, "stats": st})
    if weeks:
        wk = weeks[-1]
        P = sorted([r for r in picks if r["wk"] == wk], key=lambda r: -r["sc"])
        out["last"] = {"week": wk, "picks": [{"pl": r["pl"], "st": r["st"], "ln": r["ln"], "ask": r["ask"],
                                              "p": round(100 * r["p"]), "sc": round(r["sc"]), "res": r["res"],
                                              "act": r.get("act"), "note": r.get("note", ""), "cat": r.get("cat", ""),
                                              "gl": r["gl"]} for r in P]}
        cats = {}
        for r in P:
            if r.get("cat"):
                cats[r["cat"]] = cats.get(r["cat"], 0) + 1
        out["last"]["cats"] = sorted(cats.items(), key=lambda x: -x[1])
    live = [r for r in picks if r["res"] != "void"]
    for lbl, lo, hi in (("Under 60%", 0, .6), ("60–69%", .6, .7), ("70–79%", .7, .8), ("80%+", .8, 2)):
        B = [r for r in live if lo <= r["p"] < hi]
        if B:
            out["calib"].append({"label": lbl, "n": len(B), "pred": round(100 * sum(r["p"] for r in B) / len(B)),
                                 "act": round(100 * sum(r["res"] == "hit" for r in B) / len(B))})
    L = {"n": learned["n_ladders"], "weeks": learned["weeks"], "changed": bool(learned.get("calib")),
         "min": MIN_LADDERS, "trust": round(100 * learned.get("trust", 0)),
         "weights": [{"name": nm, "prior": round(100 * engine.DEFAULT_W[k]), "now": round(100 * learned["weights"][k])}
                     for k, _, nm in SIGNALS], "adjust": []}
    c = learned.get("calib")
    if c:
        base = engine.calibrate(0.70, 60, "none", "none", learned)
        d = base - 0.70
        if abs(d) >= 0.015:
            L["adjust"].append(f"Overall the model was {'over' if d < 0 else 'under'}confident: a 70% call on a 60¢ line is now treated as {base:.0%}.")
        else:
            L["adjust"].append("Overall the model's percentages were about right, so no overall correction.")
        sig = lambda z: 1 / (1 + math.exp(-z))
        for kind, names, words in (("stat", STATS, STATW), ("role", ROLES, ROLEW)):
            for k in names:
                eff = sig(engine.logit(0.70) + c[kind][k]) - 0.70
                if abs(eff) >= 0.02:
                    what = f"{words[k]} props" if kind == "stat" else f"{words[k]} props"
                    L["adjust"].append(f"{what[0].upper() + what[1:]} hit {'less' if eff < 0 else 'more'} often than predicted, "
                                       f"so their chances are {'trimmed' if eff < 0 else 'raised'} {abs(eff) * 100:.0f} pts.")
    out["learn"] = L
    return out


def prepare(data, overrides):
    hist = load_history()
    graded = grade(hist, data, overrides)
    learned = fit(graded)
    os.makedirs(os.path.dirname(LEARNED), exist_ok=True)
    json.dump(learned, open(LEARNED, "w"), indent=1)
    return (learned if learned.get("calib") else None), report(graded, learned)
