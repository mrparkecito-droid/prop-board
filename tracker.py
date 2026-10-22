"""Grades the featured parlays and the TD parlays, so the Results tab can show whether they actually cash.

Every update, parlay.py records the #1 Safe / Medium / Flyer parlay for each slate (Sunday, TNF, MNF) and the
Safest TD / Cheat Code TD parlays into data/parlay_log.json. A recorded parlay keeps being replaced by the
newest version until one of its games kicks off; from then on it's frozen, so what gets graded is the parlay
as it stood right before its first game started (the same rule as the board's picks).

Once the week's box scores are in, each leg is graded (hit / miss / void if the player had no stat line);
a parlay hits only if every live leg hits.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "data", "parlay_log.json")
SLATES = [("sun", "Sunday"), ("tnf", "TNF"), ("mnf", "MNF")]
TIERS = [("safe", "Safe"), ("medium", "Medium"), ("flyer", "Flyer")]
TD_TIERS = [("safe", "Safest TD"), ("code", "Cheat Code TD")]
KEY = {"pass": "pass_yd", "rush": "rush_yd", "rec": "rec", "recyds": "rec_yd"}


def norm(n):
    n = (n or "").replace(".", "").replace("'", "").replace("’", "").lower()
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


def load(path=LOG):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return {"weeks": {}}


def _put(slot, legs, price, started, now):
    """Replace a recorded parlay unless the one already there has a game that's started."""
    if slot.get("legs") and any(l["g"] in started for l in slot["legs"]):
        return slot
    if not legs:
        return slot
    return {"legs": legs, "price": price, "ts": now}


def record(log, season, week, featured, td, started, price_of, now):
    """featured: {slate: {tier: [parlay, ...]}} from parlay.featured; td: out["td"]; price_of(legs) -> 0..1."""
    W = log["weeks"].setdefault(f"{season}-{week}", {"season": season, "week": week, "props": {}, "td": {}})
    started = set(started or [])
    for sl, _ in SLATES:
        for t, _ in TIERS:
            ps = ((featured or {}).get(sl) or {}).get(t) or []
            if not ps:
                continue
            legs = [{"pl": l["pl"], "tm": l["tm"], "g": l["g"], "gl": l["gl"], "st": l["st"], "ln": l["ln"], "ask": l["ask"]} for l in ps[0]]
            k = f"{sl}:{t}"
            W["props"][k] = _put(W["props"].get(k, {}), legs, round(price_of(ps[0]), 4), started, now)
    if td and td.get("players"):
        by = {x["key"]: x for x in td["players"]}
        for t, _ in TD_TIERS:
            keys = (td.get("parlays") or {}).get(t) or []
            legs = [{"pl": by[k]["pl"], "tm": by[k]["tm"], "g": by[k]["g"], "gl": by[k]["gl"], "st": "td", "ln": 1,
                     "p": by[k]["p"], "q": by[k].get("q")} for k in keys if k in by]
            price = 1.0
            for l in legs:
                price *= (l["q"] / 100) if l.get("q") else l["p"]
            W["td"][t] = _put(W["td"].get(t, {}), legs, round(price, 4), started, now)
    return log


def grade(log, stats, completed):
    """[{week, items: [{kind, name, res, price, legs: [{..., act, res}]}]}], newest week first."""
    box = {}
    for s in stats:
        box[(norm(s["name"]), s["wk"])] = s
    out = []
    for key, W in sorted(log.get("weeks", {}).items(), key=lambda kv: -kv[1]["week"]):
        wk = W["week"]
        if wk not in completed:
            continue
        items = []
        groups = [("props", f"{sn} {tn}", W["props"].get(f"{sl}:{t}")) for sl, sn in SLATES for t, tn in TIERS]
        groups += [("td", tn, W["td"].get(t)) for t, tn in TD_TIERS]
        for kind, name, P in groups:
            if not P or not P.get("legs"):
                continue
            legs, live = [], 0
            for l in P["legs"]:
                s = box.get((norm(l["pl"]), wk))
                if s is None:
                    res, act = "void", None
                elif l["st"] == "td":
                    if "rtd" not in s:
                        res, act = "void", None
                    else:
                        act = (s.get("rtd") or 0) + ((s.get("ctd") or 0) if s.get("pos") != "QB" else 0)
                        res = "hit" if act >= 1 else "miss"
                else:
                    act = s.get(KEY[l["st"]]) or 0
                    res = "hit" if act >= l["ln"] else "miss"
                live += res != "void"
                legs.append(dict(l, act=act, res=res))
            if not live:
                res = "void"
            else:
                res = "miss" if any(l["res"] == "miss" for l in legs) else "hit"
            items.append({"kind": kind, "name": name, "res": res, "price": P.get("price"), "legs": legs})
        if items:
            out.append({"week": wk, "items": items})
    return out
