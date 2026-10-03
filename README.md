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
3. **build.py** renders `docs/index.html`, which GitHub Pages hosts.

## One-time setup (about 15 minutes)

1. **Make a GitHub account** at github.com if you don't have one.
2. **Create a new repository.** Name it `prop-board`, set it to **Public** (free Pages hosting needs a public repo), and skip the README option.
3. **Upload the files.** On the empty repo page click *uploading an existing file*, then drag in everything from this folder, including the hidden `.github` folder. On a Mac, press Cmd+Shift+. in Finder to show it. Commit.
   - If the `.github` folder won't drag in, create the file by hand: *Add file → Create new file*, name it `.github/workflows/update.yml`, and paste in the contents.
4. **Let the workflow push updates.** Settings → Actions → General → Workflow permissions → *Read and write permissions* → Save.
5. **Turn on the website.** Settings → Pages → Source: *Deploy from a branch* → Branch: `main`, folder `/docs` → Save. After a minute it shows your link: `https://YOUR-USERNAME.github.io/prop-board/`
6. **Run it once now.** Actions tab → *Update prop board* → *Run workflow*. Watch the run; when it's green, refresh your link.

Share that link with friends. It updates itself:
- Thursday 6:00 PM PT
- Saturday 9:00 AM PT
- Sunday 8:00 AM PT

You can also hit *Run workflow* any time for fresh prices.

## Day-to-day

- **Fix a bad stat line:** add a row to `overrides.csv`, e.g. `Tee Higgins,2,rec_yd,95,PFR box score`. Use `DNP` as the value to mark a week a player didn't play. The page footer notes how many corrections are applied.
- **Score a different week:** *Run workflow* and type the week number.
- **Change the model:** the weights and limits are the constants at the top of `engine.py`. Changing them will make the Week 4 regression test fail on purpose. Update `tests/expected_week4_top50.json` if you mean to keep the change.

## Known limits

- Kalshi only lists some ladders a day or two before games. Early-week runs can be thin, and the page lists which games are missing markets.
- Injuries aren't modeled. Check Sunday inactives.
- One rule is a deliberate safety choice: a non-QB with no stat line in a week his team played counts as a miss, not a DNP. If someone was injured, add a `DNP` override.
- Kalshi availability varies by state.
