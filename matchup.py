"""Week-4 lessons: factors added after grading the first full week.

CUSHION      How far over (or under) the line he's been, not just yes/no. Clearing 15+ with 60 is far safer
             than clearing it with 16. Each game becomes a 0-1 "cushion" (how many standard deviations over the
             line), recent games count more, and it's averaged 50/50 with the plain hit rate.
DEPTH        Depth-chart role and expected volume. WR3s, TE2s and backup RBs get few targets/carries, so a
             couple of quiet series sinks them. Role sets a starting penalty; expected targets/carries from the
             usage model can cancel it (a WR3 seeing 7 targets a game is fine) or deepen it.
MATCHUP      How much this defense gives up to this exact role compared with the league average for that role
             (e.g. yards to WR2s), mixed with its overall pass/rush rank. Combined with game script: a soft
             defense AND a game script that forces volume (trailing team throwing) stack.
OFFENSE      Is the offense itself any good at moving the ball through the air / on the ground? Struggling
             offenses drag everyone's props down. A QB in his first season with the team (or first season
             starting) on a struggling offense gets an extra cut: his old numbers don't carry over.
"""
import math

Phi = lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2)))

# how hard each factor can move the score / the model's chance
DEPTH_W, DEPTH_P = 0.10, 0.03
ENV_W, ENV_P = 0.14, 0.04
OFF_W, OFF_P = 0.12, 0.03
ROLE_PEN = {"WR3": -0.3, "WR4": -0.5, "WR5": -0.6, "TE2": -0.4, "TE3": -0.6, "RB2": -0.3, "RB3": -0.5}


def clip(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def cushion(vals, line, sdv):
    """Recency-weighted average of Phi((value - line) / sd). vals: oldest first; -1 means a missed game."""
    xs = [max(v, 0) for v in vals if v is not None]
    if not xs:
        return None, None
    n = len(xs)
    w = [1 + (i / (n - 1) * 0.5 if n > 1 else 0) for i in range(n)]
    c = sum(wi * Phi((x - line) / sdv) for wi, x in zip(w, xs)) / sum(w)
    avg_margin = sum(x - line for x in xs) / n
    return c, avg_margin


def depth(role, pos, stat, uinfo):
    """(-1..0.5, note)"""
    if pos == "QB" or (stat == "rush" and pos != "RB") or stat == "pass":
        return 0.0, ""
    f = ROLE_PEN.get(role, 0.0)
    note = ""
    opp = (uinfo or {}).get("opp")
    if opp is not None:
        if stat in ("rec", "recyds"):
            v = clip((opp - 4.5) / 3, -1, 0.3)
            kind = "targets"
        else:
            v = clip((opp - 10) / 6, -1, 0.3)
            kind = "carries"
        f += v
        if f <= -0.3:
            note = f"Low volume: about {opp:.1f} {kind} a game as the {role}."
        elif f >= 0.2 and ROLE_PEN.get(role):
            note = f"Gets real volume for a {role}: about {opp:.1f} {kind} a game."
    elif f < 0:
        note = f"{role} on the depth chart, so few looks."
    return round(clip(f, -1, 0.3), 2), note


def env(m, sx):
    """Matchup and game script combined; they stack when both point the same way."""
    e = 0.6 * m + 0.6 * sx
    if m > 0 and sx > 0:
        e += 0.4 * m * sx
    elif m < 0 and sx < 0:
        e -= 0.4 * m * sx
    return round(clip(e), 2)


def matchup(allowed_vals, league_avg, rank_s, opp, role_word, stat_word):
    """(-1..1, note). allowed_vals: what this defense allowed to the same role in its games."""
    parts, note = [], ""
    if allowed_vals and league_avg and league_avg > 0:
        ratio = (sum(allowed_vals) / len(allowed_vals)) / league_avg
        parts.append(0.6 * clip((ratio - 1) * 1.5))
        pct = round((ratio - 1) * 100)
        if abs(pct) >= 15:
            note = (f"{opp} gives up {pct}% more {stat_word} to {role_word} than average." if pct > 0 else
                    f"{opp} gives up {-pct}% fewer {stat_word} to {role_word} than average.")
        w_rank = 0.4
    else:
        w_rank = 1.0
    parts.append(w_rank * (2 * rank_s - 1))
    return round(clip(sum(parts)), 2), note


def offense(team, stat, pos, team_pg, league_pg, rank, new_qb, ordinal):
    """(-1..1, note). team_pg/league_pg: per-game passing or rushing yards for the team / league average."""
    if team_pg is None or not league_pg:
        return 0.0, ""
    scale = 50 if stat != "rush" else 35
    f = clip((team_pg - league_pg) / scale) * 0.6
    note = ""
    word = "passing" if stat != "rush" else "rushing"
    if f <= -0.2:
        note = f"{team}'s offense ranks {ordinal(rank)} in {word} ({team_pg:.0f} yds a game)."
    elif f >= 0.25:
        note = f"{team} has a top {word} offense ({ordinal(rank)}, {team_pg:.0f} yds a game)."
    if stat == "pass" and pos == "QB" and new_qb and f < 0:
        f -= 0.3
        note = (note + " " if note else "") + new_qb
    return round(clip(f), 2), note
