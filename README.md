# BTC 10-Strategy Backtester

A dashboard for backtesting candle-based BTC trading strategies, inspired by the
TradeSmart video *"I Built a 10-Strategy System for Polymarket Trading."* Price
data is served from a **local SQLite database** of Binance candles (built once
from Binance's public bulk archive), with a live-API fallback for the newest
bars. The framework is built so you can drop in the other nine strategies over
time — the dashboard renders each strategy's parameter form automatically from
the backend schema.

**All ten of the video's strategies are implemented**, plus additions beyond
them — **Fair Value Gap**, **Fib Retracement**, **Reversal**, **Harmonic
Patterns**, **Momentum Indicators**, **CHoCH**, **Elliott Wave**, **Renko**, **Trend Lines**,
**Support & Resistance** and **Gann Angles** — classic chart-analysis tools built
on the same framework and held to the same evidence bar, along with **Moon Phase**,
kept as a documented null. This also includes **Candlesticks** as a formula-based
addition.

---

## Quick start

```bash
cd /work/david/PolyMarket/03_BTC_10Strategy
cp .env.example .env          # first time only — set PORT for this checkout
./run.sh
# open http://localhost:$PORT   (default 8100)
```

`.env` is gitignored, so the port belongs to the checkout rather than to a
branch — switching branches no longer changes which port the dashboard binds,
and a port tweak can never collide in a merge. Give each parallel checkout its
own `PORT`. An inline override still wins for one-off runs: `PORT=9000 ./run.sh`.

FastAPI + uvicorn are the only dependencies (already present system-wide here).
Everything else — the Binance client, data store, indicators, and backtest
engine — is pure standard-library Python.

### Strategies

In dashboard dropdown order. Every one ships Polymarket-tuned 5m presets; the
linked sections document how each was fitted and what it is worth.

| # | Strategy | Idea |
|---|----------|------|
| 1 | [RSI + BB](#rsi--bb-strategy-1) | Fade the band stretch with RSI at an extreme |
| 2 | Stoch Wick | Stochastic extreme plus a rejection wick |
| 3 | ATR DevExh | Fade an ATR-scaled deviation from the mean |
| 4 | [BB Squeeze](#bb-squeeze-strategy-4) | Trade the coil when Bollinger bandwidth compresses |
| 5 | [Zscore MS](#zscore-ms-strategy-5) | Fade a statistical stretch, optionally Keltner-confirmed |
| 6 | [Regime Switch](#regime-switch-strategy-6) | Detect trending vs ranging, apply the matching playbook |
| 7 | [Volume Exhaustion](#volume-exhaustion-strategy-7) | Fade the climax bar printed on abnormal volume |
| 8 | [Jump Exhaustion](#jump-exhaustion-strategy-8) | Fade the overshoot — the Saturday effect |
| 9 | [CCI Williams](#cci-williams-strategy-9) | Two oscillators must agree on exhaustion |
| 10 | [Multi Horizon](#multi-horizon-strategy-10) | Z-score agreement across three timeframes — **strongest here** |
| + | Fair Value Gap | Trade the retest of a 3-candle price imbalance |
| + | [Fib Retracement](#fib-retracement-beyond-the-video) | Buy the pullback into a measured swing leg |
| + | [Candlesticks](#candlesticks-beyond-the-video) | Nine classic patterns, each written as a formula |
| + | [Reversal](#reversal-beyond-the-video) | Candles, divergence and structure breaks — N of 3 must agree |
| + | [Harmonic Patterns](#harmonic-patterns-beyond-the-video) | Buy the XABCD completion zone — Gartley, Bat, Butterfly, Crab, … |
| + | [Momentum Indicators](#momentum-indicators-beyond-the-video) | Nine oscillators on one scale — fade the stretched composite |
| + | [CHoCH](#choch-change-of-character-beyond-the-video) | Fade the structure break — and tell CHoCH from BOS |
| ✗ | [Moon Phase](#moon-phase-a-measured-negative) | Lunar folklore — **measured, no edge**; kept as a documented null |
| + | [Elliott Wave](#elliott-wave-beyond-the-video) | Count impulse waves mechanically, bet the next leg |
| + | [Renko](#renko-beyond-the-video) | Fade the brick that breaks a one-way run |
| + | Trend Lines | Sloping lines from two swing pivots — fade the break |
| + | [Support & Resistance](#support--resistance-beyond-the-video) | Horizontal levels clustered from pivots — fade the break |
| + | [Gann Angles](#gann-angles-beyond-the-video) | Fan from a pivot — **the angles measure as worthless**; the level earns |
| + | [Oscillators](#oscillators-beyond-the-video) | One banded oscillator, five textbook rules — **only the band entry earns** |
| ⊕ | Combined (Agreement) | Meta-strategy: require N of the above to confirm each other |

## Historical price data (local DB)

Candles are served from a local **SQLite** database (`data/market.db`) instead of
hitting the Binance REST API on every request. The DB stores **1-minute** OHLCV
candles; higher intervals (5m, 15m, 1h, 1d, …) are **resampled from 1m on read**
(byte-exact with Binance's own higher-interval klines).

Build / update it from Binance's public
[data.binance.vision](https://data.binance.vision) bulk archive — monthly zips,
sha256-checksum-verified, no API key:

```bash
# full BTCUSDT 1m history (2017-08 → now): ~230 MB download, ~4.7M rows, ~320 MB DB, ~5 min
python3 -m backend.data.ingest --symbol BTCUSDT --interval 1m --from 2017-08 --to now

python3 -m backend.data.ingest --from 2024-01 --to 2024-06    # just a slice
python3 -m backend.data.ingest --force                         # re-load everything
```

Ingestion is **idempotent and resumable**: completed months are logged and
skipped, so re-running only fetches what's new (schedule it via cron to stay
current). The current month — not yet published as a monthly zip — is pulled from
Binance's daily archives automatically.

Reads are a **hybrid**: history comes from the DB; if a request runs past the
newest ingested candle (e.g. today, before the next ingest), the tail is fetched
live from Binance and spliced on seamlessly. `GET /api/coverage?symbol=BTCUSDT`
reports what's loaded (min/max time + row count).

The DB is gitignored — rebuild it locally with the command above. Set `USE_DB=0`
to bypass the DB and read directly from the Binance API (the original behaviour),
and `MARKET_DB=/path/to.db` to point at a different file.

## Chainlink settlement data (optional)

Polymarket's **5-minute / 15-minute** BTC up-down markets don't settle on
Binance — they settle on **Chainlink Data Streams** (partnership since Sept
2025). Binance is only an ~85% proxy; the disagreement sits right at the strike,
where edge estimates are most fragile. If you have a Data Streams key, you can
record the *true* settlement price into the same SQLite store under the symbol
`BTCUSD_CL`.

Put your credentials in `.env` (gitignored) — see `.env.example` for the keys:
`CHAINLINK_API_KEY`, `CHAINLINK_USER_SECRET`, `CHAINLINK_BTC_FEED_ID`. Then:

```bash
python3 -m backend.data.probe_chainlink       # sanity-check key, feed, retention
python3 -m backend.data.ingest_chainlink       # backfill all retained history, then append
python3 -m backend.data.basis_report --horizon 5m   # measure the Binance↔Chainlink basis
```

Key facts (probed 2026-07): the stream updates at **~1 Hz** (folded into 1m OHLC
on read-resample, same as Binance), but Data Streams only **retains ~3–4 weeks**
of history — so it *cannot* be a deep-history backtest source. The model is:
Binance for deep history, Chainlink recorded forward from now. Reports carry
**no volume** (stored as 0), so volume-based strategies skip `BTCUSD_CL`.

`ingest_chainlink` does backfill *and* incremental append in one path
(`[max(last+1, now−retention), last complete minute]`), so schedule it per minute
via cron to keep the series current — it gap-fills missed runs within the
retention window and is idempotent (`INSERT OR IGNORE`):

```cron
* * * * * flock -n /tmp/cl_ingest.lock -c 'cd /work/david/PolyMarket/03_BTC_10Strategy/BTC_10Strategy_git && /usr/bin/python3 -m backend.data.ingest_chainlink' >> data/chainlink_ingest.log 2>&1
```

## Polymarket market data from the pmqb capture (`ingest_stream`)

The sibling **pmqb** bot records, per tick, both the Chainlink BTC price *and* the
live Polymarket 5-minute UP/DOWN book to `01_EarlyEntry/pmqb/data/stream.jsonl`.
`ingest_stream` folds that single file into the same `market.db`, giving three
things a Binance-only backtest can't:

- **`BTCUSD_CL` 1-minute candles** built from the stream's Chainlink price —
  verified *identical to the cent* to the Data Streams `latest` report, so it
  shares the symbol with `ingest_chainlink` (whichever writes a minute first
  wins; `INSERT OR IGNORE`). This backfills history the Data Streams API no
  longer retains — the capture reaches back **~33 days** (2026-06-20 →).
- **`pm_window`** — one row per 5-minute market: `start_ts` (on the 5m grid, so
  it joins straight to a BTC 5m candle), `market_id`, `slug`, Chainlink
  `start_price` / `end_price`, and `resolved_up`.
- **`pm_quote`** — the tick-level **YES(UP) share price** (mid + book bid/ask),
  ~1/second. This is the real tradeable Polymarket odds, so a backtest can price
  an entry from the actual quote *N seconds into the window* instead of assuming
  a flat 0.5.

```bash
python3 -m backend.data.ingest_stream            # backfill (first run) / append (later)
python3 -m backend.data.ingest_stream --reset    # rescan from offset 0
STREAM_FILE=/path/to/stream.jsonl python3 -m backend.data.ingest_stream
```

It's a **resumable tail**: a byte cursor per source file lives in `stream_cursor`,
so the first run backfills the whole file (~4.4 GB, ~50 s) and each later run
reads only what was appended (sub-second). The still-forming trailing 1-minute
Chainlink candle is held back in `cl_partial` so an incomplete minute is never
sealed. Everything is idempotent.

**Resolution provenance** (`pm_window.resolved_src`): windows the capture logged
a Chainlink outcome for are `'chainlink'` (authoritative). Older windows — before
pmqb logged outcomes — are resolved `'boundary'`, from the *next* window's
Chainlink `start_price`, which **is** Polymarket's settlement reference (they
agree with recorded outcomes 99.6% of the time). Net result: **99.9% of ~9,400
windows carry a UP/DOWN label**, split ~50/50 (no directional bias).

Live ingest is driven by **`run_updaters.sh`** — the single entry point for every
market.db updater (`stream` = Chainlink + Polymarket, `binance` = 1m candles,
`pmdata` = Polymarket L2 order book, `all` = all three). Each job takes its own
`flock` lock, so the fast and slow jobs run at their own cadences without ever
colliding:

```cron
* * * * *    <proj>/run_updaters.sh stream  >> <proj>/data/ingest_stream.log 2>&1
*/30 * * * * <proj>/run_updaters.sh binance >> <proj>/data/binance_ingest.log 2>&1
40 1 * * *   <proj>/run_updaters.sh pmdata  >> <proj>/data/pmdata_ingest.log 2>&1
```

Read the data back with `backend/pm_store.py`: `coverage()`, `windows(lo, hi)`,
`quotes(start_ts)`, and `quote_at(start_ts, elapsed)` — the last returns the YES
price at/just before a given second into a window, i.e. a realistic fill price.

> Note: this live path depends on the pmqb recorder running. If pmqb stops,
> `ingest_stream` simply finds nothing new; `ingest_chainlink` (Data Streams API)
> remains an independent source for `BTCUSD_CL`.

`ingest_stream` also stores the two **model probabilities** pmqb computed each
tick — `pm_quote.p_up_bin` (Binance-fed) and `p_up_chain` (Chainlink-fed) — which
power the PM Edge strategy below. (`p_up_bin` only exists from ~2026-07-07, when
pmqb added the Binance-fed model.)

> **DB location:** the store path comes from `MARKET_DB` in `.env` (a shared
> `…/database/market.db`), and `backend/db.py` now reads `.env` itself — so any
> module run directly (`python -m backend.data.ingest_stream`, a manual query)
> hits the same DB the cron and dashboard use, not a stray local `data/market.db`.

## Polymarket full-history order book from PMData (`ingest_pmdata`)

The pmqb capture above only reaches back to the day the bot started. **PMData**
(`pmdata.dev`) has recorded Polymarket's websocket feeds since **2026-02-13** for
the BTC 5m series, which extends the Polymarket history by ~4 months *and* adds
what pmqb never captured: **real order book depth**.

**Loaded as of 2026-07-28** — `2026-02-13 .. 2026-07-27`, 165 contiguous days:

| | |
|---|---|
| raw L2 events folded | **6,150,005,504** (~37M/day) |
| `pm_l2_quote` / `pm_l2_book` rows | **25,825,544** each |
| `pm_l2_market` windows | **47,201** — 46,217 resolved, 984 undetermined |
| archive on disk | **67.4 GB** (165 zips, ~200 MB/day in Feb → ~600 MB/day in Jul) |
| added to `market.db` | **~13.8 GB** (0.56 GB → 14.3 GB) |
| wall-clock | ~35 min download + **20 min** fold (12 workers) |

```bash
python3 -m backend.data.ingest_pmdata                  # full history: download + fold
python3 -m backend.data.ingest_pmdata --from 2026-07-01 --to 2026-07-27
python3 -m backend.data.ingest_pmdata --download-only   # just fill the archive
python3 -m backend.data.ingest_pmdata --ingest-only     # fold what is already on disk
python3 -m backend.data.ingest_pmdata --status          # coverage report, no work
```

Needs `PMDATA_API_KEY` in `.env`. Two things about the scale drive the whole design:

- **BTC 5m alone is ~37M L2 events a day — 6.15 billion over the full history.**
  Storing those verbatim would be 500 GB+ and days of write time. So the raw
  daily `.zip` archives are kept on disk (67.4 GB, under `PMDATA_ARCHIVE`,
  defaulting beside `market.db`) and SQLite gets the state folded onto a
  **1-second grid** — a 238x row reduction. The archive is the source of truth:
  any other resolution can be re-derived from it later without re-downloading.
- **PMData bills by *day unlocked*, not by request** — and an unlocked day is
  then free forever, across every series *and* data type. That is exactly why the
  archives are never re-fetched: rebuilding the tables costs nothing, but
  re-downloading a day you deleted would cost quota.

Four tables (see `backend/db.py` for the full schema):

- **`pm_l2_quote`** — per `(window, second)`: `bid`/`ask`/`mid`, size resting at
  the best, and **cumulative depth within 1c/5c/10c** of the best on each side.
- **`pm_l2_book`** — the **full ladder** for that second, as a zstd-compressed
  2000-slot `uint32` array (~330 bytes/row). Polymarket quotes a 1c grid but drops
  to 0.1c in the tails, so the ladder is 0.001-resolution: slot `p` is the bid at
  `p/1000`, slot `1000+p` the ask, value is `shares*100`.
- **`pm_l2_market`** — per-window metadata plus the outcome.
  Deliberately *separate* from `pm_window` so a PMData backfill can never disturb
  the Chainlink-sourced windows the existing backtests read.
- **`pmdata_day`** — which archives have been folded in, so re-runs skip them.

**Resolution provenance** (`pm_l2_market.resolved_src`), mirroring `pm_window`'s:

- `'feed'` (**34,682** windows) — the exchange's own `market_resolved` event.
  Authoritative.
- `'terminal'` (**11,535**) — derived, because **PMData did not record
  `market_resolved` before ~2026-03-28**, leaving the first ~6 weeks without a
  reported outcome. A 5m market's YES price converges to ~1.0 (UP) or ~0.0 (DOWN)
  as it settles, so the last two-sided quote implies the result. Backtested
  against the 34,682 windows where the feed *did* report an outcome: the rule
  decides **92.6%** of them at **99.87% accuracy** (42 wrong out of 32,120;
  median terminal mid 0.995 for UP, 0.015 for DOWN).
- `NULL` (**984**, 2.1%) — stayed ambiguous. Left unresolved rather than guessed.

Filter with `WHERE resolved_src='feed'` to use only exchange-reported outcomes.
`--no-derive` skips the derivation entirely.

Against `pm_window` on the overlapping period, split by *both* sources' provenance:

| PMData L2 | `pm_window` | agreement |
|---|---|---|
| `feed` | `chainlink` | **99.80%** (7,014/7,028) |
| `feed` | `boundary` | 97.41% (3,540/3,634) |
| `terminal` | `chainlink` | **100%** (27/27) |

Two independent authoritative sources agree to 99.8%. Nearly all of the residual
sits against `pm_window`'s *derived* `'boundary'` rows — so where the two differ,
`pm_l2_market.resolved_src='feed'` is the better label.

**Book reconstruction.** A `book` event is a full snapshot; `price_change` sets or
clears one level. The feed also reports its own best bid/ask on every
`price_change`, and those are used *verbatim* for the quoted prices — so the
top-of-book columns never depend on replay being perfect. Full snapshots arrive
~3.4x/second, so the replayed depth resyncs continuously rather than drifting.

The fold is vectorised (numpy ladder, one fancy-indexed assignment per second)
because the obvious per-event Python loop runs at ~6.7 µs/event — about 12 CPU-hours
over the full history. Vectorised it is **~18x faster** (~150 ms/market), which is
what makes a 6.15-billion-event backfill a 20-minute job on 12 workers. It was
validated against that plain reference replay: **0 ladder and 0 top-of-book
mismatches** over ~3,000 second-rows.

**Cross-check against the independent pmqb capture** — the two share 2,875,992
seconds of overlap, recorded by different machines from different feeds:

| check | result |
|---|---|
| mean \|PMData bid − pmqb `yes_bid`\| | **0.0072** (under one 1c tick) |
| mean *signed* bid / ask difference | **+0.00000 / +0.00001** (no bias) |
| exact tick match | 69.7% |

The residual is sampling phase, not error: this grid takes end-of-second state
while pmqb sampled whenever its tick landed. The resolution agreement above is
also what confirms the archived book is the **YES(UP)** side, matching
`pm_quote`'s convention.

A verification pass over the loaded data confirms: `bid<=0`, `ask>=1`, and
depth-monotonicity violations (`sz<=d1<=d5<=d10`) are all **0**; prices span
exactly 0.001–0.999; and on sampled second-rows the size quoted at the best
always equals what the ladder holds at that price (**0 mismatches**).

**Caveats worth knowing:**

- **Prices are snapped to the 0.001 grid.** ~8% of feed values arrive with float
  noise (`0.501` as `0.5009998095600838`), which would break `WHERE bid = 0.501`
  and disagree with the ladder's own slotting. The correction is ~2e-7, far below
  a tick.
- **`bid`/`ask` are the feed's own reported best**, not the top of the replayed
  ladder. They disagree ~1% of the time because the exchange batches updates; the
  feed's value is the one that was actually quoted, so it wins.
- **A small number of rows are one-sided** (5.7% have no bid, 5.8% no ask) —
  normal once a market is effectively decided. `bid`/`ask` are NULL there, never 0.
- **`bid >= ask` on 356 rows (0.0014%)** — momentarily crossed in the feed's own
  batched updates. Kept as-is rather than smoothed over.
- **PMData has its own recording gaps.** Every calendar day 2026-02-13..07-27 is
  present, but 9 of them hold fewer than the full 288 windows — 2026-02-13 (84,
  recording began 17:00 UTC), 03-23 (190), 06-17 (282), 06-06 (285), 02-26 /
  04-15 / 04-16 (286), 06-20 / 07-12 (287). Total 47,201 of a possible 47,376
  (99.6%). Re-check with `SELECT data_date, COUNT(*) FROM pm_l2_market GROUP BY
  data_date HAVING COUNT(*) != 288`.

Read it back with `backend/pm_store.py`: `l2_coverage()`, `l2_quotes(start_ts)`,
`l2_quote_at(start_ts, elapsed)`, `l2_book_at(start_ts, elapsed)` (full ladder as
best-first `(price, shares)` lists), and `l2_fill(start_ts, elapsed, shares, side)`
— which **walks the resting book** to price a market order of a given size. That
last one is the point of storing depth: a large order does not fill at the top of
book, and past the best level these markets are often thin.

Daily upkeep runs from the same `run_updaters.sh`. PMData publishes an archive only
once a day has closed, so this job is daily rather than per-minute and is a no-op
when there is nothing new:

```cron
40 1 * * *   <proj>/run_updaters.sh pmdata >> <proj>/data/pmdata_ingest.log 2>&1
```

> Cost: `pm_l2_quote` + `pm_l2_book` add **~13.8 GB** to `market.db` (~84 MB/day),
> roughly 75% of it the ladder blobs. Pass `--no-ladder` to keep only the
> top-of-book + depth-bucket table (~21 MB/day) if that footprint matters; the
> ladder can be folded in later from the archive without spending quota.

## PM Edge — Polymarket market-vs-model strategy

A **Polymarket-native** strategy (not a candle strategy): it trades the 5-minute
UP/DOWN market on the disagreement between the market's YES price and a
price-displacement model. Per window, in an entry band it takes the first tick
where `|model_pUp − yes| ≥ δ`, **follows** the model (bet UP if the model is above
the market, else DOWN), enters at the executable book price (YES at ask / NO at
1−bid), and holds to Chainlink settlement.

```bash
python3 -m backend.data.pm_edge_backtest                       # defaults, full history
python3 -m backend.data.pm_edge_backtest --from 2026-07-07 --delta 0.10
python3 -m backend.data.pm_edge_backtest --model chainlink --entry-from 180 --entry-to 210
# or open the dashboard page:  http://localhost:$PORT/pm-edge
```

**Findings (full Polymarket record; Binance model spans ~20 days from 07-07):**

- **Follow, not fade.** Betting *with* the model beats betting with the market
  against it at every threshold (fade is −0.05 to −0.09/bet). The model leads.
- **Entry timing matters and was swept.** The edge lives in the **middle** of the
  window; the last ~90 s is a graveyard (270–295 s ≈ −0.10/bet on both models —
  near the boundary you'd *fade*, not follow), and the first ~60 s is weak.
  - **Binance model peaks at 120–180 s** (the default).
  - **Chainlink model peaks ~60 s later, at 180–210 s** — consistent with
    Chainlink lagging Binance ~2 s — and is the documented fallback if the
    Binance model feed is unavailable.
- **Best config: Binance · follow · 120–180 s · δ0.12.** ~3,000 bets, ~45% hit
  vs ~43% breakeven, **EV ≈ +0.07 per $1 stake net of a 4% winnings fee**
  (+0.094 gross), ROI ≈ +7% on stake turned over. It stays positive even at an
  8% fee.
- **Mechanism:** the winning bets sit on the *cheaper* side (avg odds ~0.43) at a
  ~45% hit rate — a model-selection / favorite-longshot edge, not a coin-flip
  improvement.

**Caveats:** ~20 days / ~3k bets is a short, single-regime sample; entries assume
you fill at the observed ask (thin longshot books slip on size); and pmqb's own
notes flag a ~1–2 s model-vs-market lag, so live latency is the main risk to the
paper edge. Config lives in `backend/pm_edge.py` (`PMEdgeConfig`); the sweep
scripts that produced these numbers are research artifacts, not in the repo.

## Using the dashboard

1. Pick a **strategy**, **symbol** (default `BTCUSDT`), **interval**, and a
   **start / end** date range.
2. Adjust parameters in the sidebar, or load a named **preset**
   (Default / Aggressive / Conservative).
3. **Run backtest** → fetches candles, generates signals, simulates trades, and
   shows:
   - candlestick chart with entry arrows (▲ long / ▼ short) and win/loss exit dots,
   - stat cards: bars, signals, trades, win rate, total P/L %, avg/trade,
     profit factor, max drawdown, exit-type breakdown, avg hold,
   - a per-trade table.
4. **Load chart** shows the candles alone (no signals) for the chosen range.

## How the backtest works

- A signal fires at a bar's **close**; the trade enters at the **next bar's open**
  (no look-ahead).
- Exit = first of **take-profit** (`tp_atr_mult × ATR`), **stop-loss**
  (`sl_atr_mult × ATR`), or a **time stop** after `max_hold_bars`.
- One position at a time; signals during an open trade are skipped.
- If TP and SL are both inside one bar, the **stop** is assumed hit first.
- Optional `fee_bps` (round-trip) is subtracted from every trade.

These exit/cost controls live in the **Exit / Backtest** parameter group and apply
to every strategy.

## Backtest modes

The top-bar **Mode** selector switches how signals are scored:

- **TP / SL** (default) — the TP/SL/time-stop simulation described above.
- **Polymarket up/down** — models a Polymarket-style **5-minute binary market**.
  Each signal is an *independent* bet placed at the next candle's open and
  resolved purely on that candle's **direction** (close vs open); TP/SL are
  ignored. You set the **Odds** (entry price, cost per $1 share); a WIN pays $1.
  The stats become betting metrics: **hit rate**, **breakeven** (= your odds),
  **EV per bet**, up/down split, and cumulative flat-stake P/L. It's profitable
  only when hit rate > breakeven, i.e. you can enter your side below your odds.
  Backed by `backend/polymarket.py`; works with any strategy.

  BTC 5-min direction is close to a coin flip (~50%), so realistic edges are
  small — treat a few points above 50% as thin, not a sure thing. The BB Squeeze
  **Polymarket 5m (Reversion)** preset is tuned for this mode (interval 5m).

## Multi Horizon (strategy #10)

*Agreement across timeframes.* One lookback only ever tells one story — a close
can look wildly stretched against the last hour and perfectly ordinary against
the last twelve, and a single-window signal cannot tell those apart. This
strategy measures the same **z-score** at three horizons at once:

```
z(h) = (close − SMA(close, h)) / stdev(close, h)
```

Expressed in each horizon's own sigmas, `z` is comparable across horizons *and*
across the 2017-2026 price range — 2σ means the same thing at $4k and $120k.
Defaults of 12/48/144 bars are 1h/4h/12h on the 5m interval.

| Group | Params |
|-------|--------|
| **Horizons** | `h_fast`, `h_mid`, `h_slow` (bars) |
| **Signal** | `z_threshold`, `min_agree` (how many horizons must be stretched the same way), `require_fast` |
| **Volatility Filter** | `vol_atr_length` (also sizes TP/SL), `atr_pct_min`, `atr_pct_max` |
| **Entry Timing** | `require_opposing_bar` ☑, `opposing_bar_min_atr` |
| **Trend Filter** | `use_trend_filter` ☑, `trend_logic`, `ma_type`, `ma_length`, `source` |
| **Decision** | `predict_direction` (Reversion ⋁ Continuation) |

Any horizon stretched the *opposite* way vetoes the bar — that is a conflict, not
a signal.

### Entry timing: don't fade a turn that already happened

The stretch says *what* to bet; it says nothing about *when*. `require_opposing_bar`
asks the second question: the signal bar must close **against** the bet — for a
reversion SHORT, the bar must still be pushing up. If the bar has already turned
your way, the reversal started without you. Those entries are a coin flip:

| Preset | Kept | Kept hit | Dropped | Dropped hit | z |
|--------|-----:|---------:|--------:|------------:|--:|
| PM 5m Volume | 44,971 | 57.63% | 53,947 | 53.69% | +12.42 |
| PM 5m Balanced | 38,497 | 57.78% | 1,845 | 51.22% | +5.56 |
| PM 5m Selective | 20,635 | 57.82% | 1,071 | 50.42% | +4.78 |
| PM 5m Hi Hit | 7,825 | 59.41% | 490 | 52.24% | +3.13 |
| PM 5m Max Hit | 3,511 | 61.63% | 287 | 50.52% | +3.71 |

All five presets enable it. `opposing_bar_min_atr` tightens it further by
demanding a real body on that bar. Bolted onto presets chosen without it, that
knob did nothing — so it stays 0 in four of them. But once the parameters were
re-swept with the filter *inside* the loop, 21 of the 25 best configs asked for
an opposing body of 0.50-0.75×ATR, and *Volume* now uses 0.75.

### Why *not* to skip windows after a loss

Consecutive losing windows are conspicuous, and runs of them really are longer
than chance (loss-runs of ≥3 come out z=+2.3 to +20 above a within-run shuffle).
Skipping a window whose neighbouring predecessor pointed the same way and lost is
the obvious response. It was measured, and it makes things worse.

A run of neighbouring signals exists *because* the bet kept losing — a win
resolves the stretch, so the next bar stops firing. The win is what **ends** the
run, so runs are shaped `loss, loss, …, win`:

| Preset | Runs (≥2) | First window | Middle | Last window |
|---|---:|---:|---:|---:|
| PM 5m Volume | 17,935 | 20.55% | 38.42% | **94.18%** |
| PM 5m Balanced | 9,428 | 11.00% | 13.37% | **84.09%** |
| PM 5m Selective | 5,077 | 11.33% | 13.87% | **83.38%** |
| PM 5m Hi Hit | 1,394 | 14.56% | 26.33% | **75.11%** |
| PM 5m Max Hit | 706 | 15.44% | 31.09% | **75.50%** |

Skipping after a loss keeps the first window of each run and throws away the rest
— including the terminal winner. It removes the group hitting 57-62% and keeps
the group hitting 50-53%. Across 60 configurations (5 presets × 2 readings of
"previous prediction" × 1-3 bar neighbourhoods × this filter on/off) hit rate
falls in 58, by ~1.1pp on *Volume* and 0.3-0.4pp elsewhere, at a cost of 20-35%
of the bets.

Those run positions aren't tradeable — you only know a window was last in its run
after it wins. The predecessor's *outcome* is tradeable, and it says the opposite
of the intuition: a loss means the stretch grew, so the next bet is stronger.
`require_opposing_bar` is that same fact in per-bar form.

### Polymarket presets

Swept over the whole DB (936,829 5m bars), same admission rules as the others.
*Volume* comes from a 672k-combination re-sweep that had `require_opposing_bar`
inside the loop and selected on **2017-2023 only**, so its 2024-26 column is
out-of-sample. The other four keep their original parameters:

| Preset | Bets | Hit | 2024-26 bets | 2024-26 hit | Worst yr | z |
|--------|-----:|----:|-------------:|------------:|---------:|--:|
| **PM 5m Volume** | 44,971 | 57.63% | 13,586 | 55.64% | 50.19% | **32.4** |
| **PM 5m Balanced** | 38,497 | 57.78% | 10,420 | **56.31%** | 50.49% | **30.5** |
| **PM 5m Selective** | 20,635 | 57.82% | 3,002 | 57.76% | 50.81% | 22.5 |
| **PM 5m Hi Hit** | 7,825 | 59.41% | 1,939 | 58.48% | 54.42% | 16.6 |
| **PM 5m Max Hit** | 3,511 | 61.63% | 552 | 61.41% | **55.56%** | 13.8 |

**This is the strongest strategy in the repo.** *Volume* now carries both the
most bets and the highest z (32.4) at 55.64% over 2024-26 — and that number is
out-of-sample. *Balanced* holds 56.31% across 10,420 recent bets, and — unlike
the other strategies' high-hit presets — *Hi Hit* and *Max Hit* rest on real
samples: every year from 2017 to 2026 lands between 54.4% and 63.9%.

Two caveats on the re-sweep. Train hit rate is informative but optimistic: the
top 50 configs by 2017-2023 hit average 63.2% there and 60.8% on 2024-26, so
budget ~3pp of shrinkage on any in-sample figure. And four of the five presets
were already at the out-of-sample frontier — nothing beat *Balanced*, *Selective*,
*Hi Hit* or *Max Hit* at equal bet count (−0.3 to −2.7pp), so only *Volume*
changed. Their 2024-26 numbers remain in-sample and aren't on equal footing with
*Volume*'s.

Three findings came out of the sweep:

- **Reversion only, again.** All 4,304 passing combinations were Reversion, zero
  Continuation. That now holds across three independent strategies — on BTC 5m,
  stretch reverts.
- **The veto matters more than the agreement.** The best configs use
  `min_agree = 1`, so they do *not* demand horizons line up. The edge comes from
  the other half of the rule: no horizon may disagree. Multi-horizon pays off as
  a **conflict filter**, not a confirmation stack.
- **"With Trend" here**, which combined with Reversion means buying a
  down-stretch while price is above the MA — buy the dip in an uptrend. (Volume
  Exhaustion preferred *Against* Trend; different setups, no contradiction.)

## Moon Phase (a measured negative)

*The lunar folklore, implemented so it can be tested rather than argued about.*
"Buy the new moon, sell the full moon" — the claim is that sentiment tracks the
lunar cycle, so the waxing half (new → full) is bullish and the waning half
bearish. There is real academic work behind it (Dichev & Janes 2003; Yuan, Zheng
& Zhu 2006 reported lower equity returns around full moons), though it is widely
held not to survive correction for multiple testing — and none of it concerns
five-minute crypto bars.

**Phase model.** Phase is a pure function of the timestamp, so this needs no
market data and no dependencies. Every new and full moon is computed with Meeus
*Astronomical Algorithms* Ch.49 — validated to under a minute against published
lunations — and a bar's phase is its interpolated position between the
surrounding anchors. Both anchors are used deliberately: a lunation is **not**
symmetric, and the full moon can fall up to ~20 h from the midpoint between two
new moons, so interpolating from new moons alone would misplace the Full Moon
bucket by 22% of a bucket width.

### The result: nothing

Measured directly on 939,513 BTCUSDT 5m bars (2017-08 → 2026-07) — bucket every
bar by phase, record whether the **next** candle closed up. The null is the base
rate, not 50%: BTC's 5m candles close up 50.147% of the time.

| phase bucket | bars | up-rate | vs base | z |
|---|---:|---:|---:|---:|
| New Moon | 117,250 | 50.004% | −0.142pp | −0.97 |
| Waxing Crescent | 117,194 | 50.276% | +0.130pp | +0.89 |
| First Quarter | 117,082 | 50.026% | −0.121pp | −0.83 |
| Waxing Gibbous | 117,124 | 50.161% | +0.015pp | +0.10 |
| Full Moon | 116,677 | 50.145% | −0.001pp | −0.01 |
| Waning Gibbous | 116,530 | 50.120% | −0.026pp | −0.18 |
| Last Quarter | 116,001 | 50.270% | +0.124pp | +0.84 |
| Waning Crescent | 117,113 | 50.170% | +0.024pp | +0.16 |

Not one bucket moves the base rate by 0.15pp, and the largest |z| across all
eight is **0.97** — short of significance before correcting for eight tests, let
alone after. Testing the claim directly: the waxing half's up-rate is 50.096%
and the waning half's is 50.198%, a difference of **−0.102pp (z = −0.99)**. The
folklore predicts a *positive* difference, so the point estimate does not merely
fail to reach significance, it leans the wrong way.

This is a **strong** null. At ~117k bars per bucket the standard error is
0.15pp, so a genuine 0.5pp effect would have shown at z > 3. Nothing is there.

**Why running it scores ~49.7%.** The folklore end-to-end hits 49.708% — and
*inverting* it hits 49.809%. Both below 50%, which looks paradoxical until you
count flat candles: 4,541 bars (0.48%) close exactly at their open and score as
losses either way. That structural cost, not a hidden reverse edge, is the whole
story. Any "moon strategy" that looks profitable on this data is showing you the
selection applied on top of it, not the moon.

### An optimisation was run anyway. It failed instructively.

"Sweep it harder" is the obvious next thought, so it was: **1,530 configs** —
255 non-empty subsets of the 8 phase buckets × 2 directions × 3 trend-filter
modes — under the same train / holdout / unswept protocol as the Reversal
presets.

Best on train: **51.76%** (6,223 bets), *Waxing Long / Against Trend / Waxing
Crescent only*. That looks shippable until the controls run.

**Control 1 — a meaningless cycle does better.** Re-running the identical sweep
with the moon replaced by an arbitrary **31.7-day** cycle gives a best train hit
of **51.92%**, beating the real moon. Whatever the sweep found is a best-of-1,530
order statistic, not an astronomical one.

**Control 2 — the moon adds nothing over the trend filter.** Every top config
used *Against Trend*, which forces side = long below EMA200 / short above it. The
phase subset therefore cannot choose direction, only which bars are taken. Over
the unswept years:

| rule | bets | hit |
|---|---:|---:|
| pure Against Trend, **no moon at all** | 681,818 | 50.82% |
| + the "winning" subset (Waxing Crescent) | 84,803 | 50.76% |
| + the other seven buckets | 597,015 | 50.83% |
| + a fake 31.7-day cycle, 1 of 8 | 85,228 | 50.72% |

The optimised lunar filter scores **worse** than using no lunar filter, while
cutting volume ~8×. The ~50.8% is the mean-reversion edge of the Against-Trend
filter — documented elsewhere in this repo, far too thin to trade after costs,
and nothing to do with the moon.

**No preset ships**, and it is deliberately left out of `combined.py`'s
`SUB_IDS` — a voter with no edge can only dilute an agreement rule. The strategy
is kept because a measured negative is worth more than an untested rumour, and
because the ephemeris is reusable: a daily or weekly horizon, where the original
equity research actually operated, is a different and untested question.

## Reversal (beyond the video)

*Three independent ways of arguing a move is out of participants, with a
configurable agreement threshold.* Rather than one "reversal" rule, this
implements three **detectors** that each vote a direction:

| Detector | Fires when |
|----------|-----------|
| **Candlestick pattern** | Engulfing, hammer / shooting star, morning / evening star, or piercing / dark cloud — required to print **at** an N-bar extreme |
| **Divergence** | Price makes a lower low / higher high that RSI or the MACD histogram fails to confirm, measured between the last two confirmed pivots |
| **Market structure** | A double top / bottom whose neckline just broke, or a break of structure: a lower-low downtrend whose latest swing high gives way |

`min_confirmations` (1–3) turns them from an OR into a consensus, and is clamped
to the number of detectors actually enabled so it can never silently mute the
strategy. `predict_direction` then takes the call at face value (**Reversal**) or
fades it (**Continuation**). A bar with votes on both sides is discarded.

Divergence and structure both rest on fractal pivots, which are only knowable
`pivot_right` bars after they print. Pivots are fed in through a **confirmation
cursor** that admits a pivot only once the scan reaches `j + pivot_right`, so
nothing a signal reads is unavailable in real time. This is verified by a
truncation test: re-running on a series cut at each signal bar reproduces every
signal with zero future bars available.

| Group | Params |
|-------|--------|
| **Candlestick Patterns** | `use_engulfing` ☑, `use_pin_bar` ☑, `use_star` ☑, `use_piercing` ☐, `min_body_ratio`, `min_wick_ratio` |
| **Location** | `use_location` ☑, `swing_lookback`, `extreme_tolerance_atr` |
| **Pivots** | `pivot_left`, `pivot_right`, `max_pivot_gap` |
| **Divergence** | `use_divergence` ☐, `osc_type`, `rsi_length`, `macd_*`, `min_osc_gap` |
| **Structure** | `use_structure` ☐, `structure_pattern`, `retest_tolerance_atr` |
| **Confirmation** | `min_confirmations` |
| **Volatility** | `vol_atr_length`, `atr_pct_min`, `atr_pct_max` |
| **Decision** | `predict_direction` (Reversal \| Continuation) |

### Presets — and what did *not* work

Selected with a **genuine holdout**, unlike the other presets in this repo:
train on 2024-07 → 2025-11, freeze the pick, then score 2025-11 → 2026-07. The
years 2018–2024 were never loaded by the sweep at all and act as a second,
much larger out-of-sample check.

| preset | bets | hit | 2018-23 (unswept) | train | HOLDOUT | worst yr |
|--------|-----:|----:|------:|------:|--------:|---------:|
| PM 5m BOS Volume | 32,630 | 56.60% | 58.08% | 54.02% | 54.83% | 54.07% (2024) |
| PM 5m BOS Balanced | 22,386 | 56.84% | 58.09% | 55.09% | 55.86% | 53.67% (2024) |

#### 1-minute presets

Swept separately, because **the parameters do not carry over**. `pivot_left`
counts *bars*, so the 5m winner's 30 is 150 **minutes**. Run at the default
`pivot_left=3` the whole family reads as dead on 1m tape (BOS/Continuation
scores 49.26% on train); pushing the left window out to the same wall-clock
scale restores it, and the holdout then rises monotonically with it — 51.31% at
`pivot_left=20` up to 53.73% at 300.

| preset | bets | hit | 2018-23 (unswept) | train | HOLDOUT |
|--------|-----:|----:|------:|------:|--------:|
| PM 1m BOS Volume | 67,441 | 52.03% | 53.19% | 51.76% | 53.15% |
| PM 1m BOS Balanced | 58,099 | 52.22% | 53.46% | 51.83% | 53.83% |

**Read these against 48.44%, not 50%.** On 1m tape **3.12%** of candles close
exactly at their open, and a flat candle loses whichever side you took, so the
ceiling for *any* 50/50 bettor is `(1 - flat) / 2 = 48.44%`. Balanced's 52.22%
is therefore **+3.78pp** over a coin flip (z = +18.2), not +2.2pp. The flat rate
swings hard by year — 33.58% in 2017, 0.12% in 2021, 3.46% in 2025 — so a
per-year hit rate only means anything against that year's own ceiling.

Measured that way, Balanced is negative in the first two years and positive in
every year since:

| year | edge vs ceiling | | year | edge vs ceiling |
|---|---:|---|---|---:|
| 2017 | −3.11pp (z −3.1) | | 2022 | +6.21pp (z +9.6) |
| 2018 | −2.35pp (z −3.4) | | 2023 | +6.60pp (z +9.7) |
| 2019 | +5.07pp (z +7.2) | | 2024 | +2.54pp (z +4.6) |
| 2020 | +5.26pp (z +8.1) | | 2025 | +4.51pp (z +8.4) |
| 2021 | +3.34pp (z +5.8) | | 2026 | +5.08pp (z +6.6) |

2017–2018 is the same failure mode the 5m presets show, and it is coherent:
these presets **fade** structure breaks, and both the 2017 parabolic run and the
2018 crash were sustained one-way trends where breaks kept going. Expect losses
in a strongly trending regime.

> **Why these fade the break.** Measured as a reversal *detector* at face value
> (`predict_direction = Reversal`, symmetric ±1 ATR barriers), Break of Structure
> scores **45.43%** on 5m and **47.11%** on 1m — well below chance, 13.9σ below on
> 5m. As a reversal call it is reliably wrong, which is exactly why the presets
> above trade it as `Continuation`. Candlestick patterns were the only detector
> above chance (50.71% / 51.83%), but no detection preset is shipped.

**On the data source.** `btc_1s.db` (Binance 1s klines, 2026-02 → 2026-07,
15,292,800 rows, gapless) was evaluated as the 1m source and **rejected**.
Aggregating its per-second open/close reproduces open and close essentially
exactly — close matched on 10,080/10,080 sample bars — but understates the true
bar range by ~4%, since per-second open/close cannot see inside a second: 39% of
bars miss the real extreme. Reversal reads high/low for wicks, pivots, ATR and
the location gate, and `market.db` has real 1m klines that are 100% complete
over the same window and span 9 years instead of 6 months.

It was used as a **robustness check** instead, and the presets survive it. Over
2026-02 → 07, rebuilding the bars from 1s changes Volume 53.33% → 52.96% and
Balanced 53.44% → 53.58%, both well inside their 95% intervals — so the edge
does not depend on exact wick extremes.

Both are **Break of Structure traded as Continuation** — i.e. *fade* the break.
The evidence that this is not a curve fit: the never-swept 2018–2023 years score
*higher* than the window that was optimised on, the holdout beats train for both
presets, and it is not directional beta (bets run ~48% long / ~52% short while
the share of all 5m candles closing up is 49.6–50.5% in every year).

**Where it fails.** 2017 (partial year, Aug–Dec) scores 43.9% / 44.7% — far below
chance, and not noise at ~1,000 bets. That is the mechanism running in reverse:
these presets fade structure breaks, and 2017 was a parabolic bull run in which
breaks kept going. Expect losses in a sustained runaway trend. The edge also
decays: 58% across 2018–2023 against 54–56% across 2024–2026.

**Not shipped.** The candlestick-pattern family (384 configs) hit 54–55% on train
but fell to 49–53% on the holdout across every top config, and RSI/MACD
divergence never cleared 50.7% on train at any usable volume. Both remain
available as parameters; neither earned a preset.

The `+0.13` EV per \$1 at 0.50 odds assumes a 0.50 fill, which a real Polymarket
book will not offer on a directional 5m market. Hit rate is the finding; the EV
figure is an upper bound.

## Harmonic Patterns (beyond the video)

*XABCD geometry, entered at the completion zone.* Harmonic pattern theory
(Gartley 1935, formalised with Fibonacci ratios by Scott Carney) says a reversal
can be anticipated from the **proportions** of the last four swing legs. Label
five alternating swing points X-A-B-C-D and each named pattern is one box in a
four-dimensional space of leg ratios:

| pattern | AB/XA | BC/AB | CD/BC | D |
|---------|-------|-------|-------|---|
| Gartley | 0.618 | 0.382–0.886 | 1.13–1.618 | 0.786 of XA |
| Bat | 0.382–0.50 | 0.382–0.886 | 1.618–2.618 | 0.886 of XA |
| Butterfly | 0.786 | 0.382–0.886 | 1.618–2.618 | 1.27–1.618 of XA |
| Crab | 0.382–0.618 | 0.382–0.886 | 2.618–3.618 | 1.618 of XA |
| Cypher | 0.382–0.618 | 1.13–1.414 \* | 1.272–2.0 | 0.786 of XC |
| Shark | free | 1.13–1.618 \* | 1.618–2.24 | 0.886–1.13 of XA |
| AB=CD | free | 0.382–0.886 | 1.13–2.618 | CD = AB |

(\* measured against XA rather than AB, as those two patterns are defined.)

X-A-B-C are known; **D is a forecast**. Its price is projected two independent
ways — from the pattern's own D ratio and from the CD/BC extension — and the
overlap is the **Potential Reversal Zone**. The bet is placed on the bar that
first trades into that zone: long a bullish pattern's PRZ (X low, A high, B low,
C high, D low), short a bearish one's.

The usual objection to harmonic patterns is that they are drawn after the fact —
pick different swing points and every ratio changes. Two rules remove the
discretion: swing points are **fractal pivots** cleaned into a strictly
alternating sequence and admitted only once the scan reaches `j + pivot_right`,
and a pattern is built from the **last four confirmed pivots and nothing else**.
A truncation test (regenerate on the series cut off *at* each signal bar)
reproduces 120 sampled signals across the three presets with zero mismatches.

An armed PRZ is a standing order until price reaches it, price blows through its
far side by more than `prz_overshoot_atr`, `max_bars_to_d` bars elapse, or the
pivot C it hangs off is overwritten by a more extreme one. Several can be armed
at once; a bar where a bullish and a bearish zone both complete is discarded.

| Group | Params |
|-------|--------|
| **Pivots** | `pivot_left`, `pivot_right` |
| **Patterns** | `use_gartley` … `use_abcd`, `ratio_tolerance`, `require_cd_zone` |
| **Geometry** | `min_xa_atr`, `max_pattern_bars`, `max_bars_to_d`, `max_prz_atr` |
| **PRZ Entry** | `prz_entry` (Wick Touch \| Close Inside), `prz_overshoot_atr` |
| **Entry Timing** | `require_opposing_bar`, `opposing_bar_min_atr` |
| **Volatility** | `vol_atr_length`, `atr_pct_min`, `atr_pct_max` |
| **Decision** | `predict_direction` (Reversal \| Continuation) |

`ratio_tolerance` pads every window in the table above on both sides, in ratio
units — it is the single knob deciding how strict "a Gartley" is.

### Polymarket presets

Fitted to **the last two years**, split rather than used whole: train
2024-07-29 → 2025-11-01, then the pick frozen and 2025-11-01 → 2026-07-29 scored
once. The years before 2024-07 were never loaded by the sweep. 5,032
configurations over four stages; selection was mechanical (bet floor, both train
halves ≥ 52%, `pivot_left` off the grid boundary, then highest train hit rate).

| preset | bets | hit | z | unswept 2017–24 | 2yr | train | HOLDOUT | worst full yr |
|--------|-----:|----:|--:|------:|------:|------:|--------:|---------:|
| PM 5m Volume | 43,820 | 55.38% | +22.5 | 55.59% | 54.70% | 54.56% | 54.93% | 54.07% (2024) |
| **PM 5m Balanced** | 13,144 | **57.11%** | +16.3 | 57.28% | 56.38% | 56.49% | 56.19% | 54.42% (2024) |
| PM 5m Selective | 3,972 | 57.28% | +9.2 | 57.06% | 57.97% | 60.24% | 54.37% | 54.77% (2021) |

**Balanced is the pick** — it gives up 1.4pp against Selective for 3.3× the
volume, and its train / holdout / unswept columns agree to within 1.1pp.
Selective is the one to distrust: 60.24% train against 54.37% on 366 holdout
bets. Evidence this is not a curve fit: the never-swept 2017–2024 years score
*higher* than the window optimised on, on 3–5× the bets; bets split evenly and
both sides win at the same rate (Volume: 21,544 long at 55.44%, 22,276 short at
55.33%) while 49.6–50.5% of all 5m candles close up in every year.

**The textbook direction is right.** Every admitted config bets *with* the
pattern: Reversal 54.70 / 56.41 / 58.11% against Continuation 45.19 / 43.56 /
41.89% on the same bets. Note this is also the mean-reverting direction — a
bullish PRZ is reached by price falling into it — which is what every strategy
that works in this repo has in common.

### The Fibonacci ratios earn almost nothing; the shape constraint earns

Replace the pattern set with **one free box** — AB, BC and CD unconstrained, D at
an arbitrary retracement `r` of XA, everything else identical — and walk `r`
through the canonical values and deliberate non-canonical neighbours:

| r | hit | vs neighbours | | r | hit | vs neighbours |
|---|-----|------|--|---|-----|------|
| 0.300 | 51.00% | — | | 0.886 \* | 53.72% | +0.29pp |
| 0.382 \* | 50.59% | **−0.99pp** | | 0.950 | 53.08% | −0.29pp |
| 0.450 | 52.17% | +0.71pp | | 1.000 ~ | 53.02% | −0.22pp |
| 0.500 ~ | 52.32% | +0.07pp | | 1.130 | 53.40% | −0.06pp |
| 0.550 | 52.32% | +0.04pp | | 1.272 \* | 53.90% | +0.03pp |
| 0.618 \* | 52.25% | **−0.21pp** | | 1.450 | 54.32% | +0.43pp |
| 0.700 | 52.61% | −0.29pp | | 1.618 \* | 53.89% | −0.07pp |
| 0.786 \* | 53.54% | +0.34pp | | 1.800 | 53.60% | — |

(\* = canonical Fibonacci, ~ = conventional but not Fibonacci.) Hit rate rises
smoothly with depth and there is **no bump at the golden-ratio values**: 0.618
comes in 0.21pp *below* the mean of its neighbours and 0.382 is the worst point
on the curve. Same verdict [Fib Retracement](#the-fibonacci-ratios-earn-nothing)
reached from the other direction.

**But the joint constraint does earn.** The best free box tops out at 54.0–54.3%,
and tightening it until it is nearly as selective as the real thing does not
close the gap (3,664 bets at 54.15%, against the six real boxes at 2,537 bets and
56.41%). Requiring AB, BC *and* CD jointly in range is worth roughly +2pp over
any single D-level rule. Shifting every pattern's AB and D window off its
textbook centre confirms the values are a weak optimum at best:

| offset | −0.15 | −0.10 | −0.05 | **0.00** | +0.05 | +0.10 | +0.15 |
|--------|------:|------:|------:|------:|------:|------:|------:|
| 2yr hit | 54.54% | 55.17% | 55.97% | **56.41%** | 55.69% | 55.69% | 54.65% |
| holdout | 56.38% | 55.85% | 54.41% | 56.19% | 56.16% | **57.69%** | 56.43% |

Canonical peaks on the fitted window by 0.4–0.6pp, but the holdout column peaks
at +0.10. Read that as "the textbook numbers are a reasonable place to put the
boxes", not as evidence that phi does anything.

**Per pattern** (one enabled at a time, Balanced settings, 2 years): Crab 479
bets @ 59.08% (z +4.0), Butterfly 347 @ 59.65% (+3.6), Gartley 902 @ 55.88%
(+3.5), Bat 646 @ 55.11% (+2.6), Cypher 188 @ 55.32% (+1.5), Shark 144 @ 52.78%
(+0.7), AB=CD 3,365 @ 54.23% (+4.9). AB=CD — the one pattern with no Fibonacci
content at all — carries the most total edge by z purely on volume, at the lowest
rate. Balanced and Selective leave it off; Volume keeps it on, which is most of
why Volume has 3× the bets and 2pp less edge.

**Where it fails.** 2017 (partial year, Aug–Dec) scores 44.9 / 48.2 / 43.8% — far
below chance, and not noise at 105–1,434 bets. That is the mechanism in reverse:
these presets buy reversals, and 2017 was a parabolic run in which they did not
come. Reversal's BOS presets break in the same year for the same reason. The edge
also decays — 55–60% across 2018–2023 against 54–58% across 2024–2026 — and
volume is thin: Balanced is ~1,270 bets/year, Selective ~470. The Volume preset's
Against-SMA50 trend filter was selected over no filter by +0.22pp on train; do
not read meaning into it.

## Momentum Indicators (beyond the video)

*Nine momentum oscillators put on one scale and averaged.* Momentum theory says
the **rate** at which price moves carries information the price level does not.
Every classic indicator measures that rate differently, and they disagree
constantly — mostly because they are quoted on incomparable scales. So each is
mapped to a score in `[-1, +1]` and the composite **M** is their mean:

| oscillator | normalised as |
|---|---|
| ROC | `clamp(roc% / (norm × ATR%))` |
| RSI | `(rsi - 50) / 50` |
| Stochastic %K | `(%K - 50) / 50` |
| Williams %R | `(%R + 50) / 50` |
| CCI | `clamp(cci / 200)` |
| Ultimate | `(uo - 50) / 50` |
| MACD histogram | `clamp(hist / (norm × ATR))` |
| TSI | `tsi / 100` |
| Awesome | `clamp(ao / (norm × ATR))` |

The three that live in price units are divided by ATR, which is what makes them
comparable across 2017 and 2026 without refitting. ROC, RSI, Stochastic,
Williams %R, CCI and Ultimate share **one** `osc_length` rather than carrying six
near-duplicate parameters; only the two-EMA family (MACD, TSI, Awesome) keeps its
own fast/slow pair, because there the *gap* between the lengths is the indicator.

`trigger_mode` picks the event, each defining a momentum direction `d`:

| trigger | fires when | d |
|---|---|---|
| **Extreme** | `\|M\|` first reaches `score_threshold` | `sign(M)` |
| **Zero Cross** | M changes sign | `sign(M)` |
| **Momentum Turn** | M's slope flips while `\|M\|` is still extreme | sign of the new slope |

`predict_direction` then trades with `d` (**Follow**: momentum persists) or
against it (**Fade**: momentum exhausts).

### Polymarket presets

Fitted to **the last two years**, split rather than used whole: train
2024-07-29 → 2025-11-01, pick frozen, then 2025-11-01 → 2026-07-29 scored once.
1,644 configurations over three stages; mechanical selection.

| preset | bets | hit | z | unswept 2017–24 | 2yr | train | HOLDOUT | worst full yr |
|--------|-----:|----:|--:|------:|------:|------:|--------:|---------:|
| PM 5m Volume | 33,061 | 57.18% | +26.1 | 57.98% | 55.25% | 54.93% | 55.86% | 55.4% (2024) |
| **PM 5m Balanced** | 12,765 | **58.46%** | +19.1 | 59.17% | 56.98% | 57.40% | 56.10% | 56.9% (2024) |
| PM 5m Selective | 1,124 | 62.81% | +8.6 | 62.93% | 62.65% | 64.26% | 59.24% | 55.4% (2024) |

**Balanced is the pick.** Volume trades 2.6× as often for 1.3pp less edge, and
Selective posts the best headline while being the least trustworthy number here.

### What the sweep actually found

**Fade, not follow.** Pooled: Fade 50.23% train / 51.50% holdout against Follow
49.62% / 48.43% — and every Follow config that looked good on train fell to
47–49% out of sample. Momentum theory's headline claim, that a fast move keeps
going, is false at the 5m horizon. The exhaustion half of the same theory is not.

**Only the Extreme trigger works.**

| trigger | train | holdout |
|---|------:|--------:|
| Extreme | 52.16% | 53.72% |
| Momentum Turn | 49.62% | 50.19% |
| Zero Cross | 49.31% | 51.04% |

Worth dwelling on: "momentum is decelerating" is the more sophisticated-sounding
idea and it carries nothing. The second derivative is indistinguishable from
noise, and so is the zero cross. All of the edge is in the plain, unfashionable
observation that the reading is stretched.

**Stochastic %K and Williams %R are the same number.** Over one window,
`%R = %K - 100`, so after recentring they normalise to an identical score —
verified equal to 2e-16. Two of the nine "independent" oscillators are one
measurement wearing two names, and the default panel silently double-weights it.

**RSI carries the panel.** One oscillator at a time, at the Balanced settings:

| alone | bets | hit | | alone | bets | hit |
|---|-----:|----:|--|---|-----:|----:|
| RSI | 5,531 | **56.28%** | | Awesome | 8,611 | 51.68% |
| Ultimate | 6,183 | 54.50% | | Stochastic | 41,994 | 51.28% |
| CCI | 20,625 | 52.07% | | ROC | 38,165 | 50.32% |

Against the full nine-oscillator composite at 4,088 bets and 56.65%. Eight
further momentum indicators, averaged in, buy about **0.4pp** over RSI on its
own. And ROC — the purest expression of momentum in the family — is a coin flip
by itself.

**`min_agree` does nothing** (1/5/9 → 54.12/54.12/54.14%). Once `|M| ≥ 0.5` the
oscillators already agree by construction, so the vote threshold has nothing
left to reject.

**The trend filter is redundant by construction.** Fading a momentum extreme is
*definitionally* against the short-term trend, so Against-Trend/SMA50 passes 100%
of signals and With-Trend/SMA50 passes 0%. Only a much slower MA can disagree —
which is exactly what Selective's With-Trend EMA200 exploits.

**Where it fails.** 2017 (partial year) scores 45.5 / 46.7 / 53.8%, well below
chance: fading exhaustion loses in a parabolic run. Every mean-reversion strategy
in this repo fails in that same year. The edge also decays — 57–64% across
2018–2023 against 55–57% across 2024–2026 — and Selective's two train halves are
55.4% and 69.3%, a 14pp spread that no holdout can make respectable.

## CHoCH (Change of Character) (beyond the video)

*The first structure break against the trend.* Smart-Money-Concepts vocabulary
for one idea: a trend is a sequence of swing points, and you can name the exact
bar where that sequence stops behaving like one. **Two events come out of the
same break**, and only the prior state separates them:

| prior bias | price breaks | event | reading |
|---|---|---|---|
| bearish | last swing **high** | **CHoCH** (bullish) | the downtrend just failed |
| bullish | last swing **high** | **BOS** (bullish) | the uptrend just continued |
| bullish | last swing **low** | **CHoCH** (bearish) | the uptrend just failed |
| bearish | last swing **low** | **BOS** (bearish) | the downtrend just continued |

Bias is carried explicitly in a state machine, which is what this adds over the
structure detector in [reversal.py](backend/strategies/reversal.py) — that one
compares two swing lows against the latest swing high per bar and calls the
result a "BOS", but by the table above it only ever fires on a downtrend broken
upward, which is a **CHoCH**. Good detector, wrong name, no memory of bias.

Swing points are fractal pivots admitted only at `j + pivot_right`; a level is
consumed when it breaks and re-arms only when a new pivot confirms. A truncation
test reproduces 105 sampled signals across the three presets with zero
mismatches.

| Group | Params |
|-------|--------|
| **Structure** | `pivot_left`, `pivot_right`, `max_level_age` |
| **Event** | `signal_on` (CHoCH \| BOS \| Both) |
| **Break** | `break_mode` (Close \| Wick), `break_buffer_atr`, `min_displacement_atr` |
| **Entry** | `entry_mode` (On Break \| On Retest), `retest_tol_atr`, `max_retest_bars` |
| **Higher Scale** | `use_htf_filter`, `htf_logic` (Agree \| Oppose), `htf_pivot_*` |
| **Decision** | `predict_direction` (With \| Against Structure) |

### Polymarket presets

Fitted to **the last two years**, split rather than used whole: train
2024-07-29 → 2025-11-01, pick frozen, then 2025-11-01 → 2026-07-29 scored once.
2,025 configurations over three stages; mechanical selection.

| preset | bets | hit | z | unswept 2017–24 | 2yr | train | HOLDOUT | worst full yr |
|--------|-----:|----:|--:|------:|------:|------:|--------:|---------:|
| **PM 5m Volume** | 25,730 | **57.79%** | +25.0 | 58.41% | 56.05% | 55.93% | 56.27% | 56.4% (2025) |
| PM 5m Balanced | 8,421 | 56.77% | +12.4 | 56.97% | 56.22% | 57.51% | 53.92% | 55.2% (2021) |
| PM 5m Selective | 5,219 | 59.55% | +13.8 | 59.95% | 58.03% | 60.60% | 53.94% | 58.7% (2018) |

**Volume is the pick** — unusually for this repo, it is the only tier whose
holdout matches its train (55.93% → 56.27%). Balanced and Selective post better
headlines and shrink 3.6pp and 6.7pp out of sample.

### Every break is traded backwards

Pooled over stage 1, taking the break at face value scores **46.72%** train /
46.04% holdout; fading it scores **53.15%** / 53.86%. There is no configuration
where following structure wins. A structure break on 5m BTC is an exhaustion
signal, not a continuation one.

### The SMC claim, tested on matched settings

SMC teaches that CHoCH marks reversal and BOS marks continuation. The cleanest
test is **inside the Volume preset**, which trades both — identical settings,
identical bars, identical filters, differing only in whether the break went
against the prevailing bias:

| event | bets | hit |
|---|---:|---:|
| **CHoCH** | 10,378 | **59.05%** |
| BOS | 15,352 | 56.94% |

CHoCH is worth **+2.1pp** over BOS on a matched comparison, and stage 2 agrees
pooled (57.10% vs 54.35% on the holdout). So the distinction is real — but note
what it is *not*: both are profitable, and both are profitable **faded**. CHoCH
isn't the reversal signal and BOS the continuation signal; CHoCH is the *better*
reversal signal and BOS the worse one.

Corroboration from outside this file: reversal.py ships two presets on what is
actually a faded CHoCH, scoring 56.60% and 56.84% — a different implementation
landing within a point of this one.

### What else the sweep found

**The retest entry destroys the edge.** SMC's signature move is to wait for
price to return to the broken level and enter on the "mitigation". At all three
anchors it costs 5–6pp: 50.57% vs 56.01%, 50.87% vs 56.51%, 52.15% vs 57.93%.
Whatever the break is telling you has decayed by the time price comes back.

**Close beats wick** (53.78%/54.54% against 52.64%/53.32%). The stop-run through
a level that closes back inside is real, and it is not a structure break.

**`max_level_age` is inert** — 100, 500 and 2000 give byte-identical results. A
level is consumed the moment it breaks and re-arms only when a new pivot
confirms, so it never survives long enough to go stale.

**Higher-scale "Oppose" helps** (58.25% holdout at 60-bar pivots, against 56.36%
for no filter and 54.89% for "Agree") — fading a break that fights the *bigger*
structure beats fading one aligned with it, the opposite of the SMC habit.

**Where it fails.** 2017 (partial year) scores 47.8 / 43.8 / 50.6% — fading
breaks loses when breaks keep running, which is what a parabolic year is. Every
mean-reversion strategy in this repo fails in that same year.

## Fib Retracement (beyond the video)

*Trade the pullback inside a measured swing leg.* Take a swing **leg** — a
low-to-high push or its mirror — and measure how much of it price has since
given back, as a fraction of the leg. The classic trade enters as price pulls
back into one of the canonical levels (23.6 / 38.2 / 50 / 61.8 / 78.6 %),
betting the leg resumes.

Discretion is the usual problem with this tool: pick a different swing high and
every level moves. So the leg is defined mechanically and causally — inside a
rolling `swing_lookback` window, take the highest high and the lowest low, and
whichever came **last** ends the leg and gives it direction. No manual
anchoring, no look-ahead, and the retracement is always in [0, 1].

| Group | Params |
|-------|--------|
| **Swing Leg** | `swing_lookback`, `min_leg_atr`, `min_leg_bars` |
| **Fibonacci** | `fib_level`, `fib_tolerance`, `require_fresh_touch` ☑ |
| **Entry Timing** | `require_opposing_bar` ☑, `opposing_bar_min_atr` |
| **Volatility Filter** | `vol_atr_length` (also sizes TP/SL), `atr_pct_min`, `atr_pct_max` |
| **Trend Filter** | `use_trend_filter` ☑, `trend_logic`, `ma_type`, `ma_length`, `source` |
| **Decision** | `predict_direction` (Trend Resume ⋁ Retrace Deeper) |
| **Day of Week (UTC)** | `trade_mon` … `trade_sun` ☑ |

`fib_level` is a **free float, not a dropdown of the five blessed values** — on
purpose, so a sweep can ask whether 0.618 does anything 0.55 and 0.70 don't.

### The Fibonacci ratios earn nothing

The level grid interleaved the canonical ratios with non-canonical neighbours.
Hit rate is **monotone in retracement depth** with no bump at the golden-ratio
values. Each level is also compared against the mean of its two grid neighbours,
holding every other parameter fixed:

| Level | Hit | vs neighbours | | Level | Hit | vs neighbours |
|------:|----:|--------------:|-|------:|----:|--------------:|
| **0.236*** | 49.43% | −1.07pp | | 0.55 | 52.40% | −0.06pp |
| 0.30 | 50.49% | +0.12pp | | **0.618*** | 52.82% | **−0.09pp** |
| **0.382*** | 51.43% | +0.28pp | | 0.70 | 53.73% | +0.24pp |
| 0.45 | 51.98% | +0.21pp | | **0.786*** | 54.78% | +0.45pp |
| **0.50*** | 52.21% | +0.04pp | | 0.85 | 55.61% | +0.83pp |

<sub>* = canonical Fibonacci level. 50% isn't one either — it's the plain
midpoint, included by convention.</sub>

0.618 — the level every chartist watches — lands 0.09pp **below** the average of
its neighbours. The depth curve flattens and turns over past 0.85 (0.85 = 55.88%,
0.90 = 55.76%, 0.95 = 55.55%). `fib_level` is a depth knob wearing a Fibonacci
hat.

### …but the leg does earn its keep

A deep retracement means the close sits near the far end of the window's range —
which is what Williams %R measures with no Fibonacci and no leg at all. So the
leg gate was tested against a matched control keeping the window, leg-size floor,
zone, first-touch, opposing-bar and volatility rules, removing **only** the
requirement that the swing be intact (high after low, no new low since):

| Config | Fib bets | Fib hit | Control bets | Control hit | z |
|---|---:|---:|---:|---:|---:|
| lb=24, lvl=0.85 | 13,365 | 54.96% | 74,025 | 54.01% | +2.03 |
| lb=24, lvl=0.95 | 5,010 | 54.83% | 71,056 | 55.06% | −0.31 |
| lb=48, lvl=0.618 | 27,505 | 52.98% | 56,071 | 52.11% | +2.38 |
| lb=144, lvl=0.85 | 3,595 | 56.75% | 26,668 | 53.46% | +3.71 |

The structural half of the idea is real; the arithmetic half is not. What this
actually trades is *"price rallied, gave nearly all of it back, but held above
the prior low — buy that retest"*, and the leg definition is what encodes the
holding-above part.

### Polymarket presets

Three sweep stages, 10,800 combinations, whole DB (938,857 5m bars, 2017-08 →
2026-07). Parameters chosen on **2017-2023 only**; the TEST column was scored
afterwards and never consulted while selecting.

| Preset | Bets | Hit | Train 17-23 | TEST 24-26 | 2025-26 | Worst yr | z |
|--------|-----:|----:|------------:|-----------:|--------:|---------:|--:|
| **PM 5m Balanced** | 7,338 | 58.16% | 58.86% | **56.66%** | **56.32%** | 50.75% | 14.0 |
| PM 5m Volume | 18,205 | 55.31% | 56.46% | 52.36% | 51.86% | 51.41% | **14.3** |
| PM 5m Selective | 3,843 | 57.69% | 60.41% | 53.63% | 53.31% | 51.89% | 9.5 |
| PM 5m Hi Hit | 1,132 | 59.45% | 62.77% | 53.09% | 54.66% | 50.66% | 6.4 |

***Balanced is the preset to use*** — the only tier that survives the holdout
intact: 56.66% across 2,328 out-of-sample bets, still 56.32% over 2025-26, and
every year bar the partial 2017 at or above 54.8%. The other three are shipped to
show the frontier, not as recommendations.

Findings beyond the numbers:

- **Trend Resume only.** All 50 of the top-50 training configs bet the leg
  resumes (pooled: 51.73% vs 49.40%). Since buying a pullback means fading the
  most recent move, this is a mean-reversion result too — the fourth strategy
  here to land there.
- **`require_opposing_bar` is the most valuable single filter**, as in Multi
  Horizon: ON 52.51% vs OFF 49.75%, z=+189. Demanding a real body helps
  monotonically (0.0 → 0.75 ×ATR gives 54.94 → 56.01%).
- **"Against Trend" helps** (57.74% vs 57.03% filter-off), matching Volume
  Exhaustion. With Trend Resume that means buying the pullback while price is
  *below* the MA.
- **No weekend gate.** Split by UTC day at fixed parameters, the premium is
  +0.43 / +2.32 / −2.06pp on Balanced / Selective / Hi Hit, all |z| < 1.4.

⚠️ **Two caveats specific to this strategy.** **Shrinkage scales with training
hit rate, steeply** — ranked by train hit the four tiers lose 2.2 / 4.1 / 6.8 /
9.7 points out-of-sample, exactly inverting the order, so the best-looking preset
in-sample (Hi Hit, 62.77%) is the worst out of it (53.09%). And **train-internal
stability did not predict survival**: configs scoring 63.7% and 66.4% on the two
halves of the training span still collapsed to 50.0% on 2024-26. Only the real
holdout separated these tiers — which is the argument for keeping one.

## Candlesticks (beyond the video)

*The classic Japanese patterns, written as formulas.* A hammer, an engulfing
bar, a morning star — each is a claim about who won the bar, read as either a
reversal or a continuation. The usual problem is that "that's a hammer" is a
judgement call, so all nine families here are **formulas over OHLC** with the
fuzzy parts (how big is a strong body, how long is a long wick) exposed as
parameters a sweep can turn.

| Group | Params |
|-------|--------|
| **Patterns** | `pat_engulfing` ☑, `pat_hammer` ☑, `pat_harami`, `pat_piercing`, `pat_star`, `pat_doji`, `pat_tweezer`, `pat_marubozu`, `pat_soldiers` |
| **Pattern Geometry** | `body_strong_min`, `body_small_max`, `doji_body_max`, `pin_wick_min`, `pin_opp_wick_max`, `marubozu_body_min`, `tweezer_tol_atr`, `engulf_mode` (Body ⋁ Body+Wick), `min_range_atr` |
| **Prior Move** | `require_prior_move` ☑, `prior_move_logic` (Textbook ⋁ Extension ⋁ Reversal), `prior_move_bars`, `prior_move_atr` |
| **Volatility Filter** | `vol_atr_length` (also sizes TP/SL), `atr_pct_min`, `atr_pct_max` |
| **Trend Filter** | `use_trend_filter` ☑, `trend_logic`, `ma_type`, `ma_length`, `source` |
| **Decision** | `predict_direction` (Pattern ⋁ Fade) |
| **Day of Week (UTC)** | `trade_mon` … `trade_sun` ☑ |

**One structural adaptation, and it matters.** The textbook piercing line, dark
cloud cover and star patterns all require a *gap* — the next session opening
away from the last close. Crypto trades 24/7, so `open[i] == close[i-1]` almost
exactly on every 5m bar and a strict gap test would make those patterns fire
essentially never. Every gap condition is relaxed to a **touch** condition. What
survives of those three is their body geometry, not the gap.

Patterns that disagree on the same bar discard it rather than being
majority-voted, the same rule the Combined strategy uses for its voters.

### The textbook reading is backwards

Each family was tested in both prior-move contexts — the classical **reversal**
reading (the pattern contradicts the move into it) and the **extension** reading
(the pattern caps a move already under way) — betting *against the prior move*
in both cases, so the two are directly comparable. Train hit rate, prior move
≥ 1.0×ATR over 12 bars, against a 52.18% control:

| Family | Reversal ctx | Extension ctx |
|--------|-------------:|--------------:|
| Three soldiers / crows | 43.00% (614) | **58.21%** (2,723) |
| Marubozu | 45.83% (12,319) | **56.94%** (10,524) |
| Morning / evening star | 48.02% (2,353) | **56.87%** (932) |
| Engulfing | 48.68% (26,659) | **55.14%** (16,959) |
| Piercing / dark cloud | 49.51% (8,738) | 53.62% (3,264) |
| Hammer / shooting star | 53.21% (25,835) | 53.10% (22,574) |
| Harami | 51.47% (33,216) | 52.38% (12,701) |
| Tweezer | 50.45% (24,014) | 52.05% (13,880) |
| Doji | 53.13% (34,367) | *undefined* |

The classical reversal reading sits at or **below** the control for six of the
nine families, and collapses for the two that are supposed to be most decisive:
three white soldiers after a decline is 43.00%, a marubozu against the move
45.83%. The extension reading beats the control for all eight families where it
is defined. Only the two wick families — hammer and doji — earn anything in the
direction the textbook says, and they earn about a point.

Put plainly: **a bullish engulfing bar is not a bottom.** It is a big green
candle, and a big green candle at the end of a rally is a good thing to sell.
`prior_move_logic` exists so this is a setting rather than an assumption.

### …but most of the edge is the context, not the shape

Every winning configuration bets against the immediately preceding move, so the
shapes were tested against a tight matched control: same prior-move gate, same
bar-range floor, same volatility band, fading the bar's own direction — with
**no body-ratio requirement**, so any bar extending the move qualifies.

| Preset config | Control bets | Control hit | Preset bets | Preset hit | Gap vs disjoint remainder |
|---|---:|---:|---:|---:|---:|
| 6b / 1.0×ATR / rng 0.5 | 184,532 | 55.16% | 36,079 | 56.99% | +2.27pp (z=+7.8) |
| 6b / 1.5×ATR / rng 1.0 | 60,246 | 56.74% | 13,501 | 58.41% | +2.15pp (z=+4.4) |
| 12b / 2.0×ATR / rng 1.5 | 24,118 | 57.12% | 5,279 | 58.97% | +2.37pp (z=+3.1) |

The geometry is real and statistically solid — and it is roughly **a fifth of
the story**. The other four fifths is *"fade a decisive bar that extends a
move"*, which needs no pattern vocabulary at all. If you want the effect without
the taxonomy, the control is simpler and carries 5× the volume at 1–2pp less.

### Polymarket presets

Five sweep stages, ~3,000 combinations, whole DB (939,434 5m bars, 2017-08 →
2026-07). Parameters chosen on **2017-2023 only**.

| Preset | Bets | Hit | Train 17-23 | TEST 24-26 | 2025-26 | Worst yr | z |
|--------|-----:|----:|------------:|-----------:|--------:|---------:|--:|
| **PM 5m Balanced** | 13,501 | 58.41% | 60.61% | **55.48%** | 55.44% | 47.47% (2017) | 19.5 |
| PM 5m Volume | 36,079 | 56.99% | 58.67% | 54.59% | 54.32% | 48.33% (2017) | **26.5** |
| PM 5m Selective | 5,279 | 58.97% | 61.65% | 55.57% | 54.94% | 52.79% (2017) | 13.0 |
| PM 5m Hi Hit | 1,472 | 59.78% | 63.23% | 55.17% | 56.38% | 48.98% (2017) | 7.5 |

***Balanced is the preset to use.*** All four survive the holdout and land
within 1pp of each other there (54.59 / 55.48 / 55.57 / 55.17%), so the tier
carrying the most bets at the top of that band wins on evidence rather than on
headline number: 5,793 out-of-sample bets at 55.48%, and every year from 2018 on
at or above 55.0%.

Findings beyond the numbers:

- **Fade, always.** No configuration of any family beat 50% betting the
  pattern's own direction once the context gate pointed the right way. The
  fifth strategy in this repo to land on mean reversion.
- **The trend filter and volatility band earn nothing here.** Across 225 filter
  combinations "Against SMA50" beat filter-off by 0.0–0.2pp and the ATR% band by
  ~0.3pp — inside noise at every volume. Both ship off/wide, unlike Fib
  Retracement and Volume Exhaustion where *Against Trend* paid.
- **A big bar matters more than a pretty one.** `min_range_atr` is the most
  valuable geometry knob — 0.0 → 1.5×ATR adds 3–4pp across every family — while
  `marubozu_body_min` anywhere in 0.75–0.90 barely separates (61.0–61.9%).
- **Body-only engulfment beats whole-range**: `Body+Wick` appears nowhere in the
  top 20 of its 288-combination grid.
- **No weekend gate.** The premium is +0.27 / +1.28 / +0.59 / +7.46pp across the
  four tiers; only Hi Hit's is nominally significant (z=+2.69), on 424 weekend
  bets — one result out of four comparisons.

⚠️ **Caveats.** **Shrinkage scales with training hit rate, again** — ranked by
train hit the tiers lose 4.1 / 5.1 / 6.1 / 8.1 points out-of-sample, in exactly
that order, repeating what Fib Retracement showed. **Every worst year is 2017**,
a partial year and the most relentlessly trending stretch in the record — a
parabolic trend is this strategy's failure mode and it will recur. **The edge
decays**: every tier's 2026 sits below its 2018-2023 average. And the holdout was
displayed during stage 2 before being switched off for stages 3-5, where the
shapes and filters were actually chosen — so treat 2024-2026 as a very good
shrinkage estimate rather than a perfectly blind one.

## Elliott Wave (beyond the video)

*Count the impulse mechanically and bet the next leg.* Elliott Wave says a trend
unfolds as a five-leg impulse (1-2-3-4-5) followed by a three-leg correction, and
that three rules are inviolable: **R1** wave 2 never retraces more than 100% of
wave 1, **R2** wave 3 is never the shortest of 1/3/5, **R3** wave 4 never
overlaps wave 1's territory. Three tradeable claims fall out: after 1-2 comes
wave 3, after 1-2-3-4 comes wave 5, and after a complete five comes a correction.

Wave counting is normally discretionary — which is what makes the theory hard to
falsify, since a count that fails gets relabelled rather than marked wrong.
Nothing here is discretionary. Swings come from an **ATR-thresholded zigzag**
where each pivot carries two bar numbers: where it happened (`i`) and where the
reversal that proved it made it knowable (`c`). Signals may only use pivots whose
`c` is at or before the current bar, so an in-progress leg's extreme is never
treated as a pivot. The count is then read off the last few confirmed pivots, and
a structure that does not match is skipped.

| Group | Params |
|-------|--------|
| **Wave Detection** | `atr_length`, `pivot_atr_mult`, `min_pivot_bars`, `min_wave1_atr` |
| **Wave Rules** | `enforce_impulse_rules` ☑, `wave2_min/max_retrace`, `wave4_min/max_retrace` |
| **Setup** | `trade_setup` (Wave 3 ⋁ Wave 5 ⋁ Wave 3 + 5 ⋁ Post-Impulse Reversal), `entry_mode` (Pivot Confirm ⋁ Retrace Zone), `max_setup_age_bars` |
| **Entry Timing** | `require_opposing_bar` ☑, `opposing_bar_min_atr` |
| **Volatility Filter** | `vol_atr_length`, `atr_pct_min`, `atr_pct_max` |
| **Decision** | `predict_direction` (Follow Count ⋁ Fade Count) |
| **Trend Filter** | `use_trend_filter` ☑, `trend_logic`, `ma_type`, `ma_length`, `source` |

`enforce_impulse_rules` is **a switch, not a law** — deliberately, so a sweep can
ask whether an "Elliott-valid" count predicts better than the same swing
structure with the rules removed.

### The three rules earn nothing — except on a complete count

2,016 pairs of configurations identical in every other parameter, one with
R1/R2/R3 on and one with them off, pooled over the training span:

| Setup / entry mode | Rules ON | | Rules OFF | | Δ |
|---|---:|---:|---:|---:|---:|
| Wave 3, Pivot Confirm | 1,448,881 | 54.75% | 1,771,303 | 54.77% | −0.02pp |
| Wave 3, Retrace Zone | 2,545,238 | 54.96% | 2,567,167 | 55.10% | −0.15pp |
| Wave 3+5, Pivot Confirm | 4,443,684 | 54.81% | 6,561,539 | 54.78% | +0.03pp |
| Wave 3+5, Retrace Zone | 8,390,683 | 54.63% | 11,681,785 | 54.17% | +0.46pp |
| Wave 5, Retrace Zone | 356,306 | 53.42% | 2,580,450 | 53.75% | −0.33pp |
| **Wave 5, Pivot Confirm** | **126,160** | **57.18%** | 1,309,607 | 54.65% | **+2.53pp** |

Five of six cells are indistinguishable from zero. The rules pay in exactly one
place — a **complete, confirmed 1-2-3-4 count** — where they buy +2.53pp at the
cost of 90% of the volume. That is also the only cell where R2 and R3 can be
evaluated at all; both need the whole structure. R1 alone, which is all a wave-3
setup can test, is worth −0.02pp. Elliott's constraints are not a general filter;
one conjunction of them is a rare-setup detector, and *PM 5m Hi Hit* is that
detector.

The Fibonacci-shaped retracement zones fare no better, echoing the Fib
Retracement result one strategy over: the wave-4 zone does best switched **off**
(wide 54.80% vs the textbook 0.236–0.786 band at 54.44% vs the tight
0.146–0.618 at 54.15%). What pays is plain depth on wave 2 — 0.5–1.0 gives
54.82%, 0.236–1.0 gives 54.68%, wide gives 54.37%.

### Polymarket presets

Three sweep stages, 8,340 combinations, whole DB (939,433 5m bars, 2017-08 →
2026-07). Selected on **2017-2023 only**; 1,558 of 7,836 scored configurations
passed admission (z ≥ 2.5 on train, both train halves ≥ 52%, every train year
with ≥ 25 bets above 50%), and each preset is the top train hit rate in its
bet-count band. The TEST column was scored afterwards.

| Preset | Bets | Hit | Train 17-23 | TEST 24-26 | 2025-26 | Worst yr | z |
|--------|-----:|----:|------------:|-----------:|--------:|---------:|--:|
| **PM 5m Balanced** | 5,285 | 58.81% | 60.59% | 55.39% | 54.48% | 52.38% | 12.5 |
| PM 5m Volume | 8,182 | 57.14% | 59.00% | 53.43% | 52.91% | 52.63% | **13.3** |
| PM 5m Selective | 1,488 | 60.95% | 62.34% | **58.04%** | 56.19% | 53.07% | 7.8 |
| PM 5m Hi Hit | 384 | **67.45%** | 67.66% | **67.11%** | 64.89% | 58.06% | 5.4 |

***Balanced is the preset to use*** on volume-vs-margin grounds — ~1 bet per 15
hours, 55.39% across 1,809 out-of-sample bets, no year below 52.4%. Selective is
the better *rate* out of sample on a third of the volume.

Findings beyond the numbers:

- **Mean reversion in both entry modes** — and the two winners carry *opposite*
  `predict_direction` settings. Pooled: Pivot Confirm + Fade 51.73% (vs Follow
  47.78%); Retrace Zone + Follow 51.13% (vs Fade 48.39%). They are the same
  trade: Retrace Zone + Follow buys while the wave-2 pullback is still falling;
  Pivot Confirm + Fade sells after the zigzag confirms the bounce off that low.
  Both fade the most recent move, where every strategy in this repo has landed.
- **`require_opposing_bar` is the most valuable single filter again** — the third
  independent confirmation, after Multi Horizon and Fib Retracement. 58.55% vs
  57.76%, 57.43% vs 56.45%, 61.99% vs 59.68% across the three lanes, and the
  minimum body helps monotonically (0.0 → 0.75 ×ATR gives 58.06 → 58.73%).
- **Bigger swings, better bets, to a plateau.** `pivot_atr_mult` 2.5 / 4.0 / 6.0
  / 9.0 gives 53.83 / 55.58 / 55.97 / 55.49%.
- **The trend filter barely matters here**, unlike Fib Retracement: Against Trend
  58.40% vs filter-off 58.32%.

⚠️ **Do not push this one for volume.** The widest admissible net — 28,618 bets,
54.58% on train — collapses to 50.74% on the holdout and 49.76% over 2025-26,
i.e. to nothing. Elliott's edge lives entirely in selectivity, which is why no
preset runs wider than ~8,000 bets. And *Hi Hit* is the one tier in this repo
that does **not** shrink (67.66% train → 67.11% holdout) — but it fires ~43 times
a year, so its 149 holdout bets carry a ±7.6pp interval. Genuinely
out-of-sample, and genuinely thin.

### …and refitted on the trailing 2 years

The presets above have decayed: over 2024-07-28 → 2026-07-28 (210,528 bars)
*PM 5m Volume* runs at **52.55%** against the 59.00% it showed on its own
training span. So all three stages were re-run over that window alone. There is
no holdout inside it, so admission could only use in-window stability (z ≥ 2.5,
both one-year halves ≥ 52%, ≥ 60 bets per half); 2,983 of 7,777 configurations
passed.

The in-window number is fitted and therefore biased. The column that isn't is
**2017-2023** — years the refit never saw:

| Preset | Last 2yr (fitted) | | 2017-2023 (unseen) | | Halves |
|---|---:|---:|---:|---:|:--|
| **PM 5m Volume - 2yr Train** | 1,898 | 56.38% | 4,771 | 56.89% | 56.19 / 56.56 |
| PM 5m Balanced - 2yr Train | 803 | 58.53% | 2,020 | 57.38% | 55.37 / 61.98 |

Both hold their rate on the years they were not fitted to — which is what
separates a durable setting that happens to be current from a regime call. **On
recent tape the refit beats the full-record fit at matched volume: 1,898 bets at
56.38% against 2,135 at 52.55%, +3.8pp.**

Run over the *whole* record, *Volume - 2yr Train* is also the flattest preset
here year to year — everything from 2018 on lands in a 4pp band:

| | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Volume - 2yr Train | 46.9% | 54.4% | 58.6% | 57.1% | 57.8% | 58.2% | 58.0% | 57.9% | 55.3% | 58.1% |
| *(bets)* | *226* | *719* | *696* | *699* | *822* | *763* | *846* | *902* | *961* | *534* |

The only year below 50% is the partial 2017 on 226 bets. Compare the
full-record *PM 5m Volume*, which runs 57-61% through 2018-2023 and then 54.3 /
52.6 / 53.4% in 2024-26. Fitting on the recent window did not chase the recent
regime; it found a setting that was always there.

The refit also *relocated* the strategy. Both 2yr presets are **Wave 5 + Pivot
Confirm + Fade Count** — the structure the full-record fit used only for its
thinnest tier — and *Balanced - 2yr Train* is the second preset to turn Elliott's
rules **on**. Recent tape rewards waiting for a complete 1-2-3-4 count and fading
its wave-4 confirmation. Neither uses the opposing-bar, volatility or trend
filters: over two years none of them earned their place, which is itself a
warning about choosing filters from two years of data.

⚠️ These carry **no out-of-sample evidence for the fit itself** — the 2017-2023
column is the past, not the future, and the full-record presets looked just as
good before they decayed. *Balanced - 2yr Train* is the less stable of the two
(55.37% then 61.98% by half). No Selective or Hi Hit tier is shipped for this
window: the best candidates were 257 bets at 61.48% and 138 at 64.49% — about 1.5
bets a week with a ±6pp interval and no holdout, which is not evidence.

## Renko (beyond the video)

*Fade the brick that breaks a one-way run.* A Renko chart throws away time: a
fixed-size **brick** prints only when price moves a full brick beyond the last
one, so a quiet hour prints nothing and a violent one prints six. What is left is
a stair-step of same-size moves — a deliberately crude noise filter that cannot
wiggle. A **run** of N bricks is N brick-sizes of net one-way movement with
volatility already divided out; the **reversal** brick that ends it is the
chart's own definition of "that trend just broke".

Bricks are built from **closes only**, so the sequence never depends on assuming
which of a bar's extremes came first — an assumption a backtest cannot check and
which flatters wick-based Renko. The cost is honest: this is the slower, less
sensitive Renko, and a bar that spikes and returns prints nothing.

| Group | Params |
|-------|--------|
| **Brick Size** | `brick_mode` (ATR ⋁ Percent ⋁ Fixed), `atr_length`, `brick_atr_mult`, `brick_pct`, `brick_fixed` |
| **Bricks** | `reversal_bricks` |
| **Signal** | `trigger` (Brick Reversal ⋁ Brick Run ⋁ Any New Brick), `min_run_bricks`, `max_new_bricks` |
| **Volatility Filter** | `vol_atr_length`, `atr_pct_min`, `atr_pct_max` |
| **Decision** | `predict_direction` (Follow Brick ⋁ Fade Brick) |
| **Trend Filter** | `use_trend_filter` ☑, `trend_logic`, `ma_type`, `ma_length`, `source` |

### The naive comparison says the run structure is worthless. It isn't.

Stage 1 pooled each trigger over its whole grid and concluded that *Any New
Brick* — the control, with no run or reversal structure at all — **beat** both
structured triggers (54.57% vs 53.79% and 52.47%). That was an artifact of
averaging `min_run_bricks` from 1 to 8 together. Stage 2 ran the *same brick
grid* through every trigger, so the comparison is matched (132 grids, train only):

| Trigger | Bets | Hit | vs control |
|---|---:|---:|---:|
| Brick Reversal, run ≥ 8 | 108,710 | 57.98% | **+1.19pp** |
| Brick Reversal, run ≥ 5 | 280,062 | 57.42% | **+1.13pp** |
| Brick Run, run ≥ 8 | 88,978 | 57.13% | +0.56pp |
| Brick Reversal, run ≥ 3 | 576,241 | 56.81% | +0.51pp |
| Brick Reversal, run ≥ 2 | 850,997 | 56.61% | +0.26pp |
| *Any New Brick (control)* | 3,491,721 | 56.55% | — |
| Brick Reversal, run ≥ 1 | 1,270,608 | 56.09% | −0.13pp |

The pattern is real and monotone in run length, and only appears from run ≥ 3.
*Brick Run* (fade a run as it extends) is consistently worse than *Brick
Reversal* (fade the brick that breaks it) at the same length — the turn matters,
not just the run.

### Polymarket presets

Three sweep stages, 2,832 combinations, same DB and same protocol as above; 793
of 2,172 scored configurations passed admission. `Fixed` brick mode was excluded
from the sweep — over a record where BTC runs from ~$3k to ~$110k, one dollar
brick is absurdly coarse at one end and absurdly fine at the other.

| Preset | Bets | Hit | Train 17-23 | TEST 24-26 | 2025-26 | Worst yr | z |
|--------|-----:|----:|------------:|-----------:|--------:|---------:|--:|
| **PM 5m Volume** | 8,115 | 58.16% | 58.59% | **55.94%** | **56.01%** | **55.17%** | **14.2** |
| PM 5m Balanced | 3,844 | 59.81% | 60.81% | 56.07% | 57.58% | 54.45% | 11.9 |
| PM 5m Selective | 1,154 | 60.92% | 62.56% | 52.41% | 53.61% | 51.11% | 7.8 |
| PM 5m Hi Hit | 328 | 63.11% | 64.55% | 60.19% | 56.94% | 44.00% | 4.3 |

***Volume is the preset to use*** — it loses only 2.7 points train-to-holdout,
the smallest shrinkage in this file, holds 55.94% across 1,314 out-of-sample
bets, and **every calendar year in the record is at or above 55.17%**. It is also
the highest-volume tier, which is not the usual ordering here.

Findings beyond the numbers:

- **Fade Brick, overwhelmingly** — by 8-10 points on every trigger (54.57% vs
  44.93%, 53.79% vs 45.55%, 52.47% vs 46.84%). A Renko brick on BTC 5m is an
  overshoot, not a breakout.
- **Bigger bricks, better bets, to a point.** `brick_atr_mult` 1.0 → 6.0 gives
  56.26 / 57.14 / 57.25 / 56.32 / 56.04 / 55.28%; `brick_pct` 0.3 → 1.5 gives
  56.07 / 56.56 / 56.59 / 57.03 / 57.06%. ATR and Percent sizing perform about
  equally (56.60% vs 56.39%).
- **`reversal_bricks` ≥ 2 is worth ~0.7pp** over flipping on every brick (1/2/3/4
  → 56.15 / 56.84 / 56.81 / 56.72%). The classic Renko rule is right and there is
  nothing beyond it.
- **`max_new_bricks` earns nothing** — 0/1/2 give 56.48 / 56.53 / 56.50%. Worth
  knowing before reaching for it.
- **"With Trend" helps here**, opposite to Fib Retracement and Volume
  Exhaustion: With Trend EMA200 58.70% vs filter-off 57.89% vs Against Trend
  ~57.8%. With Fade Brick that reads as *fade DOWN bricks while price is above
  the EMA200* — buy dips in an uptrend.

⚠️ Same volume warning as Elliott Wave: the widest admissible net (82,807 bets,
54.24% train) falls to 50.80% on the holdout. **Selective is NOT RECOMMENDED** —
62.56% train against 52.41% holdout is a −10.2pp shrinkage. **Hi Hit is thin**
(~36 bets/year; 2026 so far is 44.0% on 25 bets). And **ATR-mode bricks are not
reproducible from one number** — brick size tracks recent volatility, so two runs
over different date ranges do not share a brick grid; Percent mode is scale-free
with a fixed yardstick, which is why most presets use it.

### …and refitted on the trailing 2 years

Same three stages re-run over 2024-07-28 → 2026-07-28 alone, same
stability-only admission (867 of 2,172 configurations passed):

| Preset | Last 2yr (fitted) | | 2017-2023 (unseen) | | Halves |
|---|---:|---:|---:|---:|:--|
| **PM 5m Volume - 2yr Train** | 1,852 | 56.80% | 11,143 | 57.92% | 55.41 / 58.58 |
| PM 5m Balanced - 2yr Train | 784 | 59.69% | 4,883 | 59.57% | 59.38 / 60.12 |

Both hold across the unseen years, *Balanced - 2yr Train* almost exactly (59.69%
vs 59.57%) — Renko's structure is stable across regimes in a way Elliott Wave's
was not. Over the whole record every single year clears 52% for both (scored
inside a full-history run, so 2024-26 differs slightly from the standalone table
above — see the brick-anchor caveat below):

| | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Volume - 2yr Train | 52.2% | 57.9% | 63.1% | 59.6% | 57.5% | 58.4% | 60.3% | 55.2% | 57.2% | 54.6% |
| Balanced - 2yr Train | 53.7% | 60.6% | 62.6% | 59.1% | 59.7% | 60.0% | 61.5% | 56.1% | 56.5% | 57.4% |

**Here the refit buys volume, not rate.** Unlike Elliott Wave the full-record
Renko presets have *not* decayed: *PM 5m Volume* still runs 58.09% over the
trailing 2 years, better per bet than the refit's 56.80% — but it only fires 964
times in that window against 1,852. So:

- best rate on recent tape → full-record **PM 5m Volume**;
- roughly double the bets for ~1.3pp → **PM 5m Volume - 2yr Train**;
- better on *both* axes than full-record Balanced (57.44% on 585) →
  **PM 5m Balanced - 2yr Train** (59.69% on 784).

The refit also softened the run requirement — *Volume - 2yr Train* uses
`min_run_bricks = 2`, below the run ≥ 3 threshold where the matched comparison
above found the structure starts paying. Over two years the shorter run wins on
volume; over nine it does not. Treat it as the volume knob it is.

⚠️ No out-of-sample evidence for the fit itself. And the **brick-anchor effect
is visible in these numbers**: scoring *Volume - 2yr Train* inside a full-history
run gives 55.38% versus 56.80% standalone — 1.4pp purely from where the grid is
anchored. The tables use the standalone run, which is what the dashboard gives
you for that date range.

## Support & Resistance (beyond the video)

*The oldest tool on the chart, drawn mechanically so it can be tested.* A
horizontal price the market has repeatedly turned at is the first thing anyone
learns to draw, and the hardest thing to backtest honestly — normally you see
which line worked and draw that one. Here the levels build themselves:

- every **confirmed fractal pivot**, high or low, is a candidate price;
- a pivot within `cluster_tol_atr` × ATR of an existing level **joins** it,
  pulling the level to the running mean of its members and incrementing its
  **touch count**; otherwise it starts a new level;
- only levels with at least `min_touches` members are tradeable — "two touches
  make a level" is the textbook rule, and it is a parameter here.

Highs and lows go into the **same** pool on purpose. A level's role is decided
per bar by where price sits: above the previous close it is resistance, below it
is support. Polarity flip — broken resistance becomes support — therefore falls
out of the representation instead of being special-cased. Only the **nearest**
level on each side is evaluated, since a further one cannot be reached without
passing it.

This is deliberately not [Trend Lines](#strategies), which joins two pivots into
one *sloping* line and re-anchors as new pivots print. There a level is two
points and lives until it is replaced; here a level is a **cluster** of any size,
scored by how many swings confirmed it, and horizontal. The measured overlap
between the two is small — see below.

| Group | Params |
|-------|--------|
| **Pivots** | `pivot_left`, `pivot_right` |
| **Levels** | `cluster_tol_atr`, `min_touches`, `max_level_age_bars`, `max_levels`, `retire_on_break`, `use_support`, `use_resistance` |
| **Trigger** | `use_break`, `break_buffer_atr`, `use_bounce`, `zone_tol_atr` |
| **Decision** | `predict_direction` (With Signal ⋁ Against Signal) |
| **Volatility** | `vol_atr_length`, `atr_pct_min`, `atr_pct_max` |
| **Trend Filter** | shared |

A fractal pivot at bar *j* is not knowable until bar `j + pivot_right`, so pivots
are admitted through a **confirmation cursor** that releases one only when the
scan reaches that bar. No level is ever built from a swing that had not yet
formed.

### Polymarket presets

Same protocol as Trend Lines and Reversal: train 2024-01 → 2025-10, freeze the
pick, then score 2025-10 → 2026-07 once. The years 2018–2024 were never loaded by
any sweep stage. **Read the hit rates against 49.76%, not 50%** — 0.48% of 5m
candles close exactly at their open and lose whichever side you take.

| preset | bets | hit | edge | train | HOLDOUT | UNSWEPT | z |
|--------|-----:|----:|-----:|------:|--------:|--------:|--:|
| **PM 5m Level Break Volume** | 40,578 | **56.04%** | +6.28pp | 54.18% | 55.56% | 57.59% | **+25.3** |
| PM 5m Level Break Confirmed | 16,772 | 55.15% | +5.39pp | 55.08% | 54.39% | 55.72% | +14.0 |

Stage A (36 configs) settled the family before any tuning, the same way this repo
keeps settling structure events: **break only, traded Against Signal** — 52.84%
on train against 47.42% for taking the break. **Bounces lost outright**, every
bounce-enabled variant landing between 49.8% and 50.8%; the "level held" event
carries nothing here. Stage B (6,480 configs) then tuned pivots, clustering,
touches and buffer under a rule fixed in advance (≥3,000 train bets, both train
halves >50%, `pivot_left` off the grid boundary, then maximise train hit), and
Stage C (1,536) re-ran the winners with `max_levels` and `max_level_age_bars`
extended past the grid they had pinned to. Those two axes turned out not to
matter — the whole rule-passing top 20 spans 0.9pp across every value of them —
so the tie was broken by taking the config *interior* on both rather than the one
on the new edge, at a cost of 0.53pp of train hit (~0.6 SE at n = 3,400).

**Train hit rate ranked the two presets backwards.** *Confirmed* won on train
(55.08% vs 54.18%) and is what the selection rule actually picked. Out of sample
it is the weaker of the two **on both axes at once** — *Volume* carries 2.4× the
bets *and* a higher hit rate on the holdout, on the unswept years, and over the
full record. Both ship; *Volume* is the pick.

### Three checks, all run after the picks were frozen

- **The mirror is symmetric.** Taking the break instead of fading it scores
  43.67% / 44.50% — as far *below* the ceiling as these are above it. A selection
  artifact would not produce a clean sign flip on the same bets.
- **No look-ahead.** Re-deriving 40 sampled signals per preset on the series
  truncated *at* the signal bar reproduced every one: **0 mismatches**.
- **It is not Trend Lines relabelled.** Against that strategy's own presets only
  **25–30%** of these signals are shared (Jaccard 21–24%), and the 28,512 bets
  *Volume* fires that Trend Lines never does score **55.70%** on their own. The
  exclusive half carries the edge, so this is a separate signal source rather
  than a sloping-line result rediscovered horizontally.

It is also not directional beta: bets run 47.2% long / 52.8% short and both sides
win (long 56.57%, short 55.56%) while 49.6–50.5% of all 5m candles close up in
every year.

**Where it fails.** 2017 (partial year, Aug–Dec, thin early Binance liquidity) is
the one losing year for *Volume* at 44.31%, **2.19pp below** its own 46.50%
ceiling. Every full year 2018–2026 clears, worst 53.94% in 2024 (+4.05pp). That
is the usual failure mode of a fade — a sustained one-way trend, in which broken
levels keep going — and it is the same year Trend Lines and Reversal fail.
*Confirmed* is the more robust of the two: it clears its ceiling in **every** year
including 2017 (+2.72pp), at 41% of the volume.

**The buffer is not a clean dial**, unlike Trend Lines'. On the full record it
dips before recovering, so `break_buffer_atr = 0.0` is a genuine peak rather than
the low end of a ramp — 56.04% at 40,578 bets, falling to 54.85% at 0.5, back to
55.99% at 1.2 on a quarter of the volume. Nothing beats 0.0 at any volume, so
both presets ship there.

**Not swept: the 1-minute interval.** `pivot_left` counts *bars*, so these
presets' 20/30 are 100–150 **minutes** on 5m and would be 20–30 minutes on 1m — a
different setup entirely. Trend Lines and Reversal both needed a separate 1m
sweep for exactly this reason; do that before running these on 1m tape.

As everywhere else in this repo, the EV per \$1 the dashboard reports at 0.50
odds assumes a 0.50 fill, which a real Polymarket book will not offer on a
directional 5m market. **Hit rate is the finding**; the EV figure is an upper
bound.

## Gann Angles (beyond the video)

*A fan of fixed-ratio rays from a swing pivot — and a measurement that the rays
are the part that doesn't work.* W. D. Gann projected lines forward from a
significant pivot at set price-per-time ratios: `1x1` (his "45° line"), the
steeper `2x1`…`8x1`, the shallower `1x2`…`1x8`. Price holding above a rising fan
was strength; losing a ray meant travel to the next.

**The scale problem, and what is done about it.** "45°" is not a property of
price — it is a property of the chart's aspect ratio. Rescale the y-axis and every
Gann angle moves. That is the standard and entirely fair criticism of the tool,
and Gann answered it by fixing a unit per market by hand (a cent a day, a dollar a
week). BTC has no such convention to inherit, so here:

```
one price unit per bar  =  unit_atr_mult × ATR(at the anchor bar)
```

which makes the fan invariant to price level and instrument, and reproducible.
What survives of Gann is the *shape* of the construction, not his degrees — and
`unit_atr_mult` becomes a free parameter that has to be fitted like any other.

There is one live fan per side: an **up-fan** on the latest confirmed pivot low
whose rays act as support, a **down-fan** on the latest pivot high whose rays act
as resistance. A newer pivot re-anchors it, so there is no discretion about which
pivot "worked". Each ray is traded on **break** (close pierces it by
`break_buffer_atr`, then the ray retires) or **bounce** (the extreme reaches it,
the close holds) — and `predict_direction` takes that at face value or fades it.

**Arming.** A fan's steep rays climb away from the anchor far faster than price
does, so within a few bars price is mechanically "below" the 8x1 without anything
having happened. Counting that as a break would manufacture signals out of the
geometry alone, so a ray is *armed* on the first bar its close is onside and only
an armed ray can fire. Rays price never reaches never fire — 8x1 alone produces
148 signals over two years against 14,384 for 1x1.

### The headline finding: the angles do not earn

Read every hit rate against the window's own **flat ceiling**, not 50%: 0.24% of
5m candles close exactly at their open and lose whichever side you take, so the
best a 50/50 bettor can do over the last two years is 49.88%.

`unit_atr_mult` sets how fast the 1x1 ray climbs, and it is the only parameter
here that matters much. Its marginal — mean TRAIN hit across every config sharing
that value — is monotone, and it points at zero:

| unit | mean hit | unit | mean hit | unit | mean hit |
|---|---|---|---|---|---|
| 0.0005 | 55.17% | 0.035 | 53.11% | 0.35 | 50.23% |
| 0.001 | 55.17% | 0.05 | 52.29% | 0.50 | 50.32% |
| 0.002 | 55.09% | 0.075 | 52.04% | 0.75 | 50.02% |
| 0.005 | 54.91% | 0.10 | 51.58% | 1.00 | 50.09% |
| 0.010 | 54.77% | 0.15 | 51.08% | 1.50 | 49.88% |
| 0.020 | 54.51% | 0.20 | 50.89% | 2.50 | 49.79% |

A steep fan is worth **nothing at all** — by `unit=1.5` the edge is gone entirely.
Flatten it and the edge appears, rising to a plateau at `unit ≤ 0.002`.

As `unit → 0` every ray flattens toward a horizontal line through the anchor, so
what the fitted optimum trades is the break of the last confirmed swing pivot
**level**. Two checks confirm the fan has genuinely collapsed rather than merely
flattened:

1. **The ray sets converge.** At `unit=0.0005` the 1x1 ray alone and the
   three-ray core score 55.23% and 55.11% on 2,588 and 2,617 bets — the same rate
   on the same trades. Adding six more rays multiplies the bet count by only 2.2×
   at `unit=0.005`, against 4.5× at `unit=0.2`, because near-flat rays sit on top
   of one another and fire on the same bars.
2. **The shipped geometry is flat by inspection.** At `unit=0.002` the 1x1 ray
   drifts 0.60 ATR across its entire 300-bar life — less than the 0.8 ATR break
   buffer *Balanced* requires. No meaningful angle is left.

This is the same shape of result [Harmonic Patterns](#harmonic-patterns-beyond-the-video)
reached about the Fibonacci ratios, and that Trend Lines reached about slope
(`require_direction=False` won there too): on BTC 5m the mechanically located
**level** carries the edge and the geometry drawn through it does not.

### Presets

Fitted on the trailing two years. `TRAIN 2024-07-30 → 2025-10-01` for selection
and only selection; `HOLDOUT 2025-10-01 → 2026-07-30` scored once after the picks
were frozen; `UNSWEPT 2018-01-01 → 2024-07-30` never consulted. Stage A (24
configs) settled the family as **break-only, faded** — fading beat taking the
break by 1.9pp, the third time this repo has landed there. Stage B (5,184 + 864 +
392 + 84 configs) tuned the rest, read off **marginals** rather than the argmax
(with ~3–20k bets per config the SE is 0.3–0.9pp, so the max over thousands of
draws is inflated ~3 SE by chance), and grids whose optimum hit a boundary were
extended rather than trusted — `unit` twice, buffer once, `pivot_left` once.

Selection rule, fixed before the holdout was read: ≥ 3,000 TRAIN bets; both TRAIN
halves above their own ceiling; `unit` at the plateau knee and off the grid edge;
then maximise TRAIN hit. Tiers vary **only** `break_buffer_atr`, so they differ in
selectivity rather than in a separately-fitted shape.

| Preset | 2yr bets | 2yr hit | edge | train | HOLDOUT | unswept | z |
|---|---|---|---|---|---|---|---|
| **PM 5m Volume** | 8,655 | 55.64% | +5.76pp | 55.60% | 55.71% | 58.65% | +10.7 |
| **PM 5m Balanced** | 5,412 | 56.54% | +6.66pp | 56.86% | 56.05% | 58.88% | +9.8 |
| PM 5m Selective | 2,604 | 55.65% | +5.76pp | 55.17% | 56.37% | 59.49% | +5.9 |
| *PM 5m Angled Fan* | 46,345 | 51.55% | +1.67pp | 50.90% | 52.50% | 53.49% | +7.2 |

*PM 5m Angled Fan* is **the control, not a recommendation** — a real, visible fan
at `unit=0.5` with all nine rays, the best such config on TRAIN. It is not a straw
man: +1.67pp on 46,345 bets at z +7.2 is a genuine edge, so an actual Gann fan
does carry something. It is simply worth ~5pp per bet *less* than switching the
angles off. It ships so the finding above can be checked rather than taken on
trust.

Per year, *Volume*: 2017 **44.68%** (ceiling 46.50%), then 58.31 / 59.62 / 59.50 /
58.19 / 59.30 / 58.61 / 55.72 / 56.01 / 55.61% for 2018–2026.

**Why this is probably real.** The holdout matches train on every tier (Volume
+0.11pp, Selective +1.20pp, Balanced −0.81pp). The 2018–2024 columns are
6,520–22,727 bets from years the sweep never touched, and they score *higher* than
the fitted window. It is not directional beta — bets run 49.5–50.7% long against a
49.88% ceiling. Two-year halves are close on every tier (Volume 55.48/55.83,
Balanced 57.03/56.01, Selective 55.28/56.04). Look-ahead is verified by
truncation: cutting the series at any signal bar reproduces that signal exactly,
on all four presets.

⚠️ **Largely redundant with Trend Lines.** Both end up fading the break of a
mechanically located pivot level — Trend Lines got there by finding flat lines beat
sloping ones, this by finding flat rays beat angled ones. Trend Lines' Volume tier
is 21,468 bets at +6.12pp against this file's 8,655 at +5.76pp: a similar edge with
2.5× the volume. Running both as Combined voters mostly double-counts one signal.

⚠️ **The edge decays.** Every tier scores ~3pp higher across 2018–2024 than over
the last two years (Volume 58.65% vs 55.64%). The two-year number is the live
estimate; the unswept column is evidence the mechanism is real, not a forecast.

⚠️ **2017 breaks Volume** — 44.68% against that year's 46.50% ceiling on 1,316
bets. 2017 was a parabolic run in which broken levels kept going: the standard
failure mode of a fade, and the same year that breaks Reversal and Harmonic.
*Balanced* merely matches its 2017 ceiling (46.53 vs 46.50); *Selective* clears it.

⚠️ **`break_buffer_atr` is a noisy dial, not an optimum.** Its curve is not
monotone: 0.8 is a real +1.20pp step at ~3,400 bets, but the apparent 58.56% at
buffer 2.0 sits on 1,127 bets (SE 1.5pp) with halves of 55.09/61.64 — noise, and
the ≥3,000-bet gate is what kept it out. **`max_anchor_age_bars` does nothing
measurable** (flat to 0.01pp across 100/300/600); it is 300 because a fan must
expire somewhere, not because 300 was selected. And *Selective* is thin — 2,604
bets over two years on a 1,036-bet holdout, not enough to separate 55% from 57%.

## Oscillators (beyond the video)

*Five textbook rules on one oscillator — four of them measure as worthless.* An
oscillator in the classic sense is a **bounded** indicator: it cannot trend away,
so it must turn, and "70" means the same thing in 2017 as in 2026. That
boundedness is the premise behind every rule the books teach, and this strategy
implements them all so they can be raced against each other:

- **Zone Entry** — the reading crosses *into* the overbought / oversold band;
- **Zone Exit** — it crosses back *out* (the rule most books actually
  recommend, on the grounds that overbought can stay overbought);
- **Signal Cross** — it crosses its own moving average;
- **Centerline Cross** — it crosses the midpoint, a regime flip;
- **Failure Swing** — Wilder's own pattern, and the one he singled out as a
  signal in its own right: an extreme, a pullback to a trough, a rally that
  fails to reclaim the extreme, then the break of that trough.

Seven oscillators are rescaled onto a common **0–100** axis, so the band levels,
the signal line and the failure-swing logic are written once and the oscillator
becomes a parameter instead of a fork in the code: RSI, Stochastic %K, Stoch RSI
%K and the Ultimate Oscillator are native; Williams %R is `%R + 100`; CCI and TSI
map through `50 + 50 × clamp(x / scale)`.

This is deliberately not [Momentum Indicators](#momentum-indicators-beyond-the-video),
which averages nine oscillators into a composite and trades that. Here exactly
**one** oscillator is read and the question is which *rule* pays — the axis a
composite hides. It is not [Reversal](#reversal-beyond-the-video) either, whose
oscillator leg is divergence against price; nothing here looks at price shape.

| Group | Params |
|-------|--------|
| **Oscillator** | `osc_type`, `osc_length`, `smooth_k`, `signal_length` |
| **Zones** | `overbought`, `oversold` |
| **Trigger** | `trigger_mode`, `fs_max_bars` |
| **Decision** | `predict_direction` (Fade ⋁ Follow) |
| **Volatility** | `vol_atr_length`, `atr_pct_min`, `atr_pct_max` |
| **Trend Filter** / **Window** | shared |

### Polymarket presets

Train 2024-07-31 → 2025-11-01, freeze the picks, then score 2025-11-01 →
2026-07-30 once; 2017-08 → 2024-07 was never loaded by any sweep stage. 2,040
configurations in three stages — 210 structural (oscillator × trigger ×
direction × length), 1,680 tuning length/smoothing/band inside the winning
family, 150 testing the ATR band and trend filter on the frozen winners.
Selection was mechanical: train bets ≥ the tier floor, **both** halves of train
above 52%, `osc_length` off the grid boundary, then highest train hit.

**Read the hit rates against 49.52%, not 50%** — 0.48% of 5m candles close
exactly at their open and lose whichever side you take.

| preset | oscillator | bets | hit | train | HOLDOUT | UNSWEPT | z |
|--------|-----------|-----:|----:|------:|--------:|--------:|--:|
| PM 5m Volume | Stochastic 11, band 10/90 | 56,853 | 56.15% | 54.35% | 55.03% | 56.75% | **+29.3** |
| **PM 5m Balanced** | **RSI 14, band 30/70** | 20,988 | **56.95%** | 56.07% | 55.91% | 57.25% | +20.1 |
| PM 5m Selective | TSI 7, band 10/90 | 5,247 | 56.93% | 56.90% | 55.74% | 57.08% | +10.0 |

**Balanced is the pick — and it is Wilder's published RSI defaults, unchanged.**
14 bars, a 70/30 band, no smoothing. The sweep was free to choose among six
oscillators, ten lengths, four smoothings and seven band widths, and what it
landed on at the middle tier is the setting printed in the 1978 book.

### The rule the textbooks recommend is the one that loses

Best train config per trigger (Fade, ≥1,000 bets):

| trigger | train | the same RSI settings, on the holdout |
|---------|------:|-------------------------------------:|
| **Zone Entry** | **55.84%** | **55.97%** |
| Zone Exit | 53.46% | 50.49% |
| Failure Swing | 52.33% | 45.76% |
| Signal Cross | ~50% | ~50% |
| Centerline Cross | ~50% | ~50% |

Out of sample the gap widens rather than closing. This is worth dwelling on,
because **the band exit is the rule the books actually teach** — *overbought is
not a sell signal until the oscillator crosses back down* — and it is precisely
the wait that destroys the edge. By the time the reading has climbed back out of
the band, the reversion it was predicting has already happened. So has the entire
finding.

Wilder's **failure swing** is the sharpest negative here: 53.88% on train,
**45.76%** on the holdout. It is the most elaborate pattern in the family and it
is a curve fit. The two crossover triggers are noise at every setting tried —
neither the signal line nor the centreline carries anything at 5 minutes.

### Both marginals are monotone

Pooled over all 1,680 stage-2 configs, train hit rises with the band and with
smoothing, without a single inversion:

| band | 65 | 70 | 75 | 80 | 85 | 90 | 95 |
|------|---:|---:|---:|---:|---:|---:|---:|
| train hit | 50.72% | 50.88% | 51.15% | 51.31% | 51.45% | 51.78% | 52.07% |

| smoothing | 1 | 2 | 3 | 5 |
|-----------|--:|--:|--:|--:|
| train hit | 50.45% | 51.53% | 51.68% | 51.79% |

Rarer and cleaner readings are better readings. A monotone marginal cannot be
produced by one lucky cell, so this is the result here that carries weight
independent of the selection rule.

**RSI is the best oscillator; Stoch RSI is the worst.** Pooled over stage 2,
train / holdout: RSI 53.51 / 54.16, TSI 53.11 / 53.48, Ultimate 52.35 / 53.67,
CCI 51.82 / 52.32, Stochastic 51.10 / 52.50, Stoch RSI 50.23 / 51.26. Ranking the
RSI *of* the RSI below plain RSI is the expected direction — re-ranging an
already-bounded reading against its own range adds noise, not information — but
it is worth having measured rather than assumed.

**Stochastic %K and Williams %R are the same number**, verified to 1.4 × 10⁻¹⁴
over 200,000 bars: `%K = 100(c−LL)/(HH−LL)` and `%R + 100` are the same
expression. Both are offered because both get asked for by name; picking between
them is a choice of label.

**The trend filter buys nothing** — pooled over stage 3 it spans 54.59–55.13% on
train with no ordering that survives the holdout, and for RSI the *Against Trend
/ SMA50* rows are byte-identical to the unfiltered ones, because fading an
overbought extreme is definitionally against the short-term trend and that filter
passes 100% of signals. The ATR band is likewise flat (~1pp across everything
tried), so the repo default 0.05–1.5 is kept rather than fitted.

### Three checks, all run after the picks were frozen

- **The mirror is symmetric.** Taking the extreme at face value instead of fading
  it scores 43.27% / 42.85% / 42.61% — as far below the ceiling as the presets
  are above it. A selection artifact does not produce a clean sign flip on the
  same bets.
- **No look-ahead.** The prefix test (signals from `candles[:m]` must equal the
  whole-series signals falling before *m*) passes with **0 mismatches** across
  all 7 oscillators × 5 triggers at three cut points.
- **It is not Momentum Indicators relabelled.** Against that strategy's Balanced
  preset only 9–22% of these signals are shared (Jaccard 6–16%), and the
  exclusive majority scores 55.9–56.7% alone. Against CCI Williams: 14–19%
  shared, exclusive half 55.6–56.6%. The edge lives in the bets the other
  strategies never place.

It is not directional beta either: bets split 45–50% long and both sides win
(Balanced: long 57.98%, short 55.93%) while 49.6–50.5% of all 5m candles close up
in every year.

**Where it fails.** 2017 (partial year, Aug–Dec, thin early Binance liquidity) is
the one losing year — 47.4% / 47.2% / 52.0%. Fading an extreme loses in a
parabolic run, which is what 2017 was; this is the same year that breaks Momentum
Indicators, Support & Resistance and Reversal, so treat it as one shared regime
risk rather than four warnings. Every full year 2018–2026 clears: worst 54.4%
(Volume), 55.3% (Balanced), 52.7% (Selective). **The edge decays** — 2018–2023
runs 55.2–60.4%, 2024–2026 runs 52.7–58.1%, and the recent figures are the live
estimate.

**Not swept: the 1-minute interval.** `osc_length` counts *bars*, so Balanced's
14 is 70 minutes on 5m and would be 14 minutes on 1m — a different setup that
needs its own sweep.

As everywhere else here, the EV per \$1 the dashboard reports assumes a 0.50 fill,
which a real Polymarket book will not offer on a directional 5m market. **Hit
rate is the finding**; the EV figure is an upper bound.


## Volume Exhaustion (strategy #7)

*Fade the climax bar.* A decisive bar printed on abnormally heavy volume is often
the **end** of a move rather than the start of one — the crowd that wanted in has
just piled in. Because BTC's raw volume grows by orders of magnitude across the
history, "abnormal" is measured two scale-free ways at once: **relative volume**
(bar volume ÷ its own rolling mean) and **volume percentile** (its rank inside a
longer window, robust to a single outlier dragging that mean).

| Group | Params |
|-------|--------|
| **Volume** | `vol_ma_length`, `vol_spike_mult` (× rolling avg), `vol_rank_lookback`, `vol_rank_min` (percentile gate; 0 disables) |
| **Candle** | `min_body_ratio` (the bar must be decisive), `wick_min` (rejection wick; 0 disables) |
| **Volatility Filter** | `vol_atr_length` (also sizes TP/SL), `atr_pct_min`, `atr_pct_max` |
| **Trend Filter** | `use_trend_filter` ☑, `trend_logic` (With/Against), `ma_type` (SMA/EMA/WMA/RMA), `ma_length`, `source` |
| **Decision** | `predict_direction` (Reversion ⋁ Continuation) |

### Polymarket presets

Swept over the whole DB (936,841 5m bars, ~242k combinations), same admission
rules as CCI Williams — win every calendar year, clear 53% in 2024-26 alone, be
statistically significant:

| Preset | Bets | Hit | 2024-26 bets | 2024-26 hit | z |
|--------|-----:|----:|-------------:|------------:|--:|
| **PM 5m Volume** | 64,894 | 54.52% | 19,494 | 53.07% | 23.0 |
| **PM 5m Balanced** | 38,149 | 55.82% | 11,244 | 54.70% | 22.7 |
| **PM 5m Selective** | 24,513 | 56.28% | 7,825 | 55.19% | 19.7 |
| **PM 5m Hi Hit** | 9,415 | 56.40% | 1,772 | 57.51% | 12.4 |
| **PM 5m Max Hit** | 1,062 | 57.16% | 230 | 66.09% | 4.7 |

Two structural findings shaped these. **Reversion only** — of 9,221 combinations
that passed the filters, *all* 9,221 were Reversion and none were Continuation;
fading the climax is the edge, riding it is the same edge inverted. And
**Against Trend helps** — only fading an up-climax while price is *above* the MA
(and vice versa) stacks a second mean-reversion condition, worth about a point
of hit rate at equal volume.

⚠️ **Max Hit is the thinnest result in this repo** — z of 4.7 against 20+ for the
others, ~120 bets/year, and its edge sits almost entirely in 2023-26. Treat it as
a lead to validate rather than a settled edge. *Hi Hit* is the best
risk-adjusted pick: worst year 52.2% at z=12.4.

## CCI Williams (strategy #9)

*Two oscillators must agree.* **CCI** says how far the typical price has stretched
from its own mean (in units of that window's average deviation); **Williams %R**
says where the close sits inside the window's high-low *range*. Either alone
fires constantly in a trend — together they pin down the exhaustion state:
stretched from the mean **and** stuck at the range extreme. An optional candle
filter then demands visible rejection, and a volatility band skips dead tape.

| Group | Params |
|-------|--------|
| **Core** | `cci_length`, `cci_threshold`, `wr_length`, `wr_overbought`, `wr_oversold` |
| **Candle** | `use_wick_confirm` ☑, `wick_min` (rejection wick / range), `close_recover_min` (how far the close backed off the extreme) |
| **Volatility** | `vol_atr_length` (also sizes TP/SL), `atr_pct_min`, `atr_pct_max` |
| **Decision** | `predict_direction` (Reversion ⋁ Continuation) |

`%R` runs **-100…0**, so "overbought" is the *less negative* end (e.g. `-20`) and
oversold the more negative (`-80`). Up-exhaustion = CCI ≥ +threshold **and**
%R ≥ overbought; the down mirror uses CCI ≤ −threshold and %R ≤ oversold.
**Reversion** fades that, **Continuation** rides it.

### Polymarket presets

Five presets tuned for **Polymarket up/down** mode (interval 5m) sit on a
volume-vs-hit-rate frontier, fitted over the **entire** local DB — 936,841 5m
bars, 2017-08 → 2026-07:

| Preset | Bets | Hit | 2024-26 bets | 2024-26 hit |
|--------|-----:|----:|-------------:|------------:|
| **PM 5m Volume** | 98,089 | 56.68% | 32,230 | 54.01% |
| **PM 5m Balanced** | 59,099 | 57.15% | 18,008 | 55.26% |
| **PM 5m Selective** | 24,553 | 58.60% | 8,273 | 56.82% |
| **PM 5m Hi Hit** | 13,518 | 59.48% | 2,709 | 58.10% |
| **PM 5m Max Hit** | 1,458 | 60.36% | 285 | 63.51% |

Each had to win in *every* calendar year, clear 53% in 2024-26 on its own, and
be statistically significant — not just look good in aggregate. Two honest
caveats: **the edge decays** (every preset is several points weaker in 2024-26
than in 2018-23, so read that column, not the headline), and **2017 is the weak
year** at ~50% for all but *Max Hit*. Since a bet only pays when hit rate beats
your odds, *Selective*'s 56.8% recent hit needs entry below ~0.568 to be +EV.

## Jump Exhaustion (strategy #8)

*"Fade the overshoot."* An abnormal (jump) candle that pushes to a local extreme,
prints a rejection wick, and shows stretched RSI is often exhausted, so we fade it.

Parameter groups match the video's config screen:

| Group | Params |
|-------|--------|
| **Core** | `atr_length`, `jump1_atr_mult` (min jump size in ATRs), `jump2_atr_mult` (max — bigger moves are **not** faded) |
| **Candle** | `close_extreme_min` (close near the local high/low), `wick_min_ratio` (rejection wick as a fraction of range) |
| **RSI** | `rsi_length`, `rsi_overbought`, `rsi_oversold` |
| **Volatility** | `vol_atr_length`, `atr_pct_min`, `atr_pct_max` (trade only inside a volatility band) |
| **Day of Week (UTC)** | `trade_mon` … `trade_sun` ☑ — which UTC weekdays may fire |

The `jump2_atr_mult` upper bound is deliberate: on the very biggest moves price
tends to keep going rather than revert, so those are excluded from fading.

### Saturday

This is the one strategy here that cares *when* the jump happens, and the day
that stands out is **Saturday**. "Best of 7 days" always produces a winner, so
the claim was tested four ways before any parameter was tuned on it:

1. **Control.** Raw 5m bar direction has no day bias — P(close>open) is 49.82 /
   49.91 / 50.21 / 49.79 / 49.94 / 49.87 / 49.79 % Mon…Sun across all 936,829
   bars. The effect is in the setups, not the tape.
2. **Persistence.** On the video's *Aggressive* preset (32,714 bets) Saturday
   beats the other six days in **nine of ten** calendar years; only 2026, a
   partial year, is negative (−0.19pp). Overall +2.65pp, two-proportion z=+3.19.
3. **Permutation.** Shuffling day labels 2,000×, the best day looks this good by
   chance in 0.1% of draws (p=0.001). Chi-square 18.4 on 6 df.
4. **Out-of-sample.** Saturday picked on 2017-2023 alone, scored on 2024-2026:
   56.67% vs a 54.51% all-days baseline.

Tuesday and Friday also look good in-sample and **fail** step 4 — tier winners
including them dropped from ~61% on 2017-2023 to ~55% on 2024-26 while the
Saturday-only picks held. That is why the presets are Saturday-only rather than
"the best three days". With *Sat Hi Hit*'s parameters, by day over all history:

| Mon | Tue | Wed | Thu | Fri | Sat | Sun |
|----:|----:|----:|----:|----:|----:|----:|
| 55.1% | 57.2% | 57.7% | 56.5% | 56.6% | **60.4%** | 57.8% |

### Polymarket presets

10,368 parameter combinations × 127 day-subsets. Parameters **and** days were
chosen on 2017-2023 only and 2024-2026 scored afterwards, so the TEST column is
genuinely out-of-sample. Admission: win every calendar year, z ≥ 2.5 on train.

| Preset | Bets | Hit | Train 17-23 | TEST 24-26 | Worst yr | z |
|--------|-----:|----:|------------:|-----------:|---------:|--:|
| **PM 5m Sat Hi Hit** | 3,356 | 60.31% | 60.67% | **59.46%** | **53.24%** | 11.9 |
| **PM 5m Sat Volume** | 6,325 | 59.19% | 60.14% | 56.97% | 50.78% | 14.6 |
| **PM 5m All Days** | 29,185 | 57.20% | 58.14% | 55.03% | 52.96% | 24.6 |

***Sat Hi Hit* has the best recent hit rate in the repo** — 59.46% across 1,004
out-of-sample bets, every year from 2017 to 2026 between 53.2% and 64.9%. It pays
for that in volume: Saturday is one day in seven, so the ceiling is ~470 bets a
year. *All Days* is the same parameters with the gate open — it shows what the
day filter is worth (+3.1pp) and serves when you want bet count over edge.

Two things the sweep **rejected**, both from the video's setup:

- **The rejection wick earns nothing.** Every winning combination sets
  `wick_min_ratio = 0`.
- **So does the ATR% regime filter** — the best combinations run it wide open.
  The work is done by the jump-size floor plus stretched RSI.

Caveats: days are **UTC** and a bar is stamped by its open time, so another
timezone will not reproduce this. The edge decays here too (~60-65% in 2018-2023
vs ~59% in 2024-26). And *why* Saturday rather than Sunday is not explained by
anything measured — thin weekend books are the obvious guess, but Sunday is only
middling at 57.8%, so treat the mechanism as unknown and the effect as empirical.

## BB Squeeze (strategy #4)

*Trade the coil.* When Bollinger Bands contract (a "squeeze"), volatility is
compressed and a sharp move often follows. The strategy watches **%B** (where the
close sits inside the bands) while **bandwidth** is in a low percentile of its
recent range, and fires in the direction chosen by the **Decision** group —
**Breakout** (go with the band push) or **Reversion** (fade the band tag). A
stack of optional filters then refines entries.

Parameter groups match the video's config screen:

| Group | Params |
|-------|--------|
| **Bollinger Bands** | `bb_length`, `bb_mult`, `pctb_upper`, `pctb_lower` |
| **Squeeze** | `bw_lookback`, `bw_squeeze_pct`, `require_squeeze` ☑ |
| **EMA Bias** | `ema_bias_length`, `ema_bias_slope_bars`, `use_ema_bias` ☑ |
| **Body Filter** | `min_body_ratio` |
| **Volatility Filter** | `vol_atr_length` (also sizes TP/SL), `vol_min_atr_pct`, `vol_max_atr_pct` |
| **Decision** | `predict_direction` (Breakout ⋁ Reversion) |
| **Allowed Trading Window** | `use_trading_window` ☑, `trade_mon…trade_sun` ☑, `start/end_hour`, `start/end_minute` (UTC, wrap-aware) |
| **Trend Filter** | `use_trend_filter` ☑, `trend_logic` (With/Against), `ma_type` (SMA/EMA/WMA/RMA), `ma_length`, `source` (close/hl2/…) |

Presets: **Squeeze Breakout**, **Mean Reversion**, **Trend-Filtered Breakout**.

## Zscore MS (strategy #5)

*Fade the statistical stretch.* A z-score measures how many standard deviations
price sits from its own mean:

```
z = (close - SMA(close, z_sma_length)) / StdDev(close, z_std_length)
```

A large `|z|` means price is stretched; that stretch is optionally confirmed by a
**Keltner Channel** break, so a signal needs to be extended on both a statistical
*and* a volatility basis. **Decision** then picks whether to fade it
(**Reversion**) or ride it (**Momentum**). The SMA and StdDev lookbacks are
separate on purpose — a short mean with a long deviation window measures
"far from recent price, relative to normal volatility".

| Group | Params |
|-------|--------|
| **Z-Score** | `z_sma_length`, `z_std_length`, `z_upper`, `z_lower` |
| **Keltner Channel** | `kc_ema_length`, `kc_atr_length`, `kc_mult`, `require_kc_break` ☑ |
| **Bias MA** | `bias_ema_length`, `bias_slope_lookback`, `use_bias_ma` ☑ |
| **Volatility Filter** | `vol_atr_length` (also sizes TP/SL), `vol_min_atr_pct`, `vol_max_atr_pct` |
| **Decision** | `predict_direction` (Reversion ⋁ Momentum) |
| **Allowed Trading Window** | shared — see `strategies/common.py` |
| **Trend Filter** | shared — see `strategies/common.py` |

Presets: **Polymarket 5m (Reversion)**, **Polymarket 5m (Best Days)**,
**Strict Reversion**, **Loose Reversion**, **Momentum**.

## Regime Switch (strategy #6)

*Different market, different playbook.* It measures whether the market is
**trending or ranging**, then applies the matching logic to the same trigger — a
Donchian break of the previous `channel_length` bars:

- **Trending regime** → the break is real → trade **with** it (momentum)
- **Ranging regime** → the break is noise → **fade** it (reversion)

Three interchangeable regime detectors, all normalised to a 0-100 **trend score**
so one threshold works for any of them: **ADX** (used as-is, classic cut 25),
**Efficiency Ratio** (Kaufman net-move/path ×100), and **Volatility Ratio**
(fast ATR / slow ATR ×50, so 50 = flat).

| Group | Params |
|-------|--------|
| **Regime Detector** | `regime_method`, `regime_length`, `regime_threshold`, `trade_trend_regime` ☑, `trade_range_regime` ☑ |
| **Entry Channel** | `channel_length`, `breakout_buffer_atr`, `min_body_ratio` |
| **Decision** | `regime_mapping` — switch by regime, invert, or force Always Reversion / Always Momentum |
| **Volatility Filter** | `vol_atr_length` (also sizes TP/SL), `vol_min_atr_pct`, `vol_max_atr_pct` |
| **Allowed Trading Window** | shared — see `strategies/common.py` |
| **Trend Filter** | shared — see `strategies/common.py` |

The `Always Reversion` / `Always Momentum` mappings let the regime detector act
purely as a **filter** (which bars to trade) rather than a direction switch —
which is what the tuned Polymarket presets use, since on BTC 5m a channel break
during a high-efficiency stretch tends to snap back rather than continue.

Presets: **Polymarket 5m (Reversion)**, **Polymarket 5m (Best Days)**,
**Adaptive (both regimes)**, **Range Only (fade)**, **Trend Only (momentum)**,
**Efficiency Ratio**.

## RSI + BB (strategy #1)

*Fade the band stretch.* A classic mean-reversion fade: price stretches to a
Bollinger Band, RSI is at an extreme, and the bar closes back off the extreme.

| Group | Params |
|-------|--------|
| **Direction** | `direction` (Both ⋁ Long Only ⋁ Short Only) |
| **RSI** | `rsi_length`, `rsi_overbought`, `rsi_oversold` |
| **Bollinger Bands** | `bb_length`, `bb_mult`, `pctb_upper`, `pctb_lower` |
| **Candle** | `min_wick_ratio`, `min_close_recovery` |
| **Bias Filter** | `use_bias_filter` ☑, `bias_ema_length`, `bias_slope_bars` |
| **Volatility** | `vol_atr_length`, `atr_pct_min`, `atr_pct_max` |
| **Trend Filter** | `use_trend_filter` ☑, `trend_logic`, `ma_type`, `ma_length`, `ma_source` |
| **Day of Week (UTC)** | `trade_mon` … `trade_sun` ☑ |

### The weekend, not Saturday

Band fades resolve better at the **weekend** than midweek. Measured on the two
pre-existing PM presets, weekend (Sat+Sun) vs weekday:

| | Full 2017-26 | 2024-26 | 2025-26 |
|---|---:|---:|---:|
| Weekend vs weekday | **+2.71 / +2.87pp** (z=2.17 / 2.55) | +1.81 / +1.37 | +1.77 / +1.99 |
| Saturday alone vs rest | +3.21 / +3.48pp | **−1.46 / −1.14** | −1.60 / −0.28 |

Saturday alone looks *better* on the full record — and its edge has since gone
negative, with Sunday becoming the strongest day. Gating on Saturday would be
fitting to stale history, so the presets gate on the weekend as a pair, which is
positive on every span. (Jump Exhaustion is the opposite case: there Saturday
specifically still holds.) Monday is the worst day in both presets, both spans.

### Polymarket presets

A 15,552-combination sweep over the whole DB, scored in Polymarket up/down mode.
Two families of three tiers. Admission: hit >50% every calendar year, overall
z ≥ 2.5, and 2024-26 must still clear 52%.

| Preset | Bets | Hit | Worst yr | 2024-26 | 2025-26 | z |
|--------|-----:|----:|---------:|--------:|--------:|--:|
| **PM 5m Volume** | 22,569 | 58.31% | 51.54% | 56.82% | 56.59% | **25.0** |
| **PM 5m Balanced** | 10,977 | 58.70% | 51.95% | 57.16% | 56.23% | 18.2 |
| **PM 5m Hi Hit** | 734 | **64.03%** | 58.33% | 69.33% | 66.25% | 7.6 |
| **PM 5m Wknd Volume** | 7,057 | 59.13% | 53.41% | 56.27% | 56.41% | 15.3 |
| **PM 5m Wknd Balanced** | 4,211 | 60.58% | **54.91%** | **58.42%** | **59.16%** | 13.7 |
| **PM 5m Wknd Hi Hit** | 991 | 62.06% | 56.58% | 60.32% | 57.50% | 7.6 |

Weekend gating beats all-days at the Volume and Balanced tiers (59.13 vs 58.31,
60.58 vs 58.70) on about a third of the bets — a real quality-for-quantity trade.
*Wknd Balanced* is the pick of the six: every year above 54.9%, and the only one
whose 2025-26 figure beats its 2024-26.

Two findings beyond the numbers:

- **Long Only wins.** Four of six tier winners are Long Only. Buying the oversold
  lower-band fade beats fading the overbought upper band on 5m BTC.
- **The candle filters earn nothing.** Every winner sets `min_wick_ratio = 0`
  *and* `min_close_recovery = 0` — neither the rejection wick nor the recovery
  close survives measurement.

**Caveats.** These were selected on the full record with **no holdout**, so the
headline hit rates carry selection bias and the 2024-26 / 2025-26 columns are a
recency check rather than out-of-sample evidence — budget a few points of
shrinkage. The Hi Hit tiers are thin (734 and 991 bets, ~80-110/year); *PM 5m
Hi Hit* shows 69.33% over 2024-26 but on only 150 bets (±4pp standard error), so
treat it as suggestive. Days are UTC.

## Adding another strategy

1. Create `backend/strategies/<name>.py` with a `Strategy` subclass implementing
   `param_groups()` and `generate_signals(candles, params)`.
2. `register()` it in `backend/strategies/__init__.py`.

That's it — it appears in the dropdown and its params render automatically. Params
support four `kind`s — `int`, `float`, `bool` (checkbox), and `enum` (dropdown,
via `options=[…]`) — so a strategy can expose toggles and choices, not just
numbers. Add the new id to `SUB_IDS` in `strategies/combined.py` as well if it
should be offered as a Quick Setup voter.

All ten of the video's strategies are implemented; `Fair Value Gap`,
`Fib Retracement`, `Candlesticks`, `Reversal`, `Harmonic Patterns`,
`Momentum Indicators`, `Elliott Wave`, `Renko` and `Oscillators` are additions beyond them,
held to the same evidence bar. A strategy whose
signals depend on structure that is only knowable *after* the fact (a swing
pivot, a Renko brick) must record when it became knowable and gate on that — see
`zigzag()` in `elliott_wave.py`, or the confirmation cursor in `harmonic.py`. The
check that this worked is a **prefix test**: signals generated from `candles[:m]`
must be exactly the signals from the whole series that fall before `m`.

## Layout

```
backend/
  main.py            FastAPI app + routes + static serving
  store.py           DB-backed candle reader: resample-from-1m + live gap-fill
  pm_store.py        Polymarket window/quote reader (coverage, quote_at, …)
  db.py              SQLite connection + schema (candles, ingest_log, pm_window, pm_quote)
  binance.py         Binance klines (stdlib urllib, paginated, host fallback)
  chainlink.py       Chainlink Data Streams client (BTC/USD, HMAC-signed)
  data/
    ingest.py            bulk-loader: data.binance.vision zips -> SQLite (idempotent)
    ingest_chainlink.py  Chainlink Data Streams -> BTCUSD_CL candles (live/backfill)
    ingest_stream.py     pmqb stream.jsonl -> BTCUSD_CL candles + pm_window/pm_quote
    basis_report.py      Binance vs Chainlink price/direction basis
  indicators.py      ATR / RSI / extremes / MAs / std / percentile (pure Python)
  engine.py          backtest engine + shared Exit/Backtest params
  polymarket.py      binary (Polymarket up/down) backtest scorer
  registry.py        strategy registry
  strategies/
    base.py          Strategy base class, Param / ParamGroup / Signal
    jump_exhaustion.py
    bb_squeeze.py
    cci_williams.py
    volume_exhaustion.py
    multi_horizon.py
    fair_value_gap.py    beyond the video: 3-candle imbalance retest
    fib_retracement.py   beyond the video: swing-leg Fibonacci retracement
    candlesticks.py      beyond the video: nine candlestick pattern families
    reversal.py          beyond the video: structure/candle reversal evidence
    harmonic.py          beyond the video: XABCD harmonic patterns (PRZ entry)
    momentum.py          beyond the video: nine momentum oscillators, normalised
    choch.py             beyond the video: CHoCH/BOS market-structure breaks
    moon_phase.py        beyond the video: measured null (lore-only baseline)
    elliott_wave.py      beyond the video: causal zigzag impulse-wave counting
    renko.py             beyond the video: close-based brick runs and reversals
    combined.py      meta-strategy: N-of-M agreement (Quick Setup tab)
    common.py        shared param groups (trading window, trend filter, MA/source)
    __init__.py      registers strategies (add new ones here)
frontend/
  index.html  style.css  app.js  lightweight-charts.js (vendored)
```

## Notes / caveats

- The chart's markers show **executed** trades. `signals` in the stats counts
  every raw signal; some are skipped while a position is open.
- Win rate on short samples is noise — use a wide date range before trusting it.
- Binance klines are UTC; dates in the UI are treated as UTC.
- This backtests a spot-style TP/SL bet on BTC candles. It is a research tool,
  not wired to any live venue or to Polymarket resolution.
```
