# Prop Board

A weekly NFL player-prop board for Kalshi. It runs on its own three times a week and publishes a phone-friendly page you can share with friends.

It uses the same model and the same page as the Week 4 2026 board. `tests/test_engine.py` proves it: feeding that week's data through this code reproduces the published top 50 exactly (same players, lines, order and scores) and the same card markup. Every scheduled run checks this first and stops if it ever drifts.

## What it does

1. **fetch.py** pulls, with no API keys:
   - Kalshi: every open passing, rushing, receiving-yards and receptions ladder for the coming week
   - Sleeper: box-score stats for every completed week, plus this week's projections (Rotowire)
   - Pro Football Reference: pass and rush yards allowed per game for the defense ranks. If PFR blocks the request, it falls back to totals built from Sleeper stats, and the page says so.
2. **engine.py** scores every rung and picks the board:
   - 28% Sleeper projection, 20% player hit rate, 20% defense vs this exact line (same depth-chart role), 10% defense rank, 17% price value, 5% agreement bonus
   - minus 8% per game the player missed the line, 5% per defense miss, 10% for wide bid/ask spreads, 15% for stale prices
   - one best line per player and stat, priced 35¢–88¢, max 2 props per player, top 50
3. **learn.py** keeps the track record and makes the model learn:
   - Every run saves that week's scored props to `data/history.json` (only for games that haven't kicked off).
   - Once a week is played, the next run grades every saved prop against Sleeper box scores and gives each board miss a reason: close call, offense down, usage dropped, below projection, or defense held.
   - It refits how much the projection, hit rate, defense-vs-line and defense-rank signals are worth, based on which ones actually separated hits from misses. It also recalibrates the model's chances overall, by stat and by role. Results are blended with the original weights; their share grows each week and is capped at 60%. Nothing changes until at least 40 props are graded. The current settings are in `data/learned.json`.
4. **parlay.py** prices parlays for the Parlay tab:
   - It builds the best parlays at +150 through +2000 and, if a Kalshi API key is set, gets a real Kalshi quote for each. It creates the combo, requests a 1-contract quote, reads the market makers' quotes (YES price = 1 − best NO bid), then cancels the request. It never accepts a quote, so nothing is bought.
   - Every real quote is logged to `data/quote_log.json`. From those quotes it learns Kalshi's usual combo markup, which prices any parlay that has no quote.
   - Legs with no seller showing get a price estimated from the other lines on the same ladder.
   - To turn quotes on, add repo secrets `KALSHI_KEY_ID` and `KALSHI_PRIVATE_KEY` (the full private key text).
5. **build.py** renders `docs/index.html`, which GitHub Pages hosts. The page has three tabs:
   - **Props:** the board.
   - **Parlay:** an odds slider. It builds up to 3 parlays from board props that hit the target with the highest chance (1 leg per player, max 2 per game, 2–6 legs).
   - **Results:** last week's record, why picks missed, whether the percentages held up, and what the model changed.

## One-time setup (about 15 minutes)

1. **Make a GitHub account** at github.com if you don't have one.
2. **Create a new repository.** Name it `prop-board`, set it to **Public** (free Pages hosting needs a public repo), and skip the README option.
3. **Upload the files.** On the empty repo page click *uploading an existing file*, then drag in everything from this folder, including the hidden `.github` folder. On a Mac, press Cmd+Shift+. in Finder to show it. Commit.
   - If the `.github` folder won't drag in, create the file by hand: *Add file → Create new file*, name it `.github/workflows/update.yml`, and paste in the contents.
4. **Let the workflow push updates.** Settings → Actions → General → Workflow permissions → *Read and write permissions* → Save.
5. **Turn on the website.** Settings → Pages → Source: *Deploy from a branch* → Branch: `main`, folder `/docs` → Save. After a minute it shows your link: `https://YOUR-USERNAME.github.io/prop-board/`
6. **Run it once now.** Actions tab → *Update prop board* → *Run workflow*. Watch the run; when it's green, refresh your link.

Share that link with friends. It updates itself:
- Thursday 1:00 PM PT (for Thursday Night Football)
- Friday 9:00 AM PT (Sunday and Monday games)
- Saturday 9:00 AM PT (Sunday and Monday games)

You can also hit *Run workflow* any time for fresh prices.

## Day-to-day

- **Fix a bad stat line:** add a row to `overrides.csv`, e.g. `Tee Higgins,2,rec_yd,95,PFR box score`. Use `DNP` as the value to mark a week a player didn't play. The page footer notes how many corrections are applied.
- **Score a different week:** *Run workflow* and type the week number.
- **Change the model:** the weights and limits are the constants at the top of `engine.py`. Changing them will make the Week 4 regression test fail on purpose. Learned adjustments don't affect that test. Update `tests/expected_week4_top50.json` if you mean to keep the change.

## Known limits

- Kalshi only lists some ladders a day or two before games. Early-week runs can be thin, and the page lists which games are missing markets.
- Injuries aren't modeled. Check Sunday inactives.
- One rule is a deliberate safety choice: a non-QB with no stat line in a week his team played counts as a miss, not a DNP. If someone was injured, add a `DNP` override.
- Kalshi availability varies by state.

## Game context (testing)

`context.py` pulls point spreads and over/unders (ESPN), kickoff weather (Open-Meteo), injury reports (ESPN),
headlines (ESPN), most-added players (Sleeper) and Reddit buzz. Each card gets a short "why" line, and the score
moves at most ±4% (`CTX_WEIGHT` in engine.py; set it to 0 to turn the nudge off). Players listed out are dropped.
If any source fails, the rest still run; if all fail, the board is scored exactly as before.

## Usage model and last season

`usage.py` turns Sleeper's targets, carries, pass attempts and snaps into an expected stat line
(share of team volume x team volume x efficiency, shrunk toward position averages) and blends that chance
50/50 with the Sleeper projection. Last season's box scores are pulled once into `data/prev_season.json`;
they add to the hit rate at a quarter game each (capped at 3 games total) and to efficiency at half weight.
If either is missing the model falls back to the original formula.

## Schedule

Thu 1 PM PT (TNF), Fri and Sat 9 AM PT, and Sun 8:45 AM PT (injury news before the 10 AM kickoffs).
The cron times are UTC: after daylight time ends (Nov 1) they run an hour earlier in Pacific time.
