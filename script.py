"""Two big-picture factors the box scores alone miss.

GAME SCRIPT (from the point spread and over/under):
  - A big favorite gets a lead and runs the ball to burn clock: lead-back rushing up, its passing down a bit.
  - A big underdog falls behind and throws: its passing and receiving up, its rushing down,
    and its pass-catching backs get more work.
  - The team's implied points (over/under split by the spread) lift or sink every yardage prop a little.
  The effect starts at a 3-point spread and is full strength at 10.

QUARTERBACK (for receivers: WR, TE and RB catches/receiving yards):
  - Who starts this week = the team's QB with the biggest Sleeper passing projection who isn't listed out.
  - Who usually starts = the QB with the most pass attempts in most of this season's games.
  - Same QB: no change.
  - Different QB: the receiver's games with that QB (this season, plus last season if they were teammates)
    decide it. Good history with the backup can cancel the penalty or even help; no history = a clear penalty.
"""

SCRIPT_W = 0.08      # game script moves a score up to +/-8%
QB_W = 0.10          # QB situation moves a receiver's score up to +/-10% (down to -10% with no history together)
P_SCRIPT, P_QB = 0.05, 0.06   # ...and the model's chance by up to these amounts
NO_HISTORY = -0.6


def clip(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def game_script(team, stat, pos, g):
    """(-1..1, [(weight, reason)])"""
    if not g:
        return 0.0, []
    sp, tot = (g.get("spread") or {}).get(team), g.get("total")
    if sp is None:
        return 0.0, []
    rs = []
    mag = clip((abs(sp) - 3) / 7, 0, 1)
    fav = sp < 0
    s = 0.0
    if mag > 0:
        if stat == "rush" and pos == "RB":
            s += mag if fav else -mag
            rs.append((round(s, 2), f"{team} favored by {-sp:g}, should run a lot with a lead" if fav
                       else f"{team} a {sp:g}-pt underdog, may have to abandon the run"))
        elif stat in ("rec", "recyds") and pos == "RB":
            if not fav:
                s += 0.6 * mag; rs.append((round(s, 2), f"{team} a {sp:g}-pt underdog, backs catch more when trailing"))
        elif stat in ("pass", "rec", "recyds"):
            if fav:
                s -= 0.6 * mag; rs.append((round(s, 2), f"{team} favored by {-sp:g}, may not need to throw late"))
            else:
                s += 0.8 * mag; rs.append((round(s, 2), f"{team} a {sp:g}-pt underdog, trailing teams throw more"))
    if tot:
        itt = tot / 2 - sp / 2
        t = clip((itt - 22.5) / 7) * 0.5
        if abs(t) >= 0.1:
            s += t
            rs.append((round(t, 2), f"{team} projected for {itt:.0f} pts (O/U {tot:g})" if t > 0
                       else f"{team} projected for only {itt:.0f} pts (O/U {tot:g})"))
    return round(clip(s), 2), rs


# ------------------------------------------------------------------ quarterbacks
def starters(stats, norm):
    """(team, wk) -> (norm name, display name) of the QB with the most pass attempts that week."""
    best = {}
    for s in stats:
        if s.get("pos") != "QB":
            continue
        k = (s["team"], s["wk"])
        v = (s.get("patt") or 0, s.get("pass_yd") or 0)
        if k not in best or v > best[k][0]:
            best[k] = (v, norm(s["name"]), s["name"])
    return {k: (v[1], v[2]) for k, v in best.items() if v[0][0] > 0}


def usual(team, weeks, st):
    seq = [st[(team, w)] for w in weeks if (team, w) in st]
    if not seq:
        return None
    cnt = {}
    for n in seq:
        cnt[n] = cnt.get(n, 0) + 1
    top = max(cnt.values())
    for n in reversed(seq):          # ties go to the most recent starter
        if cnt[n] == top:
            return n


def this_week(team, proj, out_names, norm):
    qbs = [p for p in proj if p.get("team") == team and p.get("pos") == "QB" and (p.get("pass") or 0) > 0
           and norm(p["name"]) not in out_names]
    if not qbs:
        return None
    p = max(qbs, key=lambda p: p.get("pass") or 0)
    return norm(p["name"]), p["name"]


def receiver_qb(vals_by_wk, team, line, weeks, st, now, prev_vals, prev_st, usual_qb, usual_status):
    """(-1..1, note, info) for a receiver. vals_by_wk: {wk: stat} this season; prev_vals: {wk: stat} last season
    on this same team."""
    if not now or not usual_qb:
        return 0.0, "", None
    if now[0] == usual_qb[0]:
        n = sum(1 for w in weeks if st.get((team, w), (None,))[0] == now[0])
        return 0.0, "", {"qb": now[1], "same": True, "n": n}
    w_now = [vals_by_wk[w] for w in weeks if w in vals_by_wk and st.get((team, w), (None,))[0] == now[0]]
    w_now += [v for w, v in prev_vals.items() if prev_st.get((team, w), (None,))[0] == now[0]]
    allv = [v for v in vals_by_wk.values() if v is not None and v >= 0]
    why = f"{usual_qb[1]} is {usual_status.lower()}" if usual_status in ("Out", "Doubtful") else f"{usual_qb[1]} isn't projected to start"
    if not w_now:
        f = NO_HISTORY
        note = (f"QB change: {why}, so the backup starts; no games together yet." if now[0] == "?" else
                f"QB change: {now[1]} starts ({why}), and they have no games together yet.")
    else:
        avg_all = sum(allv) / len(allv) if allv else 0
        avg_now = sum(w_now) / len(w_now)
        ratio = avg_now / avg_all if avg_all > 0 else 1.0
        f_hist = clip((ratio - 1) * 1.5, -1, 0.5)
        k = min(len(w_now) / 3, 1)
        f = k * f_hist + (1 - k) * NO_HISTORY
        hits = sum(1 for v in w_now if v >= line)
        note = (f"QB change: {now[1]} starts ({why}). With him: cleared {line}+ in {hits} of {len(w_now)} "
                f"({', '.join(f'{v:g}' for v in w_now[-4:])}).")
    return round(clip(f), 2), note, {"qb": now[1], "same": False, "with": len(w_now), "usual": usual_qb[1]}
