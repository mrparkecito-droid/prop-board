"""Parlay pricing: real Kalshi quotes when available, Kalshi's expected price otherwise.

Runs after engine.py. Every update:
  1. Builds the best parlays at a set of odds levels (+150 ... +2000) from the board's legs, using the
     same search as the page's slider.
  2. If KALSHI_KEY_ID / KALSHI_PRIVATE_KEY are set (GitHub secrets), asks Kalshi for a real price on each:
       create the combo market -> request a 1-contract quote (RFQ) -> read market makers' quotes -> cancel.
     A quote is NEVER accepted, so nothing is ever bought. Your YES price = 1 - the best NO bid.
  3. Logs every real quote to data/quote_log.json and refits the "expected Kalshi price" model:
         log(combo price) = log(product of leg fair prices) + b + k * legs + s * same-team pairs
     so parlays without a quote are priced the way Kalshi has actually been pricing them.
  4. Writes the quotes and the fitted model into data/out.json for the page.

Without keys it still runs: every parlay just uses the expected price.
"""
import base64, datetime as dt, json, math, os, time
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "out.json")
LOG = os.path.join(HERE, "data", "quote_log.json")
PT = ZoneInfo("America/Los_Angeles")
API = "https://api.elections.kalshi.com"
BASE = "/trade-api/v2"

LEVELS = [150, 200, 250, 300, 400, 500, 600, 750, 1000, 1250, 1500, 2000]
PER_LEVEL = 5          # candidate parlays quoted per odds level
MAX_QUOTES = 60        # cap per run
BATCH = 5              # RFQs open at once
WAIT = 8               # seconds to wait for market makers
HAIR, MAXL, MINL, BEAM, BAND = 0.97, 6, 2, 300, 1.35
PRIOR = {"b": 0.0, "k": 0.035, "s": 0.04}   # before any real quotes: ~3.5% markup per leg, a bit more for same-team legs
RIDGE = 15                                  # how many quotes it takes to move halfway off the prior
LOG_KEEP = 1500
LAST = os.path.join(HERE, "data", "last_quotes.json")   # quotes from the last full run, reused by quick runs


# ------------------------------------------------------------------ pricing model (mirrored in template.html)
def chance(l):
    return 0.5 * l["p"] + 0.5 * l["q"] / 100


def pairs(legs):
    n = 0
    for i in range(len(legs)):
        for j in range(i + 1, len(legs)):
            n += legs[i]["tm"] == legs[j]["tm"]
    return n


def expected_price(legs, K):
    z = K["b"] + sum(math.log(l["q"] / 100) for l in legs) + K["k"] * len(legs) + K["s"] * pairs(legs)
    return min(math.exp(z), 0.99)


def key(legs):
    return "|".join(sorted(f'{l["pl"]}~{l["st"]}~{l["ln"]}' for l in legs))


SCORE_W = 0.4      # a leg's overall board score nudges its cost: 85 -> like +6% chance, 55 -> like -6%
LOWVOL_W = 0.10    # low-volume legs (WR3s, backup RBs, few expected targets) are boom-or-bust: extra cost in parlays
SLATE_DAYS = {"all": None, "tnf": {"Thu"}, "sun": {"Sun"}, "mnf": {"Mon"}}
# Featured parlays (mirrored in template.html): (id, name, low odds, high odds, max legs)
# (id, name, low odds, high odds, max legs, minimum chance for every leg)
TIERS = [("safe", "Safe", 200, 300, 5, 0.62), ("medium", "Medium", 500, 600, 6, 0.58), ("flyer", "Flyer", 1000, 2000, 8, 0.50)]


def leg_cost(x):
    return max(-math.log(chance(x) * HAIR) - SCORE_W * (x.get("sc", 70) - 70) / 100 + LOWVOL_W * max(0.0, -(x.get("df") or 0)), 0.01)


def search(legs, target, K, game="all", band=BAND, maxl=MAXL, slate="all", minc=0.0):
    """Highest-confidence parlays whose expected Kalshi odds land between +target and band x that."""
    D = 1 + target / 100
    W = math.log(D)
    days = SLATE_DAYS.get(slate)
    L = []
    for x in legs:
        if game != "all" and x["g"] != game:
            continue
        if days and x.get("dy") not in days:
            continue
        if chance(x) < minc:
            continue
        w = -math.log(x["q"] / 100) - K["k"]
        if w > 0.01:
            L.append((x, w, leg_cost(x)))
    per_game = maxl if (game != "all" or len({t[0]["g"] for t in L}) <= 1) else 2
    MAXL_ = maxl
    L.sort(key=lambda t: t[2] / t[1])
    if not L:
        return []
    mr = L[len(L) // 4][2] / L[len(L) // 4][1]     # typical cost per unit of odds, used to judge how far a partial parlay still has to go
    beam, done = [((), 0.0, 0.0, frozenset(), {}, -1)], []
    for d in range(1, MAXL_ + 1):
        nx = []
        for ix, w, c, pl, gm, last in beam:
            for j in range(last + 1, len(L)):
                x, lw, lc = L[j]
                if x["pl"] in pl or gm.get(x["g"], 0) >= per_game:
                    continue
                nw = w + lw
                if nw > W + math.log(band) + 0.25:
                    continue
                g2 = dict(gm); g2[x["g"]] = g2.get(x["g"], 0) + 1
                s = (ix + (j,), nw, c + lc, pl | {x["pl"]}, g2, j)
                if nw >= W - 0.25 and d >= MINL:
                    picked = [L[i][0] for i in s[0]]
                    dec = 1 / expected_price(picked, K)
                    if D <= dec <= D * band:
                        done.append((s[2], picked))
                if nw < W + math.log(band) and d < MAXL_:
                    nx.append(s)
        nx.sort(key=lambda s: s[2] + max(W - s[1], 0) * mr)
        beam = nx[:BEAM]
    done.sort(key=lambda t: (t[0], len(t[1])))
    seen, out = set(), []
    for _, p in done:
        k = key(p)
        if k not in seen:
            seen.add(k); out.append(p)
    return out


def featured(legs, K, n=3):
    """Best parlays for each tier and slate: {slate: {tier: [parlay, ...]}} (mirrored in template.html)."""
    out = {}
    for slate in SLATE_DAYS:
        out[slate] = {}
        for tid, _, lo, hi, maxl, minc in TIERS:
            band = (1 + hi / 100) / (1 + lo / 100)
            out[slate][tid] = search(legs, lo, K, band=band, maxl=maxl, slate=slate, minc=minc)[:n]
    return out


def fit_markup(log):
    """Ridge fit of Kalshi's combo markup on logged real quotes, shrunk toward PRIOR."""
    import numpy as np
    rows = [r for r in log if 0 < r["price"] < 1]
    th0 = np.array([PRIOR["b"], PRIOR["k"], PRIOR["s"]])
    if not rows:
        return dict(PRIOR, n=0)
    X = np.array([[1.0, r["n"], r["pairs"]] for r in rows])
    y = np.array([math.log(r["price"]) - r["sumlogq"] for r in rows])
    lam = RIDGE * np.eye(3)
    th = np.linalg.solve(X.T @ X + lam, X.T @ y + lam @ th0)
    return {"b": round(float(th[0]), 4), "k": round(float(th[1]), 4), "s": round(float(th[2]), 4), "n": len(rows)}


# ------------------------------------------------------------------ Kalshi API (signed)
class Kalshi:
    def __init__(self, key_id, pem):
        import requests
        from cryptography.hazmat.primitives import serialization
        self.key_id = key_id.strip()
        pem = pem.strip().replace("\\n", "\n")
        self.key = serialization.load_pem_private_key(pem.encode(), password=None)
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "prop-board/1.0 (+github actions)"

    def sign(self, text):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        msg = text.encode()
        if isinstance(self.key, Ed25519PrivateKey):
            sig = self.key.sign(msg)
        else:
            sig = self.key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
        return base64.b64encode(sig).decode()

    def call(self, method, path, body=None, params=None, signed=True):
        ts = str(int(time.time() * 1000))
        h = {"Content-Type": "application/json"}
        if signed:
            h.update({"KALSHI-ACCESS-KEY": self.key_id, "KALSHI-ACCESS-TIMESTAMP": ts,
                      "KALSHI-ACCESS-SIGNATURE": self.sign(ts + method + BASE + path)})
        for i in range(3):
            r = self.s.request(method, API + BASE + path, headers=h, json=body, params=params, timeout=20)
            if r.status_code == 429:
                time.sleep(1.5 * (i + 1)); continue
            break
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code} {r.text[:200]}")
        time.sleep(0.12)
        return r.json() if r.text else {}


def collection_for(api, legs, cache):
    """The open combo collection that contains every leg's event."""
    evs = sorted({l["ev"] for l in legs})
    for ev in evs[:1]:
        if ev not in cache:
            j = api.call("GET", "/multivariate_event_collections", params={"status": "open", "associated_event_ticker": ev}, signed=False)
            cache[ev] = j.get("multivariate_contracts", [])
    for c in cache[evs[0]]:
        inside = {a["ticker"]: a for a in c.get("associated_events") or []} or {t: {} for t in c.get("associated_event_tickers", [])}
        if not all(e in inside for e in evs):
            continue
        if c.get("size_max") and len(legs) > c["size_max"]:
            continue
        if c.get("size_min") and len(legs) < c["size_min"]:
            continue
        per = {}
        for l in legs:
            per[l["ev"]] = per.get(l["ev"], 0) + 1
        if any((inside[e] or {}).get("size_max") and n > inside[e]["size_max"] for e, n in per.items()):
            continue
        return c["collection_ticker"]
    return None


def dollars(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def quote_all(api, parlays, log_fn=print):
    """parlays: list of leg lists. Returns {key: {"price", "src", "quotes"}}."""
    got, cache, todo = {}, {}, []
    for legs in parlays:
        try:
            if not all(l.get("tk") and l.get("ev") for l in legs):
                continue
            ct = collection_for(api, legs, cache)
            if not ct:
                log_fn(f"  no combo collection for {key(legs)}"); continue
            sel = [{"market_ticker": l["tk"], "event_ticker": l["ev"], "side": "yes"} for l in sorted(legs, key=lambda l: l["tk"])]
            m = api.call("POST", f"/multivariate_event_collections/{ct}", body={"selected_markets": sel, "with_market_payload": True})
            book = dollars((m.get("market") or {}).get("yes_ask_dollars"))
            todo.append((legs, m["market_ticker"], book))
        except Exception as e:
            log_fn(f"  combo create failed: {e}")
    for i in range(0, len(todo), BATCH):
        batch, open_rfqs = todo[i:i + BATCH], []
        for legs, tk, book in batch:
            try:
                r = api.call("POST", "/communications/rfqs", body={"market_ticker": tk, "contracts": 1, "rest_remainder": False})
                open_rfqs.append((legs, tk, book, r["id"]))
            except Exception as e:
                log_fn(f"  RFQ failed for {tk}: {e}")
                if 0 < book < 1:
                    got[key(legs)] = {"price": book, "src": "book", "quotes": 0}
        deadline = time.time() + WAIT
        best = {}
        while open_rfqs and time.time() < deadline:
            time.sleep(2)
            for legs, tk, book, rid in open_rfqs:
                try:
                    qs = api.call("GET", "/communications/quotes", params={"rfq_id": rid, "rfq_user_filter": "self"}).get("quotes", [])
                    nb = [dollars(q.get("no_bid_dollars")) for q in qs if q.get("status") in (None, "open")]
                    nb = [x for x in nb if 0 < x < 1]
                    if nb:
                        best[rid] = (1 - max(nb), len(nb))
                except Exception as e:
                    log_fn(f"  quote read failed: {e}")
        for legs, tk, book, rid in open_rfqs:
            try:
                api.call("DELETE", f"/communications/rfqs/{rid}")
            except Exception as e:
                log_fn(f"  RFQ cancel failed (it will expire on its own): {e}")
            if rid in best:
                got[key(legs)] = {"price": round(best[rid][0], 4), "src": "quote", "quotes": best[rid][1]}
            elif 0 < book < 1:
                got[key(legs)] = {"price": book, "src": "book", "quotes": 0}
    return got


# ------------------------------------------------------------------ main
def main():
    out = json.load(open(OUT))
    legs = out.get("parlay") or []
    log = json.load(open(LOG)) if os.path.exists(LOG) else []
    K = fit_markup(log)
    now = dt.datetime.now(PT)

    cands, seen = [], set()
    for slate, tiers in featured(legs, K).items():          # the featured parlays get quoted first
        for tid, ps in tiers.items():
            for p in ps[:2]:
                k = key(p)
                if k not in seen:
                    seen.add(k); cands.append(p)
    for lv in LEVELS:
        for p in search(legs, lv, K)[:PER_LEVEL]:
            k = key(p)
            if k not in seen:
                seen.add(k); cands.append(p)
    cands = cands[:MAX_QUOTES]

    if os.environ.get("QUOTES") == "0":
        # quick refresh: don't ask Kalshi again; keep the last full run's quotes for parlays still on the board
        have = {f'{l["pl"]}~{l["st"]}~{l["ln"]}' for l in legs}
        try:
            last = json.load(open(LAST))
        except (OSError, ValueError):
            last = {}
        keep = [q for q in last.get("quoted", []) if all(x in have for x in q["key"].split("|"))]
        out["kalshi"] = {"status": last.get("status", "off"), "K": K, "quoted": keep, "asked": last.get("asked", 0),
                         "ts": last.get("ts", now.isoformat(timespec="seconds"))}
        json.dump(out, open(OUT, "w"), indent=1)
        print(f"quick run: reused {len(keep)} Kalshi quotes from {last.get('ts', 'never')}")
        return

    status, quotes = "", {}
    kid, pem = os.environ.get("KALSHI_KEY_ID"), os.environ.get("KALSHI_PRIVATE_KEY")
    if not (kid and pem):
        status = "off"
        print("No Kalshi API key set; parlays use the expected Kalshi price.")
    else:
        try:
            api = Kalshi(kid, pem)
            print(f"Requesting Kalshi quotes for {len(cands)} parlays")
            quotes = quote_all(api, cands)
            status = "ok"
        except Exception as e:
            status = "error"
            print("Kalshi quoting failed, using expected prices:", e)
    print(f"real prices: {len(quotes)} of {len(cands)}")

    byk = {key(p): p for p in cands}
    quoted = []
    for k, q in quotes.items():
        p = byk[k]
        quoted.append({"key": k, "price": q["price"], "src": q["src"], "nq": q["quotes"]})
        log.append({"ts": now.isoformat(timespec="seconds"), "n": len(p), "pairs": pairs(p),
                    "sumlogq": round(sum(math.log(l["q"] / 100) for l in p), 5), "price": q["price"],
                    "exp": round(expected_price(p, K), 4), "src": q["src"]})
    log = log[-LOG_KEEP:]
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    json.dump(log, open(LOG, "w"), separators=(",", ":"))
    K2 = fit_markup(log)
    out["kalshi"] = {"status": status, "K": K2, "quoted": quoted, "asked": len(cands) if status == "ok" else 0,
                     "ts": now.isoformat(timespec="seconds")}
    json.dump(out, open(OUT, "w"), indent=1)
    json.dump(out["kalshi"], open(LAST, "w"), separators=(",", ":"))
    print(f"markup model: b={K2['b']} k={K2['k']} s={K2['s']} from {K2['n']} logged quotes")


if __name__ == "__main__":
    main()
