"""Usage model: how much work a player gets, and what that says about clearing a line.

Yards are noisy over 3 games; opportunities (targets, carries, pass attempts) and snap share are much steadier.
For every prop this estimates

    expected opportunities  = his recent share of the team's targets/carries x the team's volume per game
    expected stat           = opportunities x his efficiency (catch rate, yards per target/carry/attempt)

Efficiency is shrunk toward a position average so one long play can't inflate it, and last season's
games count at half weight. Recent weeks weigh more (newest week counts double the oldest).
The resulting chance is blended 50/50 with the Sleeper projection chance in engine.py.

Last season is also used for the hit rate: his 2025 games over the same line count at reduced weight
(together worth at most 3 games), so one fluky early-season game can't swing a score.
"""
import math

Phi = lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2)))

# position priors: (value, weight in opportunities)
CATCH = {"WR": (0.64, 15), "TE": (0.70, 15), "RB": (0.78, 15), "QB": (0.5, 15)}
YPT = {"WR": (8.2, 20), "TE": (7.2, 20), "RB": (5.8, 20), "QB": (5.0, 20)}
YPC = {"RB": (4.3, 30), "QB": (5.0, 20), "WR": (6.0, 10), "TE": (4.0, 10)}
YPA = (6.8, 60)
PREV_W = 0.5          # last season's opportunities count half for efficiency
PREV_HIT_CAP = 3.0    # last season's games together worth at most 3 current games for hit rate
PREV_HIT_PER = 0.25   # ...and each one a quarter of a current game


def _wavg(xs):
    if not xs:
        return None
    n = len(xs)
    w = [1 + (i / (n - 1) if n > 1 else 1) for i in range(n)]
    return sum(a * b for a, b in zip(xs, w)) / sum(w)


def has_usage(stats):
    return any("tgt" in s for s in stats)


def build(stats, norm):
    """Per-player weekly usage + team volume per game, from Sleeper box scores."""
    team = {}
    for s in stats:
        t = team.setdefault((s["team"], s["wk"]), {"tgt": 0, "att": 0, "patt": 0})
        t["tgt"] += s.get("tgt", 0) or 0; t["att"] += s.get("att", 0) or 0; t["patt"] += s.get("patt", 0) or 0
    vol = {}
    for (tm, wk), t in team.items():
        v = vol.setdefault(tm, {"tgt": [], "att": [], "patt": []})
        for k in v:
            v[k].append(t[k])
    vol = {tm: {k: sum(x) / len(x) for k, x in v.items()} for tm, v in vol.items()}
    players = {}
    for s in sorted(stats, key=lambda s: s["wk"]):
        t = team[(s["team"], s["wk"])]
        snp, tsnp = s.get("snp") or 0, s.get("tsnp") or 0
        players.setdefault(norm(s["name"]), []).append({
            "wk": s["wk"], "team": s["team"], "tgt": s.get("tgt", 0) or 0, "att": s.get("att", 0) or 0, "patt": s.get("patt", 0) or 0,
            "rec": s.get("rec", 0) or 0, "rec_yd": s.get("rec_yd", 0) or 0, "rush_yd": s.get("rush_yd", 0) or 0, "pass_yd": s.get("pass_yd", 0) or 0,
            "tsh": (s.get("tgt", 0) or 0) / t["tgt"] if t["tgt"] else 0, "ash": (s.get("att", 0) or 0) / t["att"] if t["att"] else 0,
            "snap": snp / tsnp if tsnp else None})
    return {"players": players, "vol": vol}


def totals(stats, norm):
    """Season totals per player (used for last season)."""
    out = {}
    for s in stats:
        d = out.setdefault(norm(s["name"]), {"g": 0, "tgt": 0, "rec": 0, "rec_yd": 0, "att": 0, "rush_yd": 0, "patt": 0, "pass_yd": 0})
        d["g"] += 1
        for k in ("tgt", "rec", "rec_yd", "att", "rush_yd", "patt", "pass_yd"):
            d[k] += s.get(k, 0) or 0
    return out


def _shrink(num, den, prior):
    v, k = prior
    return (num + v * k) / (den + k)


def chance(name_key, pos, team, stat, line, U, prev, sd_for):
    """(usage chance, expected stat, info dict) or None if there's no usage data for him."""
    if not U:
        return None
    G = [g for g in U["players"].get(name_key, []) if g["team"] == team] or U["players"].get(name_key, [])
    if not G:
        return None
    vol = U["vol"].get(team) or {}
    P = (prev or {}).get(name_key) or {}
    pw = lambda k: PREV_W * P.get(k, 0)
    S = lambda k: sum(g[k] for g in G)
    info = {}
    if stat in ("rec", "recyds"):
        sh = _wavg([g["tsh"] for g in G])
        opp = sh * vol.get("tgt", 0)
        info["share"] = [round(100 * g["tsh"]) for g in G]; info["kind"] = "targets"
        if stat == "rec":
            eff = _shrink(S("rec") + pw("rec"), S("tgt") + pw("tgt"), CATCH.get(pos, CATCH["WR"]))
        else:
            eff = _shrink(S("rec_yd") + pw("rec_yd"), S("tgt") + pw("tgt"), YPT.get(pos, YPT["WR"]))
    elif stat == "rush":
        if pos == "QB":
            opp = _wavg([g["att"] for g in G]); info["share"] = [g["att"] for g in G]; info["kind"] = "carries"
        else:
            sh = _wavg([g["ash"] for g in G])
            opp = sh * vol.get("att", 0)
            info["share"] = [round(100 * g["ash"]) for g in G]; info["kind"] = "carries"
        eff = _shrink(S("rush_yd") + pw("rush_yd"), S("att") + pw("att"), YPC.get(pos, YPC["RB"]))
    elif stat == "pass":
        opp = _wavg([g["patt"] for g in G]); info["share"] = [g["patt"] for g in G]; info["kind"] = "attempts"
        eff = _shrink(S("pass_yd") + pw("pass_yd"), S("patt") + pw("patt"), YPA)
    else:
        return None
    if not opp:
        return None
    mu = opp * eff
    pu = Phi((mu - line + 0.5) / sd_for(stat, pos, mu))
    snaps = [g["snap"] for g in G if g["snap"] is not None]
    info["snap"] = round(100 * _wavg(snaps)) if snaps else None
    info["snap_last"] = round(100 * snaps[-1]) if snaps else None
    info["mu"] = round(mu, 1)
    info["note"] = trend_note(info, pos)
    return pu, mu, info


def trend_note(info, pos):
    sh, kind = info["share"], info["kind"]
    pct = kind == "targets" or (kind == "carries" and pos != "QB")
    if len(sh) >= 2:
        first, last = sh[0], sh[-1]
        if pct and last - first >= 8:
            return f"His share of {kind} is up from {first}% to {last}%."
        if pct and first - last >= 8:
            return f"His share of {kind} fell from {first}% to {last}%."
    if info.get("snap") and info.get("snap_last") is not None and info["snap_last"] <= info["snap"] - 15:
        return f"Snaps dropped to {info['snap_last']}% last game."
    return ""


def usage_line(info, pos):
    sh, kind = info["share"], info["kind"]
    pct = kind == "targets" or (kind == "carries" and pos != "QB")
    seq = " → ".join(f"{x}{'%' if pct else ''}" for x in sh)
    what = f"share of team {kind}" if pct else f"{kind} per game"
    s = f"{seq} {what}"
    if info.get("snap") is not None:
        s += f" · {info['snap']}% of snaps"
    return s


def prev_hits(rows, stat_key, line, pos):
    """(weighted over, weighted n, raw over, raw n) from last season's games."""
    vals = [r.get(stat_key, 0) or 0 for r in rows]
    if pos != "QB":
        vals = [v for v, r in zip(vals, rows) if r.get("tgt") or r.get("att") or r.get("snp") or v]
    n = len(vals)
    if not n:
        return 0.0, 0.0, 0, 0
    over = sum(1 for v in vals if v >= line)
    w = min(PREV_HIT_PER * n, PREV_HIT_CAP) / n
    return w * over, w * n, over, n
