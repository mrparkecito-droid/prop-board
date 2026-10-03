"""Regression test: feeding the Week 4 2026 snapshot through engine.py must reproduce
the exact top-50 board that was published (same players, lines, order and scores)."""
import json, os, sys
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

if __name__ == "__main__":
    test_week4_reproduces_published_board()
    test_page_cards_match_published_board()
    test_parsers()
    print("PASS: engine reproduces the published Week 4 top 50 exactly")
