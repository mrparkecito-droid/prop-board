"""Regression test: feeding the Week 4 2026 snapshot through engine.py must reproduce
the exact top-50 board that was published (same players, lines, order and scores)."""
import json, math, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import engine

def test_week4_reproduces_published_board():
    data = json.load(open(os.path.join(HERE, "fixture_week4.json")))
    ov = json.load(open(os.path.join(HERE, "fixture_overrides.json")))
    out = engine.run(data, ov)
    got = [[r["player"], r["stat"], r["line"], r["score"]] for r in out["top"]]
    exp = json.load(open(os.path.join(HERE, "expected_week4_top50.json")))
    assert len(got) == len(exp) == 50
    for i, (g, e) in enumerate(zip(got, exp)):
        assert g == e, f"rank {i+1}: got {g}, expected {e}"

def test_page_cards_match_published_board():
    import build
    data = json.load(open(os.path.join(HERE, "fixture_week4.json")))
    out = engine.run(data, json.load(open(os.path.join(HERE, "fixture_overrides.json"))))
    cards, _ = build.cards_html(out["top"])
    published = open(os.path.join(HERE, "published_week4.html")).read()
    assert cards in published, "card markup differs from the published Week 4 board"

def test_parsers():
    import fetch
    assert fetch.cents({"yes_ask": 70}, "yes_ask") == 70
    assert fetch.cents({"yes_ask_dollars": "0.7000"}, "yes_ask") == 70
    assert fetch.event_date("KXNFLPASSYDS-26OCT04DENSF").isoformat() == "2026-10-04"
    assert fetch.T("JAC") == "JAX" and fetch.T("LA") == "LAR"

def test_learning_grades_and_stays_bounded():
    """Snapshot the Week 4 fixture, fake Week 4 box scores, grade, fit: weights stay in bounds,
    misses get reasons, and the learned model still runs. No learning = identical board (tested above)."""
    import copy, random, tempfile, learn
    data = json.load(open(os.path.join(HERE, "fixture_week4.json")))
    ov = json.load(open(os.path.join(HERE, "fixture_overrides.json")))
    tmp = tempfile.mkdtemp()
    learn.HIST = os.path.join(tmp, "history.json"); learn.LEARNED = os.path.join(tmp, "learned.json")
    data = dict(data, generated="2026-10-02T17:00:00-07:00")
    out = engine.run(data, ov)
    learn.snapshot(data, out)
    h = learn.load_history()
    assert h["weeks"]["2026-4"]["rows"], "nothing saved"
    random.seed(1)
    d2 = copy.deepcopy(data); d2["completed_weeks"] = data["completed_weeks"] + [4]
    for p in data["proj"]:
        d2["stats"].append({"wk": 4, "name": p["name"], "pos": p["pos"], "team": p["team"], "opp": p.get("opp", ""),
                            "pass_yd": max(0, random.gauss(p.get("pass") or 0, 60)), "rush_yd": max(0, random.gauss(p.get("rush") or 0, 20)),
                            "rec": max(0, round(random.gauss(p.get("rec") or 0, 1.5))), "rec_yd": max(0, random.gauss(p.get("recyds") or 0, 20))})
    learned, track = learn.prepare(d2, ov)
    g = track["weeks"][0]
    assert g["week"] == 4 and g["hit"] + g["miss"] > 20
    assert all(p["note"] for p in track["last"]["picks"] if p["res"] == "miss")
    if learned:
        assert abs(sum(learned["weights"].values()) - sum(engine.DEFAULT_W.values())) < 1e-3
        assert all(learn.W_MIN - 1e-9 <= v <= learn.W_MAX + 1e-9 for v in learned["weights"].values())
        top = engine.run(data, ov, learned)["top"]
        assert len(top) == 50 and all(0 < r["p"] <= 97 for r in top)

def test_missing_price_filled_from_ladder():
    m = [{"game": "G", "series": "pass", "name": "QB One", "line": l, "ask": a, "bid": None}
         for l, a in ((150, 0), (175, 81), (200, 0), (225, 56), (250, 41), (275, 28), (300, 17), (325, 11), (350, 0))]
    est = engine.fill_missing_asks(m, {})
    got = [est[id(x)] for x in m if x["ask"] == 0]
    assert abs(got[0] - 89) <= 2 and abs(got[1] - 69) <= 2 and abs(got[2] - 6) <= 2, got


def test_kalshi_signing_and_quote_flow():
    """Signatures verify for RSA and Ed25519 keys; a mocked Kalshi returns quotes; RFQs are cancelled, never accepted."""
    import parlay
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import rsa, ed25519, padding
    import base64
    for key in (rsa.generate_private_key(public_exponent=65537, key_size=2048), ed25519.Ed25519PrivateKey.generate()):
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        api = parlay.Kalshi("kid", pem)
        sig = base64.b64decode(api.sign("123GET/trade-api/v2/portfolio/balance"))
        if isinstance(key, rsa.RSAPrivateKey):
            key.public_key().verify(sig, b"123GET/trade-api/v2/portfolio/balance",
                                    padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
        else:
            key.public_key().verify(sig, b"123GET/trade-api/v2/portfolio/balance")
    calls = []

    class Fake(parlay.Kalshi):
        def __init__(self): pass
        def call(self, method, path, body=None, params=None, signed=True):
            calls.append((method, path))
            if path == "/multivariate_event_collections":
                return {"multivariate_contracts": [{"collection_ticker": "KXMVETEST", "size_max": 10,
                        "associated_events": [{"ticker": "EV1"}, {"ticker": "EV2"}]}]}
            if path.startswith("/multivariate_event_collections/"):
                assert all(x["side"] == "yes" for x in body["selected_markets"])
                return {"market_ticker": "MVE-" + str(len(calls)), "market": {"yes_ask_dollars": "0.0000"}}
            if path == "/communications/rfqs":
                assert body["contracts"] == 1 and body["rest_remainder"] is False
                return {"id": "rfq-" + body["market_ticker"]}
            if path == "/communications/quotes":
                return {"quotes": [{"status": "open", "no_bid_dollars": "0.80", "yes_bid_dollars": "0.10"},
                                   {"status": "open", "no_bid_dollars": "0.82", "yes_bid_dollars": "0.12"}]}
            if method == "DELETE":
                return {}
            raise AssertionError(f"unexpected call {method} {path}")
    legs = [{"pl": "A", "st": "rec", "ln": 3, "tk": "M1", "ev": "EV1", "tm": "X", "q": 50, "p": .6},
            {"pl": "B", "st": "rec", "ln": 4, "tk": "M2", "ev": "EV2", "tm": "Y", "q": 40, "p": .5}]
    parlay.WAIT = 0.5
    import time as _t; real_sleep = _t.sleep; parlay.time.sleep = lambda s: None
    try:
        got = parlay.quote_all(Fake(), [legs], log_fn=lambda *a: None)
    finally:
        parlay.time.sleep = real_sleep
    q = got[parlay.key(legs)]
    assert q["src"] == "quote" and abs(q["price"] - 0.18) < 1e-9, q
    assert ("DELETE", "/communications/rfqs/rfq-MVE-2") in calls
    assert not any("accept" in p or "confirm" in p for _, p in calls)
    K = parlay.fit_markup([{"n": 2, "pairs": 0, "sumlogq": math.log(.5 * .4), "price": .22}] * 40)
    assert K["k"] > parlay.PRIOR["k"] or K["b"] > 0      # Kalshi charged more than the legs -> markup goes up


if __name__ == "__main__":
    test_week4_reproduces_published_board()
    test_page_cards_match_published_board()
    test_parsers()
    test_learning_grades_and_stays_bounded()
    test_missing_price_filled_from_ladder()
    test_kalshi_signing_and_quote_flow()
    print("PASS: engine reproduces the published Week 4 top 50 exactly; grading, learning, price-fill and Kalshi quote checks pass")
