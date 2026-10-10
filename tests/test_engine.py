"""Regression tests. The Week 4 snapshot must reproduce the saved board exactly (expected_week4_top50.json,
regenerated after the Week 4 review added cushion, depth, matchup, offense and the market blend), so any
accidental change to the scoring shows up here."""
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
    cards, _ = build.cards_html(out["top"], extras=False)
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


def test_context_parsers_and_nudge():
    """ESPN / Open-Meteo / Reddit payloads in the shapes those APIs return, then the capped score nudge."""
    import datetime as dt, context, build
    sb = {"events": [{"competitions": [{"date": "2026-10-04T20:25Z", "neutralSite": False,
          "venue": {"indoor": False, "address": {"city": "Santa Clara"}},
          "competitors": [{"homeAway": "home", "team": {"abbreviation": "SF"}}, {"homeAway": "away", "team": {"abbreviation": "DEN"}}],
          "odds": [{"details": "SF -7.5", "overUnder": 47.5, "spread": -7.5}]}]},
                     {"competitions": [{"date": "2026-10-04T17:00Z", "venue": {"indoor": True},
          "competitors": [{"homeAway": "home", "team": {"abbreviation": "WSH"}}, {"homeAway": "away", "team": {"abbreviation": "IND"}}],
          "odds": [{"details": "EVEN", "overUnder": 44}]}]}]}
    g = context.parse_scoreboard(sb)
    assert g["DENSF"]["spread"] == {"SF": -7.5, "DEN": 7.5} and g["DENSF"]["total"] == 47.5
    assert g["INDWAS"]["spread"] == {"WAS": 0.0, "IND": 0.0}
    hours = [f"2026-10-04T{h:02d}:00" for h in range(24)]
    wx = context.parse_weather({"hourly": {"time": hours, "wind_speed_10m": [18] * 24, "wind_gusts_10m": [30] * 24,
                                "precipitation_probability": [10] * 24, "precipitation": [0] * 24, "temperature_2m": [61] * 24}}, "2026-10-04T20:25Z")
    assert wx["wind"] == 18 and wx["gust"] == 30
    inj = context.parse_injuries({"injuries": [{"displayName": "Denver Broncos", "injuries": [
        {"status": "Out", "athlete": {"displayName": "Pat Surtain II", "position": {"abbreviation": "CB"}, "team": {"abbreviation": "DEN"}}},
        {"status": "Injured Reserve", "athlete": {"displayName": "Old Guy", "position": {"abbreviation": "S"}}}]}]},
        {"Denver Broncos": "DEN"})
    assert inj == {"DEN": [{"name": "Pat Surtain II", "pos": "CB", "status": "Out"}]}
    now = dt.datetime(2026, 10, 3, tzinfo=dt.timezone.utc)
    news = context.parse_news({"articles": [{"headline": "Christian McCaffrey set for full workload", "published": "2026-10-02T12:00:00Z",
                                             "categories": [{"type": "athlete", "description": "Christian McCaffrey"}]}]}, {"christian mccaffrey"}, now)
    assert "christian mccaffrey" in news
    buzz = context.parse_reddit([{"data": {"children": [{"data": {"title": "Christian McCaffrey smash spot"}}] * 4}}], {"christian mccaffrey"})
    assert buzz == {"christian mccaffrey": 4}
    C = {"games": g, "inj": inj, "news": news, "buzz": buzz, "trend": {}}
    g["DENSF"]["wx"] = wx
    ctx, rs, st = context.assess({"game": "DENSF", "team": "SF", "opp": "DEN", "stat": "rush", "pos": "RB", "player": "Christian McCaffrey"}, C)
    assert ctx > 0
    import script
    sx, srs = script.game_script("SF", "rush", "RB", g["DENSF"])
    assert sx > 0 and any("favored" in t for _, t in srs)
    sx2, _ = script.game_script("DEN", "recyds", "WR", g["DENSF"])
    sx3, _ = script.game_script("DEN", "rush", "RB", g["DENSF"])
    assert sx2 > 0 and sx3 < 0, "underdog should throw more and run less"
    ctx2, rs2, _ = context.assess({"game": "DENSF", "team": "SF", "opp": "DEN", "stat": "pass", "pos": "QB", "player": "Brock Purdy"}, C)
    assert any("Windy" in t for _, t in rs2) and any("Surtain" in t for _, t in rs2)
    assert context.assess({"game": "DENSF", "team": "SF", "opp": "DEN", "stat": "pass", "pos": "QB", "player": "X"}, None) == (0.0, [], None)
    # the nudge is capped at +/-4% and never reshuffles which rung of a ladder is best
    data = json.load(open(os.path.join(HERE, "fixture_week4.json")))
    ov = json.load(open(os.path.join(HERE, "fixture_overrides.json")))
    base = {(r["player"], r["stat"]): r for r in engine.run(data, ov)["top"]}
    data["context"] = C
    for r in engine.run(data, ov)["top"]:
        b = base.get((r["player"], r["stat"]))
        if b:
            assert abs(r["score"] / b["score"] - 1) <= 1.04 * 1.08 - 1 + 1e-3   # context +/-4% x game script +/-8%
            assert r["why"]
    assert build.american(75) == "−300" and build.american(40) == "+150"


def test_usage_and_last_season():
    """Usage model + last season: off when the data lacks them (board above unchanged), sane when present."""
    import copy, random, usage, build
    data = json.load(open(os.path.join(HERE, "fixture_week4.json")))
    ov = json.load(open(os.path.join(HERE, "fixture_overrides.json")))
    base = {(r["player"], r["stat"]): r["score"] for r in engine.run(copy.deepcopy(data), ov)["top"]}
    random.seed(4)
    for s in data["stats"]:
        s["tgt"] = round(s["rec"] / 0.68) if s["rec"] else 0
        s["att"] = round(s["rush_yd"] / 4.4) if s["rush_yd"] > 0 else 0
        s["patt"] = round(s["pass_yd"] / 7) if s["pass_yd"] > 0 else 0
        s["snp"], s["tsnp"] = random.randint(40, 65), 65
    data["prev_stats"] = [dict(s, wk=w) for w in range(1, 18) for s in data["stats"] if s["wk"] == 1]
    out = engine.run(data, ov)
    top = out["top"]
    assert len(top) == 50
    assert all(r["pu"] is not None and 0 <= r["pu"] <= 100 for r in top)
    assert sum(1 for r in top if r["pn"]) >= 40
    shared = [k for k in base if k in {(r["player"], r["stat"]) for r in top}]
    assert len(shared) >= 35, "usage/last season should refine the board, not replace it"
    # last season is capped: 17 old games can't outweigh 3 current ones
    r = top[0]
    assert r["hs"] <= 100
    cards, _ = build.cards_html(top)
    assert "Usage:" in cards and "Last season:" in cards and "Usage model" in cards
    # efficiency shrinkage: one 80-yard catch on 2 targets doesn't make a 40-yards-per-target receiver
    U = usage.build([{"wk": 1, "name": "A B", "team": "X", "tgt": 2, "rec": 1, "rec_yd": 80},
                     {"wk": 1, "name": "C D", "team": "X", "tgt": 8, "rec": 6, "rec_yd": 60}], lambda n: n.lower())
    pu, mu, info = usage.chance("a b", "WR", "X", "recyds", 30, U, {}, engine.sd_for)
    assert mu < 30 and info["share"] == [20]


def test_qb_change_game_script_and_kickoff_lock():
    import copy, datetime as dt, random, script, learn, tempfile
    # QB change: no games with the backup = penalty; strong history with him = no penalty
    st = {("X", 1): ("a", "A"), ("X", 2): ("a", "A"), ("X", 3): ("a", "A")}
    f0, note0, _ = script.receiver_qb({1: 60, 2: 70, 3: 65}, "X", 40, [1, 2, 3], st, ("b", "B"), {}, {}, ("a", "A"), "Out")
    assert f0 == script.NO_HISTORY and "no games together" in note0
    pst = {("X", w): ("b", "B") for w in range(1, 5)}
    f1, note1, _ = script.receiver_qb({1: 60, 2: 70, 3: 65}, "X", 40, [1, 2, 3], st, ("b", "B"), {1: 80, 2: 75, 3: 70, 4: 90}, pst, ("a", "A"), "Out")
    assert f1 >= 0 and "4 of 4" in note1
    f2, _, info = script.receiver_qb({1: 60}, "X", 40, [1], st, ("a", "A"), {}, {}, ("a", "A"), "")
    assert f2 == 0 and info["same"]
    # engine: starting QB ruled out -> that team's receivers drop, unless they have history with the backup
    data = json.load(open(os.path.join(HERE, "fixture_week4.json")))
    ov = json.load(open(os.path.join(HERE, "fixture_overrides.json")))
    for s_ in data["stats"]:
        s_["tgt"] = round(s_["rec"] / 0.68) if s_["rec"] else 0
        s_["att"] = round(s_["rush_yd"] / 4.4) if s_["rush_yd"] > 0 else 0
        s_["patt"] = round(s_["pass_yd"] / 7) if s_["pass_yd"] > 0 else 0
    base = engine.run(copy.deepcopy(data), ov)["all"]
    team = next(r["team"] for r in base if r["stat"] == "recyds" and r["pos"] == "WR")
    qb = max((p for p in data["proj"] if p["team"] == team and p["pos"] == "QB"), key=lambda p: p.get("pass") or 0)
    data["context"] = {"games": {}, "inj": {team: [{"name": qb["name"], "pos": "QB", "status": "Out"}]}}
    after = engine.run(copy.deepcopy(data), ov)["all"]
    b = {(r["player"], r["stat"], r["line"]): r["score"] for r in base}
    hit = [r for r in after if r["team"] == team and r["stat"] in ("rec", "recyds") and r.get("qinfo") and not r["qinfo"]["same"]]
    if True:
        assert hit and all(r["score"] < b[(r["player"], r["stat"], r["line"])] for r in hit if r["qf"] < 0)
        assert all("QB change" in r["qnote"] for r in hit)
    # kickoff lock: a game is editable until 2 minutes before its real kickoff
    now = dt.datetime(2026, 10, 4, 13, 20, tzinfo=dt.timezone(dt.timedelta(hours=-7)))
    assert learn._open_game("2026-10-04", now, "2026-10-04T20:25Z")        # 1:25 PM PT game still open at 1:20
    assert not learn._open_game("2026-10-04", now, "2026-10-04T17:00Z")    # 10 AM game locked


def test_week4_lessons():
    import matchup, learn, copy, datetime as dt
    sd = 12
    big, _ = matchup.cushion([60, 55, 70], 15, sd)
    barely, _ = matchup.cushion([16, 17, 15], 15, sd)
    assert big > barely + 0.2, "clearing by a lot must count more than barely clearing"
    f3, note3 = matchup.depth("WR3", "WR", "recyds", {"opp": 2.5})
    f1, _ = matchup.depth("WR1", "WR", "recyds", {"opp": 8})
    assert f3 < -0.5 and "Low volume" in note3 and f1 > 0
    assert matchup.env(0.8, 0.6) > 0.6 * 0.8 + 0.6 * 0.6, "soft defense + throwing script should stack"
    o_new, n_new = matchup.offense("MIA", "pass", "QB", 160, 220, 30, "First season as a regular starter.", lambda n: f"{n}th")
    o_old, _ = matchup.offense("MIA", "pass", "QB", 160, 220, 30, "", lambda n: f"{n}th")
    assert o_new < o_old < 0 and "First season" in n_new
    # games already kicked off are left off the board
    data = json.load(open(os.path.join(HERE, "fixture_week4.json")))
    ov = json.load(open(os.path.join(HERE, "fixture_overrides.json")))
    g0 = data["games"][0]["key"]
    d2 = dict(copy.deepcopy(data), generated="2026-10-04T12:00:00-07:00",
              context={"games": {g0: {"kick": "2026-10-04T17:00Z", "spread": {}}}, "inj": {}})
    assert all(r["game"] != g0 for r in engine.run(d2, ov)["all"])
    # official board for grading is capped at 50 by score, and wrong-week rows are voided
    assert learn.BOARD_N == 50


def test_balanced_line_picking():
    """Steps up from a needlessly low line when the case is strong, but never below the safety floor or into bad value."""
    data = json.load(open(os.path.join(HERE, "fixture_week4.json")))
    ov = json.load(open(os.path.join(HERE, "fixture_overrides.json")))
    out = engine.run(data, ov)
    stepped = [r for r in out["top"] if r.get("safer")]
    assert stepped, "some ladders should step up"
    for r in stepped:
        assert r["line"] > r["safer"]["line"] and r["pc"] >= engine.BAL_MIN_P and r["roi"] >= 0
        assert r["score"] >= r["safer"]["score"] - engine.BAL_TOL_BASE - engine.BAL_TOL_STRENGTH - 1e-9
    import build
    cards, _ = build.cards_html(out["top"])
    assert "Safer:" in cards and "Bigger payout:" in cards


def test_new_qb_target_share():
    """After a QB change, games with the new QB weigh more in target share, and the card says how it changed."""
    import usage
    stats = []
    for wk, qb in ((1, "A"), (2, "A"), (3, "A"), (4, "B")):
        stats += [{"wk": wk, "name": "W One", "team": "X", "tgt": 8 if qb == "A" else 3, "rec": 5, "rec_yd": 60},
                  {"wk": wk, "name": "W Two", "team": "X", "tgt": 3 if qb == "A" else 8, "rec": 2, "rec_yd": 25}]
    U = usage.build(stats, lambda n: n.lower())
    st = {("X", w): (("a", "A") if w < 4 else ("b", "B")) for w in range(1, 5)}
    p0, _, i0 = usage.chance("w one", "WR", "X", "rec", 4, U, {}, engine.sd_for)
    p1, _, i1 = usage.chance("w one", "WR", "X", "rec", 4, U, {}, engine.sd_for, ("b", "B", "A", st))
    assert p1 < p0 and i1["qbsplit"]["new"] < i1["qbsplit"]["old"]
    assert "With B" in usage.trend_note(i1, "WR") and "down from" in usage.trend_note(i1, "WR")
    _, _, i2 = usage.chance("w two", "WR", "X", "rec", 2, U, {}, engine.sd_for, ("b", "B", "A", st))
    assert "up from" in usage.trend_note(i2, "WR")


def test_featured_parlays():
    """Safe / Medium / Flyer parlays land in their odds ranges, respect the slate filter and the per-game/player rules."""
    import parlay
    data = json.load(open(os.path.join(HERE, "fixture_week4.json")))
    ov = json.load(open(os.path.join(HERE, "fixture_overrides.json")))
    for i, g in enumerate(data["games"]):
        g["date"] = "2026-10-01" if i == 0 else ("2026-10-05" if i == 1 else "2026-10-04")
    out = engine.run(data, ov)
    legs = engine.parlay_legs(out)
    K = dict(parlay.PRIOR)
    F = parlay.featured(legs, K)
    for tid, _, lo, hi, maxl, minc in parlay.TIERS:
        ps = F["all"][tid]
        assert ps, f"no {tid} parlay"
        for p in ps:
            dec = 1 / parlay.expected_price(p, K)
            assert 1 + lo / 100 <= dec <= 1 + hi / 100 + 1e-9 and len(p) <= maxl
            assert len({l["pl"] for l in p}) == len(p)
            games = [l["g"] for l in p]
            assert max(games.count(g) for g in games) <= 2
    # slate filter: only that day's legs
    days = {l["dy"] for l in legs}
    for slate, want in (("tnf", "Thu"), ("sun", "Sun"), ("mnf", "Mon")):
        for ps in F[slate].values():
            for p in ps:
                assert all(l["dy"] == want for l in p)
    assert {"Sun", "Thu", "Mon"} <= days and F["sun"]["safe"]


def test_tnf_review():
    """Catches use a lumpy count model (a normal curve made 2+/3+ catches look too safe); thin single-game slates
    can draw on more of that game's props; every featured leg clears its tier's confidence floor."""
    import usage, parlay
    assert abs(usage.nb_over(3, 2.9) - 0.53) < 0.02          # was ~0.62 with the normal curve
    assert usage.nb_over(2, 3.5) < engine.Phi((3.5 - 2 + 0.5) / engine.sd_for("rec", "WR", 3.5))
    data = json.load(open(os.path.join(HERE, "fixture_week4.json")))
    ov = json.load(open(os.path.join(HERE, "fixture_overrides.json")))
    out = engine.run(data, ov)
    legs = engine.parlay_legs(out)
    F = parlay.featured(legs, dict(parlay.PRIOR))
    # market check: a low-volume player the model likes far more than Kalshi gets pulled toward Kalshi's price
    checked = [r for r in out["all"] if any("Market check" in t for _, t in r["reasons"])]
    assert checked and all(r["pc"] - r["mid"] / 100 < 0.15 for r in checked)
    floors = {t[0]: t[5] for t in parlay.TIERS}
    for tid, ps in F["all"].items():
        for p in ps:
            assert all(parlay.chance(l) >= floors[tid] - 1e-9 for l in p)


if __name__ == "__main__":
    test_week4_reproduces_published_board()
    test_page_cards_match_published_board()
    test_parsers()
    test_learning_grades_and_stays_bounded()
    test_missing_price_filled_from_ladder()
    test_kalshi_signing_and_quote_flow()
    test_context_parsers_and_nudge()
    test_usage_and_last_season()
    test_qb_change_game_script_and_kickoff_lock()
    test_week4_lessons()
    test_balanced_line_picking()
    test_new_qb_target_share()
    test_featured_parlays()
    test_tnf_review()
    print("PASS: engine reproduces the saved Week 4 board exactly; Week-4 lessons (cushion, depth, matchup, offense, started games, balanced line picking, new-QB target share, featured parlays, TNF review), grading, learning, price-fill, Kalshi quote, game-context, usage, QB, game-script and kickoff-lock checks pass")
