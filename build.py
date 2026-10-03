"""Renders data/out.json into docs/index.html using template.html (the same cards as the Week 4 board)."""
import datetime as dt, html, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def ordn(n): return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
def tier(s): return "t1" if s >= 75 else ("t2" if s >= 68 else ("t3" if s >= 60 else "t4"))
def fmt(v): return "DNP" if v is None else ("—" if v < 0 else f"{v:g}")


ROLE = {"QB": "QBs", "RB1": "lead RBs", "RB2": "No. 2 RBs", "WR1": "WR1s", "WR2": "WR2s", "WR3": "WR3s", "TE1": "TE1s", "TE2": "TE2s"}
STATW = {"pass": "pass yds", "rush": "rush yds", "rec": "catches", "recyds": "rec yds"}


def cards_html(top):
    cards, games = [], {}
    for r in top:
        games[r["game"]] = r["glabel"]
        gchips = "".join(f'<span class="g {"dnp" if v is None else ("hit" if v >= r["line"] else "miss")}"><i>W{i+1}</i>{fmt(v)}</span>' for i, v in enumerate(r["vals"]))
        if r["dn"]:
            dchips = "".join(f'<span class="{"ok" if v >= r["line"] else "no"}" title="W{w} {t}">{v:g}</span>' for w, t, v in r["al"])
            dline = f'<p class="allow"><b>Defense vs this line: {r["dhit"]}/{r["dn"]}</b> · {ROLE.get(r["role"], r["role"])} vs {r["opp"]} ({STATW[r["stat"]]}): <span class="al">{dchips}</span></p>'
        else:
            dline = '<p class="allow">QB rushing has no defense-vs-line check; QBs face too many different styles to compare.</p>'
        rk = f'<p class="rk"><b>{r["opp"]} D rank:</b> {ordn(r["most"])} most {r["dstat"]} allowed ({r["per"]:g}/game)</p>'
        flags = "".join(f'<p class="flag">{html.escape(f)}</p>' for f in r["flags"])
        cards.append(f'''<article class="card" data-game="{r["game"]}" data-stat="{r["stat"]}">
 <div class="top">
  <div class="score {tier(r["score"])}" aria-label="Score {r["score"]:.0f}"><b>{r["score"]:.0f}</b><small>#{r["rank"]}</small></div>
  <div class="who"><h3>{html.escape(r["player"])}</h3><p class="tm">{r["team"]} · {r["role"]} · {r["glabel"]}</p>
   <p class="prop">{r["prop"]} <strong>{r["line"]}+</strong></p></div>
 </div>
 <div class="games">{gchips}</div>
 <dl class="kpis">
  <div><dt>Hit rate</dt><dd>{r["over"]}/{r["n"]}</dd></div>
  <div><dt>Kalshi yes</dt><dd>{r["ask"]}¢</dd></div>
  <div><dt>Model</dt><dd>{r["p"]}%</dd></div>
  <div><dt>Sleeper proj</dt><dd>{r["proj"]:g}</dd></div>
  <div><dt>Return</dt><dd class="{"pos" if r["roi"] >= 15 else ("neg" if r["roi"] <= -10 else "")}">{"+" if r["roi"] > 0 else ""}{r["roi"]}%</dd></div>
  <div><dt>D vs line</dt><dd>{(str(r["dhit"]) + "/" + str(r["dn"])) if r["dn"] else "–"}</dd></div>
 </dl>
 <div class="cov">{dline}{rk}</div>
 {flags}
 <details><summary>Score breakdown</summary>
  <ul class="bars">
   <li><span>Sleeper projection</span><meter min="0" max="100" value="{r["pp"]}"></meter><b>{r["pp"]}</b></li>
   <li><span>Player hit rate</span><meter min="0" max="100" value="{r["hs"]}"></meter><b>{r["hs"]}</b></li>
   <li><span>Defense vs line</span><meter min="0" max="100" value="{r["dhs"]}"></meter><b>{r["dhs"]}</b></li>
   <li><span>Defense rank</span><meter min="0" max="100" value="{r["rks"]}"></meter><b>{r["rks"]}</b></li>
   <li><span>Price value</span><meter min="0" max="100" value="{r["vs"]}"></meter><b>{r["vs"]}</b></li>
  </ul>
 </details>
</article>''')
    opts = "".join(f'<option value="{g}">{html.escape(l)}</option>' for g, l in sorted(games.items(), key=lambda x: x[1]))
    return "\n".join(cards), opts


def render(out, template):
    meta = out.get("meta", {})
    week = meta.get("week", "")
    weeks = meta.get("completed_weeks", [])
    ngames = len(meta.get("games", []))
    cards, opts = cards_html(out["top"])
    n = len(weeks)
    word = {1: "one game", 2: "two games", 3: "three games", 4: "four games", 5: "five games"}.get(n, f"{n} games")
    sample = f"Weeks {weeks[0]}–{weeks[-1]} only, so {word} is the whole sample." if n > 1 else f"Week {weeks[0]} only, so one game is the whole sample." if n else ""
    if n > 5:
        sample = f"Weeks {weeks[0]}–{weeks[-1]}."
    notes = meta.get("notes") or []
    market_note = "".join(f"<p>{html.escape(x)}</p>" for x in notes)
    updated = meta.get("generated", "")
    try:
        t = dt.datetime.fromisoformat(updated)
        updated = t.strftime("%a %b %-d, %-I:%M %p PT")
    except Exception:
        pass
    vals = {
        "{{TITLE}}": f"Week {week} Prop Board",
        "{{EYEBROW}}": html.escape(meta.get("eyebrow") or f"Prop cheat sheet · Week {week}"),
        "{{H1}}": f"Week {week} top {len(out['top'])} props",
        "{{NGAMES}}": str(ngames),
        "{{WEEK}}": str(week),
        "{{SAMPLE_NOTE}}": sample,
        "{{MARKET_NOTE}}": market_note,
        "{{OVR_NOTE}}": meta.get("ovr_note", ""),
        "{{LEAGUE_SRC}}": html.escape(meta.get("league_source", "")),
        "{{UPDATED}}": html.escape(updated),
        "{{CARDS}}": cards,
        "{{OPTS}}": opts,
    }
    page = template
    for k, v in vals.items():
        page = page.replace(k, v)
    return page


if __name__ == "__main__":
    out = json.load(open(os.path.join(HERE, "data", "out.json")))
    page = render(out, open(os.path.join(HERE, "template.html")).read())
    os.makedirs(os.path.join(HERE, "docs"), exist_ok=True)
    open(os.path.join(HERE, "docs", "index.html"), "w").write(page)
    print("wrote docs/index.html with", len(out["top"]), "props")
