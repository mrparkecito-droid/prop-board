"""Renders data/out.json into docs/index.html using template.html (the same cards as the Week 4 board)."""
import datetime as dt, html, json, os, sys
import usage

HERE = os.path.dirname(os.path.abspath(__file__))


def ordn(n): return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
def tier(s): return "t1" if s >= 75 else ("t2" if s >= 68 else ("t3" if s >= 60 else "t4"))
def fmt(v): return "DNP" if v is None else ("—" if v < 0 else f"{v:g}")


ROLE = {"QB": "QBs", "RB1": "lead RBs", "RB2": "No. 2 RBs", "WR1": "WR1s", "WR2": "WR2s", "WR3": "WR3s", "TE1": "TE1s", "TE2": "TE2s"}
STATW = {"pass": "pass yds", "rush": "rush yds", "rec": "catches", "recyds": "rec yds"}


def ctx_src(out):
    src = (out.get("meta") or {}).get("ctx_sources")
    if not src:
        return "Context data wasn't available this run, so it had no effect."
    return "This run: " + html.escape("; ".join(f"{k.replace('_', ' ')} {v}" for k, v in src.items())) + "."


def american(c):
    """Kalshi price in cents -> sportsbook odds for buying YES at that price (before Kalshi fees)."""
    if c >= 50:
        return f"−{round(100 * c / (100 - c))}"
    return f"+{round(100 * (100 - c) / c)}"


def cards_html(top, extras=True, tlabel=""):
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
        price = f'<div><dt>Kalshi yes</dt><dd>{r["ask"]}¢</dd></div>'
        whyp = cxbar = ubar = uline = face = krow = ""
        if extras:
            k = f'{r["player"]}~{r["stat"]}~{r["line"]}'
            bid = f'bid {r["bid"]}¢ · ' if r.get("bid") is not None else ""
            krow = (f'\n <div class="krow"><span class="src real">Kalshi quote{" · " + html.escape(tlabel) if tlabel else ""}</span>'
                    f'<small>{bid}ask {r["ask"]}¢</small>'
                    f'<button type="button" class="add" data-k="{html.escape(k)}" aria-pressed="false">+ Add</button></div>')
            if r.get("pid"):
                face = f'<img class="face" src="https://sleepercdn.com/content/nfl/players/thumb/{html.escape(str(r["pid"]))}.jpg" alt="" loading="lazy" onerror="this.remove()">'

            if r.get("pu") is not None:
                ubar = f'\n   <li><span>Usage model</span><meter min="0" max="100" value="{r["pu"]}"></meter><b>{r["pu"]}</b></li>'
            if r.get("uinfo"):
                uline = f'<p class="rk"><b>Usage:</b> {html.escape(usage.usage_line(r["uinfo"], r["pos"]))}</p>'
            q = r.get("qinfo")
            if q:
                uline += (f'<p class="rk"><b>QB:</b> {html.escape(q["qb"])}' + (f' (started {q["n"]} of his games)' if q.get("same") else
                          f' starting instead of {html.escape(q["usual"])} · {q["with"]} game{"s" if q["with"] != 1 else ""} together') + '</p>')
            for lbl, kk in (("Defense matchup", "mxs"), ("Game script", "sxs"), ("Depth & volume", "dfs"), ("Offense", "ofs")):
                if kk in r and not (kk == "dfs" and r["pos"] == "QB"):
                    ubar += f'\n   <li><span>{lbl}</span><meter min="0" max="100" value="{r[kk]}"></meter><b>{r[kk]}</b></li>'
            if q:
                ubar += f'\n   <li><span>QB situation</span><meter min="0" max="100" value="{r.get("qfs", 50)}"></meter><b>{r.get("qfs", 50)}</b></li>'
            if r.get("pn"):
                uline += f'<p class="rk"><b>Last season:</b> cleared {r["line"]}+ in {r["po"]} of {r["pn"]} games</p>'

            price = f'<div><dt>Kalshi</dt><dd>{r["ask"]}%<small>{american(r["ask"])}</small></dd></div>'
            pct = 100 * 0.04 * r.get("ctx", 0)
            pill = f'<span class="cx {"up" if pct > 0 else "dn"}">Context {"+" if pct > 0 else "−"}{abs(pct):.1f}%</span> ' if abs(pct) >= 0.05 else ""
            whyp = f'<p class="why">{pill}{html.escape(r.get("why", ""))}</p>'
            if r.get("news"):
                whyp += f'<p class="news">{html.escape(r["news"])}</p>'
            if r.get("reasons") is not None:
                cxbar = f'\n   <li><span>Game context</span><meter min="0" max="100" value="{r.get("cxs", 50)}"></meter><b>{r.get("cxs", 50)}</b></li>'

        cards.append(f'''<article class="card" data-game="{r["game"]}" data-stat="{r["stat"]}">
 <div class="top">
  <div class="score {tier(r["score"])}" aria-label="Score {r["score"]:.0f}"><b>{r["score"]:.0f}</b><small>#{r["rank"]}</small></div>
  {face}<div class="who"><h3>{html.escape(r["player"])}</h3><p class="tm">{r["team"]} · {r["role"]} · {r["glabel"]}</p>
   <p class="prop">{r["prop"]} <strong>{r["line"]}+</strong></p></div>
 </div>{krow}{whyp}
 <div class="games">{gchips}</div>
 <dl class="kpis">
  <div><dt>Hit rate</dt><dd>{r["over"]}/{r["n"]}</dd></div>
  {price}
  <div><dt>Model</dt><dd>{r["p"]}%</dd></div>
  <div><dt>Sleeper proj</dt><dd>{r["proj"]:g}</dd></div>
  <div><dt>Return</dt><dd class="{"pos" if r["roi"] >= 15 else ("neg" if r["roi"] <= -10 else "")}">{"+" if r["roi"] > 0 else ""}{r["roi"]}%</dd></div>
  <div><dt>D vs line</dt><dd>{(str(r["dhit"]) + "/" + str(r["dn"])) if r["dn"] else "–"}</dd></div>
 </dl>
 <div class="cov">{dline}{rk}{uline}</div>
 {flags}
 <details><summary>Score breakdown</summary>
  <ul class="bars">
   <li><span>Sleeper projection</span><meter min="0" max="100" value="{r.get("ppj", r["pp"])}"></meter><b>{r.get("ppj", r["pp"])}</b></li>{ubar}
   <li><span>Player hit rate</span><meter min="0" max="100" value="{r["hs"]}"></meter><b>{r["hs"]}</b></li>
   <li><span>Defense vs line</span><meter min="0" max="100" value="{r["dhs"]}"></meter><b>{r["dhs"]}</b></li>
   <li><span>Defense rank</span><meter min="0" max="100" value="{r["rks"]}"></meter><b>{r["rks"]}</b></li>
   <li><span>Price value</span><meter min="0" max="100" value="{r["vs"]}"></meter><b>{r["vs"]}</b></li>{cxbar}
  </ul>
 </details>
</article>''')
    opts = "".join(f'<option value="{g}">{html.escape(l)}</option>' for g, l in sorted(games.items(), key=lambda x: x[1]))
    return "\n".join(cards), opts


def money(v):
    return ("+" if v >= 0 else "−") + f"${abs(v):,.0f}"


def pct(h, n):
    return f"{round(100 * h / n)}%" if n else "–"


def rec_rows(items):
    rows = []
    for t in items:
        n = t["hit"] + t["miss"]
        rows.append(f'<li><span>{html.escape(t["label"])}</span><meter min="0" max="100" value="{round(100 * t["hit"] / n) if n else 0}"></meter>'
                    f'<b>{t["hit"]}–{t["miss"]}</b></li>')
    return "".join(rows)


def results_html(track):
    """The Results tab: last week's grade, why picks missed, calibration, and what the model changed."""
    if not track:
        return ""
    L = track.get("learn") or {}
    parts = []
    weeks = track.get("weeks") or []
    if not weeks:
        parts.append('<section class="panel"><h2>No graded weeks yet</h2><p>The board is saved every update and each game\'s picks lock at its kickoff, so what gets graded is the board as it stood right before the game started (all of them are listed in data/locked_picks.csv in the repo). '
                     'Once the games are played, the next update grades every pick against the box scores, shows the record here, '
                     'and starts adjusting the model from what hit and what missed.</p></section>')
    else:
        w = weeks[0]
        n = w["hit"] + w["miss"]
        parts.append(f'''<section class="panel"><h2>Week {w["week"]} results</h2>
 <dl class="tiles">
  <div><dt>Board record</dt><dd>{w["hit"]}–{w["miss"]}</dd></div>
  <div><dt>Hit rate</dt><dd>{pct(w["hit"], n)}</dd></div>
  <div><dt>Model expected</dt><dd>{w["exp"]}%</dd></div>
  <div><dt>$10 on each</dt><dd class="{"pos" if w["profit"] >= 0 else "neg"}">{money(w["profit"])}</dd></div>
  <div><dt>Top 10</dt><dd>{w["top10"][0]}–{w["top10"][1]}</dd></div>
 </dl>
 <p class="sub">{n} graded picks{f", {w['void']} voided (player didn't play)" if w["void"] else ""}. Profit assumes $10 at the posted Kalshi price, before fees.</p>
 <div class="two"><div><h3>By score</h3><ul class="bars rec">{rec_rows(w["tiers"])}</ul></div>
 <div><h3>By stat</h3><ul class="bars rec">{rec_rows(w["stats"])}</ul></div></div>
</section>''')
        last = track.get("last") or {}
        misses = [p for p in last.get("picks", []) if p["res"] == "miss"]
        if misses:
            cats = "".join(f'<span class="tag">{html.escape(c)} ×{k}</span>' for c, k in last.get("cats", []))
            items = "".join(f'''<li><div><b>{html.escape(p["pl"])}</b> <span class="mono">{STATW[p["st"]]} {p["ln"]}+</span> <span class="g miss">{p["act"]:g}</span></div>
  <p>{html.escape(p["note"])}</p><small>Score {p["sc"]} · model {p["p"]}% · {p["ask"]}¢ · {html.escape(p["gl"])}</small></li>''' for p in misses)
            parts.append(f'<section class="panel"><h2>Why picks missed</h2><div class="tags">{cats}</div><ul class="misses">{items}</ul></section>')
        P = last.get("picks", [])
        def gli(i, p):
            cls = "hit" if p["res"] == "hit" else ("miss" if p["res"] == "miss" else "dnp")
            act = "DNP" if p["act"] is None else f'{p["act"]:g}'
            mg = "" if p.get("mg") is None else f'<small class="mg {cls}">{"+" if p["mg"] >= 0 else "−"}{abs(p["mg"]):g}</small>'
            return (f'<li class="{cls}"><span class="rk">{i}</span><span class="g {cls}">{"✓" if cls == "hit" else ("✗" if cls == "miss" else "–")}</span>'
                    f'<span class="who">{html.escape(p["pl"])}<small>{STATW[p["st"]]} {p["ln"]}+ · {html.escape(p["gl"])}</small></span>'
                    f'<span class="act"><b>{act}</b>{mg}</span></li>')
        allp = "".join(gli(i + 1, p) for i, p in enumerate(P))
        nh = sum(p["res"] == "hit" for p in P); nm = sum(p["res"] == "miss" for p in P)
        parts.append(f'''<section class="panel"><h2>Week {last.get("week", "")} top {len(P)}: what hit</h2>
 <p class="sub">The board as it stood right before each game kicked off, best score first. {nh} hit, {nm} missed. The number on the right is what he actually got, and how far over or under the line.</p>
 <div class="gfilter" role="group" aria-label="Show"><button type="button" data-gf="all" aria-pressed="true">All</button><button type="button" data-gf="hit" aria-pressed="false">Hits</button><button type="button" data-gf="miss" aria-pressed="false">Misses</button></div>
 <ul class="graded">{allp}</ul></section>''')
        if track.get("calib"):
            rows = "".join(f'''<li><span>Said {c["label"]}</span><div class="cal"><meter min="0" max="100" value="{c["pred"]}"></meter><meter class="act" min="0" max="100" value="{c["act"]}"></meter></div>
  <b>{c["pred"]}→{c["act"]}%</b><small>{c["n"]}</small></li>''' for c in track["calib"])
            parts.append(f'''<section class="panel"><h2>Do the percentages hold up?</h2><p class="sub">Board picks grouped by the model's chance. Top bar: what it said. Bottom bar: how often they actually hit. All graded weeks.</p>
 <ul class="bars calib">{rows}</ul></section>''')
    if L:
        wrows = "".join(f'''<tr><td>{html.escape(x["name"])}</td><td>{x["prior"]}%</td><td><b>{x["now"]}%</b></td>
  <td class="{"up" if x["now"] > x["prior"] else ("down" if x["now"] < x["prior"] else "")}">{"▲" if x["now"] > x["prior"] else ("▼" if x["now"] < x["prior"] else "·")}</td></tr>''' for x in L["weights"])
        if L.get("changed"):
            lead = (f'Learned from {L["n"]} graded ladders (week{"s" if len(L["weeks"]) > 1 else ""} {", ".join(map(str, L["weeks"]))}). '
                    f'Results now count for {L["trust"]}% of the weights; that share grows each week up to 60%, so one odd week can\'t swing it.')
        else:
            lead = f'Nothing changed yet. The model starts adjusting once at least {L["min"]} props have been graded.'
        adj = "".join(f"<li>{html.escape(a)}</li>" for a in L.get("adjust", []))
        parts.append(f'''<section class="panel"><h2>What the model learned</h2><p class="sub">{lead}</p>
 <table class="wt"><thead><tr><th>Signal</th><th>Start</th><th>Now</th><th></th></tr></thead><tbody>{wrows}</tbody></table>
 {f'<ul class="adj">{adj}</ul>' if adj else ''}
 <p class="sub">Every week it checks which signals actually separated hits from misses and shifts weight toward them, then corrects the model's chances by stat and by role (lead RB, WR2, and so on) where they ran high or low.</p></section>''')
    if len(weeks) > 1:
        hist = "".join(f'<li><span>Week {w["week"]}</span><b>{w["hit"]}–{w["miss"]}</b><span>{pct(w["hit"], w["hit"] + w["miss"])}</span>'
                       f'<span class="{"pos" if w["profit"] >= 0 else "neg"}">{money(w["profit"])}</span></li>' for w in weeks)
        parts.append(f'<section class="panel"><h2>Week by week</h2><ul class="wbw">{hist}</ul></section>')
    return "\n".join(parts)


def _tlabel(ts):
    try:
        return dt.datetime.fromisoformat(ts).strftime("%a %-I:%M %p")
    except Exception:
        return ""


def board_json(out):
    """The board's props for the build-your-own parlay slip."""
    rows = [{"pl": r["player"], "tm": r["team"], "g": r["game"], "gl": r["glabel"], "st": r["stat"], "ln": r["line"],
             "ask": r["ask"], "q": r.get("mid", r["ask"]), "p": r.get("pc", r["p"] / 100), "id": r.get("pid"),
             "wy": r.get("why", ""), "cx": r.get("ctx", 0), "sc": r["score"]} for r in out["top"]]
    return json.dumps(rows, separators=(",", ":")).replace("</", "<\\/")


def kalshi_json(out):
    k = dict(out.get("kalshi") or {})
    k["tlabel"] = _tlabel(k.get("ts", ""))
    return json.dumps(k, separators=(",", ":")).replace("</", "<\\/")


def kalshi_status(out):
    k = out.get("kalshi") or {}
    K = k.get("K") or {}
    n, real = K.get("n", 0), len(k.get("quoted") or [])
    learned = (f"Expected prices use Kalshi's markup learned from {n} real quote{'s' if n != 1 else ''} "
               f"(about {100 * (2.718281828 ** K.get('k', 0.035) - 1):.1f}% per leg)." if n else
               "Expected prices use a starting markup of about 3.5% per leg until real quotes come in.")
    st = k.get("status")
    if st == "ok":
        return html.escape(f"Real Kalshi prices for {real} of {k.get('asked', 0)} parlays, as of {_tlabel(k.get('ts', ''))}. {learned}")
    if st == "error":
        return html.escape(f"Couldn't get Kalshi quotes on the last update, so every parlay shows the expected Kalshi price. {learned}")
    return html.escape(f"Kalshi quotes are off (no API key yet), so every parlay shows the expected Kalshi price. {learned}")


def render(out, template):
    meta = out.get("meta", {})
    week = meta.get("week", "")
    weeks = meta.get("completed_weeks", [])
    ngames = len(meta.get("games", []))
    cards, opts = cards_html(out["top"], tlabel=_tlabel((out.get("meta") or {}).get("generated", "")))
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
    W = {x["name"]: (x["now"], x["prior"]) for x in ((out.get("track") or {}).get("learn") or {}).get("weights", [])}
    wtxt = lambda nm, d: f"{W.get(nm, (d, d))[0]}%" + ("" if W.get(nm, (d, d))[0] == d else f", started at {d}%")
    legs = json.dumps(out.get("parlay", []), separators=(",", ":")).replace("</", "<\\/")
    vals = {
        "{{W_PROJ}}": wtxt("Sleeper projection", 28),
        "{{W_HIT}}": wtxt("Player hit rate", 20),
        "{{W_DEF}}": wtxt("Defense vs line", 20),
        "{{W_RANK}}": wtxt("Defense rank", 10),
        "{{LEGS}}": legs,
        "{{KALSHI}}": kalshi_json(out),
        "{{KSTATUS}}": kalshi_status(out),
        "{{RESULTS}}": results_html(out.get("track")),
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
        "{{BUILD}}": html.escape(str(meta.get("generated", ""))),
        "{{CARDS}}": cards,
        "{{BOARD}}": board_json(out),
        "{{CTX_SRC}}": ctx_src(out),
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
    # tiny version file the page checks so the Home Screen app reloads itself when there's a new board
    json.dump({"v": str((out.get("meta") or {}).get("generated", ""))}, open(os.path.join(HERE, "docs", "version.json"), "w"))
    print("wrote docs/index.html with", len(out["top"]), "props")
