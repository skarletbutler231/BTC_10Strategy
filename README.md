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
| + | [Gann Angles](#gann-angles-beyond-the-video) | Fan from a pivot — **the angles measure as worthless on 5m**; the level earns. On 15m the angles earn too |
| + | [Oscillators](#oscillators-beyond-the-video) | One banded oscillator, five textbook rules — **only the band entry earns** |
| ⊕ | Combined (Agreement) | Meta-strategy: require N of the above to confirm each other |

Every strategy also carries a **PM 15m** preset for the Polymarket 15-minute
market, fitted on the latest six months with the 8.5 years before scored
once as the out-of-sample check — one table of all of them, and the
cross-strategy findings, in [15-minute presets](#15-minute-presets-fitted-on-the-latest-six-months).

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
market.db updater (`stream` = Chainlink + Polymarket, `twap` = Chainlink TWAPs,
`binance`/`binance1m`/`binance1s` = Binance candles, `pmdata` = Polymarket L2
order book, `all` = every job). Each job takes its own `flock` lock, so the fast
and slow jobs run at their own cadences without ever colliding:

```cron
* * * * *    <proj>/run_updaters.sh stream    >> <proj>/data/ingest_stream.log 2>&1
* * * * *    <proj>/run_updaters.sh twap      >> <proj>/data/twap_ingest.log 2>&1
* * * * *    <proj>/run_updaters.sh binance1s >> <proj>/data/binance_1s.log 2>&1
* * * * *    <proj>/run_updaters.sh binance1m >> <proj>/data/binance_1m_tail.log 2>&1
*/30 * * * * <proj>/run_updaters.sh binance   >> <proj>/data/binance_ingest.log 2>&1
40 1 * * *   <proj>/run_updaters.sh pmdata    >> <proj>/data/pmdata_ingest.log 2>&1
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

## Chainlink 30s + 60s TWAPs (`ingest_twap` -> `cl_twap`)

Polymarket **settles** the BTC 5m market on a Chainlink TWAP, not on spot — the
30s stream until `2026-08-14 00:00 UTC`, the 60s stream after it. pmqb writes
both averages on every snapshot line, and `ingest_twap` folds them into one row
per captured second:

```bash
python3 -m backend.data.ingest_twap            # backfill (first run) / append (later)
python3 -m backend.data.ingest_twap --reset    # rescan from offset 0
```

| `cl_twap` column | |
|---|---|
| `time` | capture second, unix seconds UTC (**primary key**) |
| `twap30` / `twap60` | Chainlink BTC/USD 30s and 60s TWAP as of `time` |
| `obs_ts` | Chainlink's own observation stamp for that report |
| `obs_win` | which window `obs_ts` belongs to — `30` before the cutover, `60` after |

**Loaded as of 2026-08-20** — `2026-08-04 05:30 .. now`, **1.41M rows**, 99% of
every second (the feed publishes ~0.95 reports/sec, so some seconds have none).
The series starts on 08-04 because that is when pmqb began recording the fields.
`obs_ts` runs ~1.5 s behind `time`: that is our ingestion lag, and keeping it on
the row is what makes it measurable rather than assumed.

Sanity check against Polymarket's own strikes — joining `cl_twap` to
`pm_window.start_price` at the window boundary:

| | vs `twap30` | vs `twap60` |
|---|---|---|
| windows **before** the cutover | **MAE $0.62** | MAE $3.36 |
| windows **after** the cutover | MAE $3.13 | **MAE $0.85** |

i.e. the right column wins on the right side of `2026-08-14`, which is both a
check on the data and a reminder that **a query spanning that date must switch
columns at it**.

> Deliberately **not** in `candles`. These are smoothed averages — a 30s TWAP
> lags spot by ~15 s, and differencing a 30s-averaged series recovers only ~91%
> of true volatility — so they must never be mistaken for the `BTCUSD_CL` spot
> series sitting next to them. Kept per-second rather than per-minute because the
> thing they are *for* is the boundary-aligned settlement price, and a 1m OHLC
> fold destroys the boundary second.
>
> It is also a **separate job from `stream`**, with its own cursor under a
> `twap:` prefix in `stream_cursor`. `ingest_stream`'s cursor was already parked
> at the end of an 11 GB file, so teaching it about TWAP would have meant a full
> `--reset` — rewriting every `pm_quote` row — just to pick up the history.

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

#### 15-minute preset

Fitted on the **latest six months** of BTCUSDT 15m (2026-03-13 → 2026-09-13),
the protocol of the [Reversal](#15-minute-preset), Oscillators and Gann 15m
presets: train 03-13 → 07-13, holdout 07-13 → 09-13 scored once after the pick
was frozen, and the 8.5 years before the window **never loaded** by any sweep
stage — scored once at the end (21,208 bets there against 1,303 in the
window). Rule fixed before tuning: train bets ≥ 300, both halves above 52%,
every parameter off its grid edge, then highest train hit, read against the
marginals. One stage of 720 configs raced Reversion/Continuation x five horizon triples (4/16/48 — the 5m defaults rescaled to 15m — 6/12/24, 8/24/72, 12/24/48, 12/48/144) x z_threshold {1.5..3.0} x min_agree {1, 2, 3} x opposing bar {off, on, on with 0.5 ATR} x trend filter {off, With EMA200}; a second pass (~10) tried the opposing-bar size, require_fast and the ATR band on the frozen pick.

Reversion is the family and Continuation its mirror (pooled train 56.18% vs 44.85%). Two of the 5m findings transfer and one does not. The OPPOSING-BAR entry gate transfers: at the pick's geometry it lifts the unloaded years from 56.34% (off) to 57.21% (0.5 ATR), and its marginal is monotone in both windows. The z threshold transfers: monotone on train (1.5: 55.51 -> 3.0: 58.26) at a steep cost in bets, with 2.0 the knee. The AGREEMENT premise does not: pooled train says three horizons beat two beat one (57.72 / 57.01 / 55.63) but on 8.5 unloaded years the order reverses (min_agree 1: 57.56%, 2: 57.21%, 3: 56.20%) — stretched on any one horizon is as good a fade as stretched on all three, which is why every 5m tier shipped with min_agree = 1. The preset keeps the train-selected 2 (the rule's protocol) and 12/24/48 bars — 3 h / 6 h / 12 h on 15m, the 5m Volume's own bar counts, which beat the wall-clock rescaling 4/16/48 on the unloaded years (57.21 vs 56.40%). The With-Trend filter loses 1.7pp on the holdout and 80% of the bets; require_fast and the ATR band are inert.

| preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017-08 → 2026-03 | worst yr |
|--------|--------:|-------:|------:|--------:|---------------------------:|---------:|
| PM 15m Balanced | 1,303 | 57.33% | 57.99% | 56.04% | **57.21%** (21,208 bets, z +21.0) | 55.11% (2022) |

Per year on the full record, none of it fitted except the last six months:

| year | hit (bets) | | year | hit (bets) | | year | hit (bets) |
|---|---:|---|---|---:|---|---|---:|
| 2017 | 51.66% (751) | | 2021 | 55.52% (2,428) | | 2025 | 56.95% (2,632) |
| 2018 | 59.03% (2,221) | | 2022 | 55.11% (2,522) | | 2026 | 57.44% (1,842) |
| 2019 | 58.62% (2,373) | | 2023 | 58.28% (2,574) | |  |  |
| 2020 | 58.78% (2,511) | | 2024 | 57.21% (2,657) | |  |  |

Train halves 58.64 / 57.31%, about 7.1 bets a day; the 18 months right
before the window score 56.98% on 3,961 bets; whole record 22,511
bets at 57.22% (z +21.7). After the pick was frozen: the prefix test
passes with 0 mismatches at three cuts; bets run 50% long and both
sides win (window 56.09% / 58.56%, unloaded 57.82% / 56.62%); the mirror scores 46.15% in the window and 50.81% unloaded.

**Where it fails.** Worst month 2026-04 at 54.2% on 225 bets; worst
full year 2022 at 55.11%. Train halves 58.6 / 57.3%; 2022 is the worst full year at 55.1%, and 2021-2022 run 55-56% against 58-59% either side.

**Not shipped.** min_agree 1 at the same settings: 2,273 window bets at 56.62% and 57.56% on 35,677 unloaded bets (z +28.5), worst year 56.22% — more bets, a better unloaded hit and a better worst year than the preset, kept out only by the rule's grid-edge test and the in-window ordering; it is the 5m presets' own setting and the better choice if volume matters. The rule's top row (z 2.5): 536 at 60.26%, 56.77% unloaded with 2022 at 53.2%. With Trend EMA200: 206 at 58.25%, 60.52% unloaded — thin. PM 5m Volume as-is: 475 at 58.32%, 56.49% unloaded.

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

#### 15-minute check

The same question was put to the six-month 15m window every other 15m preset
was fitted on (train 2026-03-13 → 07-13, holdout → 09-13, the 8.5 years before
scored once, unloaded). 132 configs — every single phase bucket, both halves
and all eight, × both directions × three trend modes × Every Bar / Once Per
Day. The best on train, First Quarter only / Waxing Short / Against Trend, is
55.29% on 709 bets; its holdout is 49.59% and its unloaded years are **49.99%
on 19,333 bets** — a z of exactly zero. Every 15m bar taken long is 49.85%,
short 50.06%. No 15m preset, on purpose.

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

#### 15-minute preset

Fitted on the **latest six months** of BTCUSDT 15m (2026-03-13 → 2026-09-13),
which is a different protocol from the two above and needs saying up front. The
six months were split 4 / 2 — train 2026-03-13 → 07-13, holdout 07-13 → 09-13
scored after the pick was frozen — and the nine years before the window were
**never loaded** by the sweep; they were scored once at the end. Six months of
15m is only 17,696 bars, so the fitted window cannot separate a fit from a
fluke on its own; the unloaded years carry the result (12,006 bets there against
793 in the window).

The sweep ran every detector family in both directions (2,802 configs), then
refined the survivor over the pivot geometry, ATR band, ATR length and trend
filter and tried every OR-combination with the pattern and divergence detectors
(3,216 configs). Selection was by train hit at ≥ 300 train bets with the grid
boundary excluded. Only one family had an edge:

| `pivot_left` (right = 1) | 2 | 3 | 4 | 6 | 8 | 12 | 16 | 20 | 30 |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| 6m bets | 845 | 874 | 856 | **816** | 746 | 634 | 546 | 526 | 432 |
| train hit | 55.7 | 56.5 | 55.7 | 56.7 | 58.4 | 58.2 | 58.8 | 60.5 | 59.1 |
| HOLDOUT hit | 47.8 | 54.8 | 58.0 | **61.2** | 60.8 | 60.6 | 59.9 | 60.9 | 55.6 |

Train rises with `pivot_left` to the edge of the grid — the overfitting
signature — while the holdout is flat at 60–61% from 6 to 20 and falls off at
30. `pivot_left = 6` is the **low end of that plateau**, i.e. the most bets it
offers, and that is why it was taken over the train maximum. Six 15m bars is 90
minutes, which is where the 5m (12–30 bars = 1–2.5 h) and 1m (90–150 bars =
1.5–2.5 h) presets landed independently: the effect lives at a wall-clock
scale, not a bar count.

| preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017 → 2024-09 | worst yr |
|--------|--------:|-------:|------:|--------:|------------------------:|---------:|
| PM 15m BOS | 793 | 58.64% | 56.83% | 62.55% | **58.81%** (9,405 bets, z +17.1) | 54.70% (2025) |

Per year on the full record, none of it fitted except the last six months:

| year | hit (bets) | | year | hit (bets) | | year | hit (bets) |
|---|---:|---|---|---:|---|---|---:|
| 2017 | 56.23% (393) | | 2021 | 58.72% (1,635) | | 2025 | 54.70% (1,757) |
| 2018 | 58.93% (1,142) | | 2022 | 56.83% (1,369) | | 2026 | 58.59% (1,106) |
| 2019 | 59.53% (1,107) | | 2023 | 57.58% (1,424) | | | |
| 2020 | 62.92% (1,281) | | 2024 | 57.10% (1,585) | | | |

**Every year clears 54%, including 2017 and 2018** — the years that sink the
5m and 1m presets. At 15m a structure break resolves inside the same one-way
move that runs the fast presets over, so the fade holds even in the parabolic
years. The cost is volume: ~4.4 bets a day against ~15 on 5m. Over all nine
years it is 58.06% on 12,799 bets (z +18.2), the never-loaded years score
*higher* than the fitted window, bets run 49% UP / 51% DOWN and hit 57.98% long
/ 58.13% short (not directional beta), and a truncation test reproduces 60/60
sampled signals with zero future bars.

**Where it fails.** The worst month in the fit window is 2026-04 at 52.5% on
118 bets; the other six range 55–64%. Expect stretches at the coin-flip line
lasting weeks and read the 58% as a multi-month average, not a monthly floor.

**Not shipped.** The brief was bets *and* hit rate, and OR-ing the candlestick
or divergence detectors into the preset does raise the bet count (to
1,100–9,000) — but every added bet lands at 52–54%, which is exactly the base
rate at which a 15m bar reverses the one before it (52.0% in the window, 51.8%
in the 18 months prior). Those detectors add volume at chance. `pivot_left =
20` hits 60.82% inside the fitted window but only 57.30% on the unloaded years
against this preset's 58.81%, with 35% fewer bets — the in-window edge was the
fit talking. The ATR band, `max_pivot_gap`, ATR length and the trend filter
were all swept and are inert or negative on 15m.

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

#### 15-minute preset

Fitted on the **latest six months** of BTCUSDT 15m (2026-03-13 → 2026-09-13),
the protocol of the [Reversal](#15-minute-preset), Oscillators and Gann 15m
presets: train 03-13 → 07-13, holdout 07-13 → 09-13 scored once after the pick
was frozen, and the 8.5 years before the window **never loaded** by any sweep
stage — scored once at the end (9,046 bets there against 539 in the
window). Rule fixed before tuning: train bets ≥ 300, both halves above 52%,
every parameter off its grid edge, then highest train hit, read against the
marginals. One stage of 2,304 configs raced Reversal/Continuation x AB=CD on/off x pivot_left {2..8} x pivot_right {1..3} x ratio_tolerance {0.05..0.15} x min_xa_atr {1.5..7} x PRZ entry {Wick Touch, Close Inside} x trend filter {off, Against SMA50}; a second pass (~20) extended the tolerance to 0.25 and tried the XA size, the PRZ width and overshoot, the CD-zone rule and the ATR band on the frozen pick.

Reversal is the family and Continuation its mirror (pooled train 56.03% vs 45.37%). The 5m findings hold: the six XABCD patterns without AB=CD beat the set with it (56.98% / 55.26% pooled against 55.56 / 54.10 — AB=CD is volume at a lower rate), Close Inside beats Wick Touch by 0.7pp, the Against-Trend SMA50 filter is worth about a point on train, and the geometry is mostly inert (pivot_left 2-8 within 1pp, pivot_right 1-3 within 0.7pp, XA 1.5-3 equal, the PRZ width and overshoot flat). The ratio tolerance is the one real dial and it is NOT a fit: 0.10 gives 381 window bets at 58.79%, 0.15 gives 539 at 57.51%, 0.20 gives 657 at 57.53%, 0.25 gives 732 at 56.97%, and all four score 57.6-58.5% on the unloaded years — looser Fibonacci ratios add bets at the same hit rate, which is the 5m file's finding about the ratios restated. The preset takes 0.15, the rule's row: six patterns, 3/2 pivots, XA >= 3 ATR, close inside the PRZ, faded against the 50-bar SMA.

| preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017-08 → 2026-03 | worst yr |
|--------|--------:|-------:|------:|--------:|---------------------------:|---------:|
| PM 15m Balanced | 539 | 57.51% | 58.57% | 55.56% | **58.47%** (9,046 bets, z +16.1) | 56.54% (2021) |

Per year on the full record, none of it fitted except the last six months:

| year | hit (bets) | | year | hit (bets) | | year | hit (bets) |
|---|---:|---|---|---:|---|---|---:|
| 2017 | 52.55% (333) | | 2021 | 56.54% (1,040) | | 2025 | 57.64% (1,053) |
| 2018 | 58.46% (1,023) | | 2022 | 61.83% (1,116) | | 2026 | 57.59% (738) |
| 2019 | 59.28% (1,051) | | 2023 | 58.28% (1,021) | |  |  |
| 2020 | 59.47% (1,098) | | 2024 | 58.00% (1,112) | |  |  |

Train halves 59.65 / 57.54%, about 2.9 bets a day; the 18 months right
before the window score 57.55% on 1,583 bets; whole record 9,585
bets at 58.41% (z +16.5). After the pick was frozen: the prefix test
passes with 0 mismatches at three cuts; bets run 47% long and both
sides win (window 55.08% / 59.72%, unloaded 58.19% / 58.70%); the mirror scores 55.00% in the window and 43.75% unloaded.

**Where it fails.** Worst month 2026-07 at 50.5% on 107 bets; worst
full year 2021 at 56.54%. Thin — ~3 bets a day — and 2026-07 ran at 50.5% on 107 bets. 2021 is the worst full year at 56.5%; the rest are 57.6-61.8%.

**Not shipped.** Tolerance 0.20 for volume: 657 window bets at 57.53%, 57.57% unloaded. min_xa_atr 2.0: 692 at 58.09% (holdout 55.33%). AB=CD on at XA 1.5: 1,144 at 56.03%, 57.32% on 19,332 unloaded bets (z +20.4) — the widest net that still holds. Tolerance 0.10: 381 at 58.79% with 60.00 / 60.00 train halves, 58.23% unloaded, but 2017 at 47%. PM 5m Balanced carried over as-is: 258 window bets with a 49.44% holdout; PM 5m Volume as-is: 837 at 53.88%, 55.45% unloaded — the 5m geometry needs the 15m sweep.

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

#### 15-minute preset

Fitted on the **latest six months** of BTCUSDT 15m (2026-03-13 → 2026-09-13),
the protocol of the [Reversal](#15-minute-preset), Oscillators and Gann 15m
presets: train 03-13 → 07-13, holdout 07-13 → 09-13 scored once after the pick
was frozen, and the 8.5 years before the window **never loaded** by any sweep
stage — scored once at the end (18,312 bets there against 1,242 in the
window). Rule fixed before tuning: train bets ≥ 300, both halves above 52%,
every parameter off its grid edge, then highest train hit, read against the
marginals. Stage 1 (864 configs) raced Fade/Follow x trigger {Extreme, Zero Cross, Momentum Turn} x osc_length {3..21} x score_threshold {0.3..0.85} x norm_atr_mult {1, 2, 4} x trend filter {off, With EMA200}; stage 2 (80) refined threshold {0.4..0.7} x normalisation {0.5..3.0} x length {5..14} inside Fade x Extreme; a third pass (~10) tried min_agree, the panel and the ATR band on the frozen pick.

The three 5m findings hold. Fade, not follow: pooled train 52.23% against 46.54%. Only the Extreme trigger earns — Zero Cross and Momentum Turn sit at 49-50% on both windows in both directions. And the score threshold is the dial: monotone on train (0.4: 54.36%, 0.5: 57.39, 0.6: 57.74, 0.7: 60.84) at a steep cost in bets (1,247 -> 93 a config). Length 7-14 is flat (55.8-56.7%), the normalisation is flat 0.5-2.0, min_agree is inert at these thresholds (as on 5m), and the With-Trend filter loses on both windows. The preset takes the interior of every marginal — a 10-bar panel, a 0.5 score, a 1.5-ATR normalisation — which is the rule-eligible row with the most even train halves (58.9 / 57.5): 1,242 window bets at 57.57% and 57.85% on 18,312 unloaded bets, every full year 57.4-59.8%. The whole neighbourhood scores 57.6-59.0% unloaded; nothing here is a fit.

| preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017-08 → 2026-03 | worst yr |
|--------|--------:|-------:|------:|--------:|---------------------------:|---------:|
| PM 15m Balanced | 1,242 | 57.57% | 58.16% | 56.39% | **57.85%** (18,312 bets, z +21.2) | 57.37% (2026) |

Per year on the full record, none of it fitted except the last six months:

| year | hit (bets) | | year | hit (bets) | | year | hit (bets) |
|---|---:|---|---|---:|---|---|---:|
| 2017 | 48.94% (799) | | 2021 | 57.40% (2,263) | | 2025 | 57.53% (2,682) |
| 2018 | 57.94% (1,814) | | 2022 | 57.49% (1,903) | | 2026 | 57.37% (1,729) |
| 2019 | 59.13% (1,786) | | 2023 | 59.37% (2,028) | |  |  |
| 2020 | 59.83% (2,054) | | 2024 | 58.05% (2,496) | |  |  |

Train halves 58.85 / 57.46%, about 6.7 bets a day; the 18 months right
before the window score 56.80% on 3,970 bets; whole record 19,554
bets at 57.83% (z +21.9). After the pick was frozen: the prefix test
passes with 0 mismatches at three cuts; bets run 49% long and both
sides win (window 56.22% / 58.84%, unloaded 58.51% / 57.29%); the mirror scores 42.43% in the window and 42.08% unloaded.

**Where it fails.** Worst month 2026-05 at 53.1% on 209 bets; worst
full year 2026 at 57.37%. 2017 is the losing year (48.9% — fading a parabolic run), and 2024-2026 run 57.4-58.1% against 59-60% in 2019-2020 and 2023. 2026-05 ran at 53.1% on 209 bets.

**Not shipped.** Threshold 0.6 at length 7 with a 1.0 normalisation: 808 window bets at 59.41% (holdout 60.55%), 58.95% unloaded (z +19.3) — the Selective tier. Length 7, 0.5, norm 2.0: 1,195 at 57.07%, 58.72% unloaded with the best worst year (57.10%). Length 7, 0.5, norm 1.0: 1,531 at 55.72%, 57.98% unloaded — the Volume tier. PM 5m Balanced carried over as-is: 229 window bets at 62.45% but 2022 at 48.5% unloaded; PM 5m Volume as-is: 646 at 57.89%, 57.84% unloaded.

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

#### 15-minute preset

Fitted on the **latest six months** of BTCUSDT 15m (2026-03-13 → 2026-09-13),
the protocol of the [Reversal](#15-minute-preset), Oscillators and Gann 15m
presets: train 03-13 → 07-13, holdout 07-13 → 09-13 scored once after the pick
was frozen, and the 8.5 years before the window **never loaded** by any sweep
stage — scored once at the end (15,310 bets there against 966 in the
window). Rule fixed before tuning: train bets ≥ 300, both halves above 52%,
every parameter off its grid edge, then highest train hit, read against the
marginals. One stage of 1,800 configs raced Against/With Structure x event {CHoCH only, BOS only, Both} x pivot_left {3..30} x pivot_right {1, 2} x break_buffer_atr {0..1.0} x min_displacement_atr {0, 0.5} x higher-scale filter {off, Agree, Oppose}; a second pass (~10) tried Wick Beyond, On Retest, max_level_age and the ATR band on the frozen pick.

Fading the break is the family and following it the mirror (pooled train 56.79% vs 43.18%) — the fifth strategy in this repo to land there. The surface is FLAT: every marginal inside Against Structure sits between 56.3% and 57.3% on train and 55.3-57.6% on the holdout, so the pick was read off the frontier and the unloaded years rather than the argmax. The rule's own top row (pivot 6/1, buffer 1.0, displacement 0.5) is the one config that fails out of sample (57.27% unloaded but 2021 at 52.7%); the preset is two rows down at the same pivot with a 0.25-ATR buffer and no displacement floor, 966 window bets at 58.39% and 57.41% on 15,310 unloaded bets, every full year 56.5-58.9%. Both event types earn inside it — BOS 58.40%, CHoCH 58.37% on 512 / 454 bets — so the event switch is left on Both. As on 5m: Close Beyond beats Wick Beyond (which adds 450 bets at 1.7pp less), On Retest is a coin flip (50.36%), the higher-scale filter only removes bets, max_level_age is byte-identical from 100 up and the ATR band is inert. The 6-bar pivot is Reversal's 15m BOS pivot; 37% of these bars are shared with that preset and the exclusive 613 score 57.10%.

| preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017-08 → 2026-03 | worst yr |
|--------|--------:|-------:|------:|--------:|---------------------------:|---------:|
| PM 15m Balanced | 966 | 58.39% | 58.19% | 58.77% | **57.41%** (15,310 bets, z +18.3) | 56.54% (2024) |

Per year on the full record, none of it fitted except the last six months:

| year | hit (bets) | | year | hit (bets) | | year | hit (bets) |
|---|---:|---|---|---:|---|---|---:|
| 2017 | 51.51% (598) | | 2021 | 56.71% (1,869) | | 2025 | 58.14% (2,026) |
| 2018 | 57.88% (1,643) | | 2022 | 56.63% (1,764) | | 2026 | 58.11% (1,351) |
| 2019 | 57.88% (1,579) | | 2023 | 58.85% (1,774) | |  |  |
| 2020 | 58.68% (1,762) | | 2024 | 56.54% (1,910) | |  |  |

Train halves 60.57 / 55.86%, about 5.2 bets a day; the 18 months right
before the window score 57.27% on 3,012 bets; whole record 16,276
bets at 57.46% (z +19.0). After the pick was frozen: the prefix test
passes with 0 mismatches at three cuts; bets run 49% long and both
sides win (window 57.95% / 58.81%, unloaded 57.76% / 57.09%); the mirror scores 41.61% in the window and 42.55% unloaded.

**Where it fails.** Worst month 2026-07 at 52.1% on 163 bets; worst
full year 2024 at 56.54%. Train halves 60.6 / 55.9%, and 2026-07 ran at 52.1% on 163 bets. 2024 is the worst full year at 56.5%.

**Not shipped.** The rule's top row (buffer 1.0, displacement 0.5): 514 window bets at 58.56%, 57.27% unloaded, 2021 at 52.7%. pivot 6/1 with no buffer (the widest net): 1,494 at 56.29%, 56.97% on 22,943 unloaded bets (z +21.1). Higher-scale Agree at the pick: 634 at 59.15% in the window, 56.10% unloaded. PM 5m Volume carried over as-is: 531 at 55.18%; PM 5m Balanced as-is: 173 at 60.69% with 2021 at 46.1% unloaded — the 5m buffers are too wide for 15m bars.

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

#### 15-minute preset

Fitted on the **latest six months** of BTCUSDT 15m (2026-03-13 → 2026-09-13),
the protocol of the [Reversal](#15-minute-preset), Oscillators and Gann 15m
presets: train 03-13 → 07-13, holdout 07-13 → 09-13 scored once after the pick
was frozen, and the 8.5 years before the window **never loaded** by any sweep
stage — scored once at the end (7,824 bets there against 479 in the
window). Rule fixed before tuning: train bets ≥ 300, both halves above 52%,
every parameter off its grid edge, then highest train hit, read against the
marginals. One stage of 1,200 configs raced Trend Resume/Retrace Deeper x swing_lookback {8..72} x min_leg_atr {2, 4, 6} x fib_level {0.382, 0.5, 0.618, 0.786, 0.85} x tolerance {0.05, 0.1} x opposing bar on/off x trend filter {off, Against SMA50}; a second pass (~10) tried the opposing-bar size, the fresh-touch rule, min_leg_bars and the ATR band on the frozen pick.

Trend Resume is the family (pooled train 52.91% vs 48.66% for Retrace Deeper) and it is a thin one on 15m: the pooled numbers barely clear 52%, and the edge lives at the deep levels — the fib_level marginal is monotone (0.382: 50.88% train / 50.62% holdout; 0.618: 53.09 / 52.14; 0.786: 56.19 / 54.66) as it was on 5m. The swing needs to be long (lookback 48 = 12 h beats 8-24 by 1-4pp on both windows) and the opposing-bar entry gate adds 2.3pp on train. The rule's row sits at 0.618 — the golden ratio itself, the one level with a story behind it — on a 48-bar swing of at least 4 ATR, entered on a bar still pushing against the bet: 479 bets at 56.78% in the window and 56.57% on 7,824 unloaded bets, the most bets of anything in the family that holds out of sample. The 0.786 rows score higher in the window (59.07% on 430 bets at lookback 24) but not out of it (56.69%); the Against-Trend SMA50 filter the 5m Balanced carries is worth +0.5pp unloaded on this row and costs a quarter of the bets, so it is off; the ATR band and fresh-touch rule are inert.

| preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017-08 → 2026-03 | worst yr |
|--------|--------:|-------:|------:|--------:|---------------------------:|---------:|
| PM 15m Balanced | 479 | 56.78% | 57.37% | 55.62% | **56.57%** (7,824 bets, z +11.6) | 54.14% (2018) |

Per year on the full record, none of it fitted except the last six months:

| year | hit (bets) | | year | hit (bets) | | year | hit (bets) |
|---|---:|---|---|---:|---|---|---:|
| 2017 | 54.22% (249) | | 2021 | 55.31% (875) | | 2025 | 56.61% (931) |
| 2018 | 54.14% (809) | | 2022 | 55.31% (960) | | 2026 | 56.13% (652) |
| 2019 | 57.70% (941) | | 2023 | 59.51% (1,072) | |  |  |
| 2020 | 57.60% (908) | | 2024 | 56.62% (906) | |  |  |

Train halves 58.54 / 56.13%, about 2.6 bets a day; the 18 months right
before the window score 56.27% on 1,395 bets; whole record 8,303
bets at 56.58% (z +12.0). After the pick was frozen: the prefix test
passes with 0 mismatches at three cuts; bets run 52% long and both
sides win (window 59.68% / 53.68%, unloaded 56.67% / 56.47%); the mirror scores 57.05% in the window and 54.47% unloaded.

**Where it fails.** Worst month 2026-09 at 46.2% on 39 bets; worst
full year 2018 at 54.14%. Thin: ~2.7 bets a day, and the window months swing widely. 2018 is the worst full year at 54.1%, 2021 and 2022 at 55.3%; 2026-09 opened at 46% on 39 bets.

**Not shipped.** 0.786 at lookback 24 without the opposing bar: 430 window bets at 59.07% (train 61.69, holdout 55.03), 56.69% unloaded. 0.618 with Against Trend SMA50: 380 at 55.79%, 57.14% unloaded. PM 5m Balanced carried over as-is (lookback 72, 0.85, Against SMA50): 148 window bets, 46.15% holdout — the 5m geometry does not transfer. PM 5m Volume as-is: 369 at 58.54%, 55.96% unloaded.

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

#### 15-minute preset

Fitted on the **latest six months** of BTCUSDT 15m (2026-03-13 → 2026-09-13),
the protocol of the [Reversal](#15-minute-preset), Oscillators and Gann 15m
presets: train 03-13 → 07-13, holdout 07-13 → 09-13 scored once after the pick
was frozen, and the 8.5 years before the window **never loaded** by any sweep
stage — scored once at the end (14,226 bets there against 1,119 in the
window). Rule fixed before tuning: train bets ≥ 300, both halves above 52%,
every parameter off its grid edge, then highest train hit, read against the
marginals. One stage of 1,440 configs raced five pattern sets (marubozu; engulfing; hammer; the seven reversal patterns together; marubozu + three soldiers) x Fade/Pattern x prior-move gate {off, Textbook, Extension, Reversal} x prior_move_bars {3, 6, 12} x prior_move_atr {0.5..2.0} x min_range_atr {0.5..1.5}; extension passes (~20) pushed the range and prior-move floors down to 0.25 because both marginals ran to a grid edge, and tried marubozu_body_min, the ATR band and the trend filter on the frozen family.

Every 5m finding transfers. FADE, always: the pattern's own direction pools 47.41% train / 48.16% holdout against 52.52 / 51.78 faded. Only the decisive bars earn: marubozu pools 55.56% / 54.29%, marubozu + soldiers 56.03 / 54.27, engulfing 53.31 / 51.06, the hammer 47.64 / 50.17 and the seven reversal patterns together 50.29 / 49.91 — the textbook reversal vocabulary is noise on 15m as it was on 5m. The EXTENSION gate is the context that matters: a marubozu that extends a move already under way fades at 57.5% / 58.7% pooled against 55.2 / 53.2 with no gate and 53.6 / 51.5 under the 'Reversal' reading (for a continuation bar, Textbook and Extension are the same reading and their rows are identical). Inside that family the surface is flat — every cell of the extended range x prior-move grid scores 58.2-59.7% on the unloaded years — so the preset takes the interior point with the most even train halves: marubozu or three soldiers of at least 0.5 ATR range, extending a 6-bar move of at least 0.5 ATR, faded. The trend filter and the ATR band are inert.

| preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017-08 → 2026-03 | worst yr |
|--------|--------:|-------:|------:|--------:|---------------------------:|---------:|
| PM 15m Balanced | 1,119 | 57.73% | 57.67% | 57.84% | **58.65%** (14,226 bets, z +20.6) | 56.79% (2022) |

Per year on the full record, none of it fitted except the last six months:

| year | hit (bets) | | year | hit (bets) | | year | hit (bets) |
|---|---:|---|---|---:|---|---|---:|
| 2017 | 55.56% (720) | | 2021 | 57.10% (1,499) | | 2025 | 56.94% (2,601) |
| 2018 | 60.92% (1,277) | | 2022 | 56.79% (1,208) | | 2026 | 58.19% (1,533) |
| 2019 | 62.00% (1,213) | | 2023 | 59.12% (1,695) | |  |  |
| 2020 | 63.22% (1,430) | | 2024 | 57.08% (2,169) | |  |  |

Train halves 57.93 / 57.44%, about 6.1 bets a day; the 18 months right
before the window score 57.36% on 3,745 bets; whole record 15,345
bets at 58.58% (z +21.3). After the pick was frozen: the prefix test
passes with 0 mismatches at three cuts; bets run 49% long and both
sides win (window 56.42% / 59.01%, unloaded 59.40% / 58.02%); the mirror scores 42.27% in the window and 41.22% unloaded.

**Where it fails.** Worst month 2026-09 at 55.2% on 87 bets; worst
full year 2022 at 56.79%. Train halves 57.9 / 57.4%; 2022 is the worst full year at 56.8% and 2021, 2024 and 2025 run 57%, against 61-63% in 2018-2020: the edge is real in every year and decaying.

**Not shipped.** prior_move_atr 1.0 at the same range: 745 window bets at 59.60% (holdout 61.42%), 59.71% on 8,690 unloaded bets — the Selective tier. min_range 0.25 with prior 0.25: 1,415 at 57.24%, 58.18% unloaded — the Volume tier. The rule's own top row (range 1.0, prior 1.0): 476 at 57.35%, 59.50% unloaded. PM 5m Volume and Balanced carried over as-is score 59.14% / 59.33% in the window and 59.60% / 59.62% unloaded — both 5m presets transfer to 15m unchanged.

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

#### 15-minute preset

Fitted on the **latest six months** of BTCUSDT 15m (2026-03-13 → 2026-09-13),
the protocol of the [Reversal](#15-minute-preset), Oscillators and Gann 15m
presets: train 03-13 → 07-13, holdout 07-13 → 09-13 scored once after the pick
was frozen, and the 8.5 years before the window **never loaded** by any sweep
stage — scored once at the end (6,872 bets there against 478 in the
window). Rule fixed before tuning: train bets ≥ 300, both halves above 52%,
every parameter off its grid edge, then highest train hit, read against the
marginals. One stage of 1,152 configs raced Follow/Fade Count x setup {Wave 3, Wave 5, Wave 3 + 5, Post-Impulse Reversal} x entry {Pivot Confirm, Retrace Zone} x pivot_atr_mult {1.5..6} x wave-2 band {shallow, deep, any} x opposing bar {off, on, on 0.5 ATR} x trend filter {off, Against SMA50}; a second pass (~12) tried the impulse rules, the wave-1 floor, setup age, min_pivot_bars and the ATR band on the frozen pick.

The 5m structure repeats exactly: the count earns in two equivalent lanes and loses in the other two. Following the count works only from inside the retrace zone (Wave 3 pooled 54.57% train / 53.90% holdout) and fading it works only on the pivot confirmation (54.86 / 53.84); the two crossed lanes are 48-49% in both windows. The lanes are the same trade — buy wave 2's pullback before or after its low is confirmed — and neither is worth more than 55% pooled, which is what the 5m file found. The rule's row is the Wave-3 zone entry on a 2.5-ATR pivot with a deep wave 2 (0.618-1.0 of wave 1) and a 0.5-ATR opposing bar: 478 window bets at 59.62% and 57.32% on 6,872 unloaded bets. The opposing bar is the one gate that earns (off: 56.91% window, 52.17% holdout); the impulse rules, the wave-1 floor and the setup age are inert; the Against-Trend filter costs bets for nothing; larger pivots (4-6 ATR) are the 5m geometry and do not transfer (pivot 4.0: 216 bets, 54.68% unloaded with 2021 at 50.7%).

| preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017-08 → 2026-03 | worst yr |
|--------|--------:|-------:|------:|--------:|---------------------------:|---------:|
| PM 15m Balanced | 478 | 59.62% | 62.31% | 54.14% | **57.32%** (6,872 bets, z +12.1) | 54.57% (2025) |

Per year on the full record, none of it fitted except the last six months:

| year | hit (bets) | | year | hit (bets) | | year | hit (bets) |
|---|---:|---|---|---:|---|---|---:|
| 2017 | 55.88% (204) | | 2021 | 55.66% (821) | | 2025 | 54.57% (865) |
| 2018 | 57.58% (712) | | 2022 | 57.97% (847) | | 2026 | 59.91% (641) |
| 2019 | 59.53% (719) | | 2023 | 60.09% (862) | |  |  |
| 2020 | 57.05% (780) | | 2024 | 56.17% (899) | |  |  |

Train halves 65.24 / 59.24%, about 2.6 bets a day; the 18 months right
before the window score 55.67% on 1,306 bets; whole record 7,350
bets at 57.47% (z +12.8). After the pick was frozen: the prefix test
passes with 0 mismatches at three cuts; bets run 52% long and both
sides win (window 59.51% / 59.74%, unloaded 57.78% / 56.87%); the mirror scores 80.00% in the window and 55.72% unloaded.

**Where it fails.** Worst month 2026-09 at 46.7% on 30 bets; worst
full year 2025 at 54.57%. Train halves 65.2 / 59.2% and a 54.14% holdout on 157 bets — the widest train-to-holdout gap of the 15m presets — against a steadier 57.3% unloaded; ~2.7 bets a day. 2025 is the worst full year at 54.6%.

**Not shipped.** The opposing bar at 0 ATR (any opposing close): 585 window bets at 58.46%, 57.29% unloaded. Wave 3 + 5 with any wave-2 depth: 687 at 56.62%, 56.62% unloaded — the Volume tier. Pivot 1.5 ATR: 378 at 60.05%, 58.24% unloaded on fewer bets. The Fade-Count lane (Wave 3, Pivot Confirm): 445 at 57.75%, 56.92% unloaded. PM 5m Balanced carried over unchanged prints 67.35% on 98 window bets and 53.87% on 1,474 unloaded ones with 2019 at 49.2% — a small-sample fluke.

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

#### 15-minute preset

Fitted on the **latest six months** of BTCUSDT 15m (2026-03-13 → 2026-09-13),
the protocol of the [Reversal](#15-minute-preset), Oscillators and Gann 15m
presets: train 03-13 → 07-13, holdout 07-13 → 09-13 scored once after the pick
was frozen, and the 8.5 years before the window **never loaded** by any sweep
stage — scored once at the end (32,046 bets there against 2,238 in the
window). Rule fixed before tuning: train bets ≥ 300, both halves above 52%,
every parameter off its grid edge, then highest train hit, read against the
marginals. One stage of 3,072 configs raced Fade/Follow x trigger {Brick Reversal, Brick Run, Any New Brick} x brick {0.25..2.0 ATR, 0.15..1.0 %} x reversal_bricks {1..4} x min_run_bricks {2..8} x max_new_bricks {0, 2} x trend filter {off, With EMA200}; a second pass (~10) tried the brick multiple, the ATR length and the ATR band on the frozen pick.

Fade the brick, as on 5m: pooled train 54.33% against 44.98% for following it. Then a change of trigger. On 5m the pick fired on the brick REVERSAL; on 15m the volume and the out-of-sample edge are in ANY NEW BRICK faded — every fresh brick is a bet against the brick's direction — and the brick has to be large: at 1.0 ATR the fade scores 57.42% on 2,238 window bets and 57.21% on 32,046 unloaded bets, at 0.25 ATR it is 53.4-53.9% on four times the volume, and at 2.0 ATR 60.7% on a third of the bets. ATR-sized bricks are preferred to percent bricks because the bet rate stays constant across price regimes (a 0.3% brick fires 3,015 times in the unloaded 2017 against 1,189 for a 1-ATR brick, and its worst year is 0.6pp lower). reversal_bricks 2 beats 3 on both windows at this trigger; min_run_bricks is unused; max_new_bricks and the ATR band are inert; With Trend EMA200 lifts the hit to 58.6% unloaded at a third of the bets. The rule's own top row (Brick Run on 0.15% bricks) prints 63.08% in the window and 56.35% unloaded with 2025 at 52.9% — a fit.

| preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017-08 → 2026-03 | worst yr |
|--------|--------:|-------:|------:|--------:|---------------------------:|---------:|
| PM 15m Balanced | 2,238 | 57.42% | 57.62% | 57.05% | **57.21%** (32,046 bets, z +25.8) | 55.88% (2022) |

Per year on the full record, none of it fitted except the last six months:

| year | hit (bets) | | year | hit (bets) | | year | hit (bets) |
|---|---:|---|---|---:|---|---|---:|
| 2017 | 53.07% (1,189) | | 2021 | 57.06% (3,756) | | 2025 | 55.92% (4,666) |
| 2018 | 57.58% (3,133) | | 2022 | 55.88% (3,638) | | 2026 | 57.75% (3,077) |
| 2019 | 58.43% (3,156) | | 2023 | 57.97% (3,873) | |  |  |
| 2020 | 59.94% (3,475) | | 2024 | 56.72% (4,321) | |  |  |

Train halves 58.53 / 56.79%, about 12.1 bets a day; the 18 months right
before the window score 56.18% on 6,881 bets; whole record 34,284
bets at 57.23% (z +26.8). After the pick was frozen: the prefix test
passes with 0 mismatches at three cuts; bets run 50% long and both
sides win (window 56.33% / 58.51%, unloaded 57.57% / 56.88%); the mirror scores 42.54% in the window and 42.74% unloaded.

**Where it fails.** Worst month 2026-08 at 55.2% on 382 bets; worst
full year 2022 at 55.88%. Train halves 58.5 / 56.8%. 2022 is the worst full year at 55.9% and 2025 at 55.9%, against 58-60% in 2019-2020 and 2023.

**Not shipped.** 2.0-ATR bricks: 771 window bets at 60.70% (holdout 59.15%), 57.22% unloaded — the Selective tier. With Trend EMA200 at 1.0 ATR: 667 at 59.37%, 58.61% unloaded. 0.5-ATR bricks with a 3-brick reversal: 4,091 at 55.90%, 57.37% on 58,700 unloaded bets (z +35.7) — the Volume tier. The rule's row (Brick Run, 0.15%, 3 / 5): 455 at 63.08%, 56.35% unloaded. PM 5m Volume carried over as-is: 155 window bets, 56.54% unloaded with 2025 at 52.6%.

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

#### 15-minute preset

Fitted on the **latest six months** of BTCUSDT 15m (2026-03-13 → 2026-09-13),
the protocol of the [Reversal](#15-minute-preset), Oscillators and Gann 15m
presets: train 03-13 → 07-13, holdout 07-13 → 09-13 scored once after the pick
was frozen, and the 8.5 years before the window **never loaded** by any sweep
stage — scored once at the end (22,423 bets there against 1,394 in the
window). Rule fixed before tuning: train bets ≥ 300, both halves above 52%,
every parameter off its grid edge, then highest train hit, read against the
marginals. One stage of 1,620 configs raced Against/With Signal x trigger {break, bounce} x pivot_left {3..30} x pivot_right {1..3} x cluster_tol_atr {0.5..1.5} x min_touches {1..3} x break_buffer_atr {0, 0.3, 0.8}; a second pass (~12) tried max_levels, level age, retire-on-break and the ATR band on the frozen pick.

Fade the break, as on 5m: pooled train 51.17% against 48.81% for taking it, and bounces are a coin flip either way. Inside the fade every marginal is flat to within a point (pivot_left 3-30: 54.8-55.9% train; touches 1-3: 55.2-56.0; cluster width 0.5-1.5: 55.0-55.9; buffer 0: 55.5, 0.3: 54.9, 0.8: 56.9) — the level is the edge and its exact construction is not. So the pick is the frontier point with the most bets that still holds out of sample: a 6-bar (90 min) pivot confirmed by 2 bars, a 1-ATR cluster, one touch, no buffer — PM 5m Level Break Volume's settings with the pivot rescaled to 15m's wall-clock, 1,394 window bets at 56.10% and 56.42% on 22,423 unloaded bets. The rule's own top row (12/2, 0.5-ATR cluster, two touches) prints 58.58% in the window and 55.58% unloaded with 2021 at 53.8%; the textbook three-touch levels lose on the holdout (min_touches 3 at 6/2: 50-51.5%). max_levels, level age and retire-on-break are inert above sane values; the ATR band is inert.

| preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017-08 → 2026-03 | worst yr |
|--------|--------:|-------:|------:|--------:|---------------------------:|---------:|
| PM 15m Level Break | 1,394 | 56.10% | 56.92% | 54.51% | **56.42%** (22,423 bets, z +19.2) | 55.26% (2022) |

Per year on the full record, none of it fitted except the last six months:

| year | hit (bets) | | year | hit (bets) | | year | hit (bets) |
|---|---:|---|---|---:|---|---|---:|
| 2017 | 53.13% (830) | | 2021 | 55.55% (2,650) | | 2025 | 56.57% (2,892) |
| 2018 | 55.81% (2,385) | | 2022 | 55.26% (2,606) | | 2026 | 56.54% (1,949) |
| 2019 | 56.51% (2,419) | | 2023 | 56.45% (2,682) | |  |  |
| 2020 | 59.75% (2,589) | | 2024 | 56.23% (2,815) | |  |  |

Train halves 57.42 / 56.43%, about 7.6 bets a day; the 18 months right
before the window score 55.82% on 4,319 bets; whole record 23,817
bets at 56.40% (z +19.8). After the pick was frozen: the prefix test
passes with 0 mismatches at three cuts; bets run 50% long and both
sides win (window 54.27% / 57.95%, unloaded 57.00% / 55.88%); the mirror scores 43.83% in the window and 43.52% unloaded.

**Where it fails.** Worst month 2026-06 at 53.0% on 217 bets; worst
full year 2022 at 55.26%. The thinnest margin of the level-break family on 15m: 56.4% unloaded, 2022 at 55.3%, and a 54.51% holdout on 477 bets. CHoCH and Gann's fan preset fade the same breaks with more edge.

**Not shipped.** The rule's row (12/2, cluster 0.5, two touches): 676 window bets at 58.58%, 55.58% unloaded. 12/2 cluster 1.0 one touch: 1,077 at 56.82%, 55.88% unloaded. 6/2 cluster 0.5 one touch: 1,697 at 55.69%, 56.10% on 27,679 unloaded bets — the Volume tier. PM 5m Level Break Volume carried over as-is: 761 at 56.11%, 55.71% unloaded; Confirmed as-is: 318 with a 45.4% first train half.

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

#### 15-minute preset

Fitted on the **latest six months** of BTCUSDT 15m (2026-03-13 → 2026-09-13),
the same protocol as the [Reversal](#15-minute-preset) and Oscillators 15m
presets: train 2026-03-13 → 07-13, holdout 07-13 → 09-13 scored after the pick
was frozen, and the 8.5 years before the window **never loaded** by any sweep
stage — scored once at the end. The unloaded years carry the result (28,974
bets there against 1,844 in the window).

Stage 1 (288 configs) raced the structure — With/Against × break/bounce/both ×
`unit` × ray set × `pivot_left`; stage 2 (1,890 + 162) tuned `pivot_left`
3–180, `pivot_right`, `unit` 0.0005–0.1, the ray set and the buffer inside the
survivor; stage 3 (~60) tried `max_anchor_age`, the ATR band and length, the
trend filter and every ray subset on the frozen finalist. The rule was fixed
before stage 2 (train bets ≥ 300, both halves above 52%, every parameter off
its grid edge, then highest train hit), with the 5m caveat applied: at ~800
bets a config the SE is 1.7pp, so the shape was read off marginals.

**What replicates.** Fading the break is again the family (pooled stage 1,
train / holdout: Against+break 53.98 / 53.46; With+break 45.98 / 46.48 — the
mirror). Bounces lose either way. `pivot_left` is a plateau from 20 to 60 on
train that falls back beyond 90 (the holdout's climb to 61% at `pivot_left =
180` is ~180 bets a config and did not survive the unloaded years). The buffer
is flat from 0.0 to 0.3, `max_anchor_age` is byte-identical from 150 up, and
the ATR band, ATR length and trend filter buy nothing.

### What does not replicate: on 15m the angles earn

The 5m headline is that the fan collapses to a flat level and the rays add
nothing. On 15m the `unit` marginal still points down on train (0.0005: 57.03%
… 0.1: 54.02%), but the holdout is flat at 56.6–57.1% for every `unit ≤ 0.01`,
and the direct test is unambiguous. Take *PM 5m Volume*'s geometry (pivot 20/1,
buffer 0.3), switch the fan on at `unit = 0.01` with all nine rays, and ask what
the eight angled rays add on top of the flat 1x1 — the level's bets are a strict
subset of the fan's, so the split is clean:

| | level (1x1 only) | angled rays add | fan total |
|---|---:|---:|---:|
| 15m, fitted window | 634 · 58.20% | **+1,210 · 57.19%** | 1,844 · 57.54% |
| 15m, unloaded 8.5 years | 9,537 · 56.58% | **+19,437 · 57.08%** | 28,974 · 56.91% |
| 5m, the same six months | 1,997 · 55.48% | +3,189 · **52.24%** | 5,186 · 53.49% |

On 5m the angles dilute the level by 3pp, exactly as the 5m sweep found. On 15m
they match it — in the window and across 8.5 years the sweep never read. Every
ray earns alone (1x8 58.0%, 1x4 58.0, 1x3 57.8, 1x2 57.6, 1x1 56.9, 2x1 57.4,
3x1 57.9, 4x1 58.3, 8x1 57.4, on 624–1,238 bets each), and the volume comes
from the steep side: 1x1…8x1 give 1,809 bets against 706 for 1x8…1x1, because
at `unit = 0.01` the shallow rays drift under 0.4 ATR over a fan's life and sit
on the level, while the 8x1 climbs 1.6 ATR in 20 bars. A steep ray is a
trailing line that price crosses when its move stalls, and fading that cross is
worth the same ~57% as fading the level. `unit = 0.01` is the knee of the
all-nine marginal (0.005: 56.17% on 708 bets a config, 0.01: 56.15% on 1,108,
0.02: 55.05% on 1,885), not an argmax.

So the 15m preset is **PM 5m Volume with the fan switched back on**: the same
pivot, buffer and direction, `unit` 0.002 → 0.01, one ray → nine.

| preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017-08 → 2026-03 | worst yr |
|--------|--------:|-------:|------:|--------:|---------------------------:|---------:|
| PM 15m Fan | 1,844 | 57.54% | 57.61% | 57.40% | **56.91%** (28,974 bets, z +23.5) | 55.68% (2021) |

Per year on the full record, none of it fitted except the last six months:

| year | hit (bets) | | year | hit (bets) | | year | hit (bets) |
|---|---:|---|---|---:|---|---|---:|
| 2017 | 51.36% (1,252) | | 2021 | 55.68% (3,581) | | 2025 | 56.25% (3,911) |
| 2018 | 57.20% (3,154) | | 2022 | 57.27% (3,220) | | 2026 | 57.73% (2,567) |
| 2019 | 56.72% (3,149) | | 2023 | 58.30% (3,048) | | | |
| 2020 | 58.67% (3,332) | | 2024 | 57.33% (3,604) | | | |

**Every full year sits in a 55.7–58.7% band**, the 18 months right before the
window score 56.45% on 5,793 bets, and 2017 — the year that breaks *PM 5m
Volume* — is at chance rather than under it. Whole record 30,818 bets at
56.95% (z +24.4), about 10 bets a day. Read the hit rates against 49.9%: 0.13%
of 15m candles close exactly at their open (0.24% on 5m).

After the pick was frozen: the truncation test passes with 0 mismatches at
three cuts; bets run 50% long in the window and both sides win (56.88% /
58.19%; unloaded 57.29% / 56.60%), as do both fans alone (up 56.88%, down
58.19%); the mirror scores 42.41%; and it is not Reversal's *PM 15m BOS*
relabelled — 23% of bars are shared (Jaccard 19.5%) and the exclusive 1,415
window bets score 56.61% (22,278 unloaded at 56.74%) against a 51.95% base rate
for a 15m bar reversing the one before it. The 5m caveat about Trend Lines
applies to the flat level, not to the steep rays, which Trend Lines does not
draw.

**Where it fails.** The train halves are 60.03% / 55.31% — a wider spread than
the other 15m presets; the holdout (57.40%) and the unloaded years say the
second half is the honest number. The worst month in the window is 2026-06 at
55.9% on 313 bets, the worst full year 2021 at 55.68%. Expect weeks at 54–55%.

**Not shipped.** The flat level at a 5-bar pivot (pivot 5/2, `unit` 0.005,
1x1, buffer 0.3): 1,027 window bets at 57.64% and 16,184 unloaded at **57.41%**
with every full year 56.5–58.8% — the highest out-of-sample hit rate in the
sweep, on 56% of the bets, and a 75-minute pivot that sits where Reversal (6
bars) and Oscillators (RSI 7) landed independently; it shares 34% of its bars
with Reversal's BOS. An ATR floor of 0.20% lifts the fan to 58.54% and removes
a third of its bets. And *PM 5m Selective* carried over unchanged prints
**68.04%** on 219 window bets — and 54.61% on 2,882 unloaded ones with 2021 and
2022 under 50%. A fluke, and the reason a six-month number needs the other nine
years behind it.

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

#### 15-minute preset

Fitted on the **latest six months** of BTCUSDT 15m (2026-03-13 → 2026-09-13),
the same protocol as [Reversal's 15m preset](#15-minute-preset): train
2026-03-13 → 07-13, holdout 07-13 → 09-13 scored after the pick was frozen, and
the 8.5 years before the window **never loaded** by any sweep stage — scored
once at the end. Six months of 15m is 17,700 bars, so the fitted window cannot
separate a fit from a fluke on its own; the unloaded years carry the result
(21,602 bets there against 1,383 in the window).

Stage 1 (1,960 configs) raced every family — 7 oscillators × 5 triggers ×
Fade/Follow × 7 lengths × 2 bands × 2 smoothings; stage 2 (2,744) tuned length,
smoothing and band inside the survivor; stage 3 (66) tried the ATR band, ATR
length, trend filter and smoothing on the frozen finalists. The selection rule
was fixed before stage 2 ran: train bets ≥ 600, both train halves above 52%,
length and band off the grid boundary, then highest train hit.

**The 5m findings replicate one for one.** Zone Entry + Fade is again the only
family that earns (pooled stage 1, train / holdout: 53.31 / 53.22; Centerline
Cross 51.46 / 51.52; everything else at or under 50 on the holdout). Follow is
the exact mirror (42.81% in the window, 42.44% unloaded). The textbook band
*exit* on the shipped settings scores 52.02% in the window and 50.41% on the
unloaded years. The band and length marginals are monotone again (band 60 →
90: 52.42 → 54.38%; length 3 → 28: 52.51 → 54.39%), the oscillator ranking is
the same (RSI 54.93 / 55.45, TSI 54.38 / 54.56, Ultimate 54.08 / 54.32,
Stochastic 53.47 / 53.07, CCI 53.01 / 53.13, Stoch RSI 51.62 / 51.83), and the
ATR band, ATR length and trend filter are inert. One difference: smoothing does
*not* help on 15m — on the finalists it only removes bets.

**The pick is not the rule's pick, and here is why.** The rule selected TSI 9
30/70. The train frontier from 600 to 1,200 train bets is a plateau, not a
peak:

| config | 6m bets | 6m hit | train (halves) | HOLDOUT | unloaded 8.5y |
|--------|--------:|-------:|---------------:|--------:|--------------:|
| TSI 9, 30/70 | 963 | **59.09%** | 59.82% (62.6 / 57.1) | 57.56% | 14,617 · 57.84% |
| **RSI 7, 30/70** | **1,383** | 57.19% | 57.86% (58.1 / 57.6) | 55.89% | 21,602 · 57.50% |
| RSI 9, 35/65 | 1,563 | 57.39% | 57.82% (56.3 / 59.4) | 56.50% | 24,182 · 57.29% |
| RSI 8, 35/65 | 1,782 | 56.96% | 57.48% (56.0 / 58.8) | 55.88% | 28,080 · 57.52% |

TSI's two-point in-window lead shrinks to a third of a point on the unloaded
years, on half the bets, and its train halves are 5.5pp apart against RSI 7's
0.5pp. The bet gap is structural (it is the band and the length); the hit gap
is not. For a brief of **bets and hit rate**, RSI 7 30/70 is the knee of the
frontier — 44% more bets than TSI 9 at the same out-of-sample edge, with the
most even train halves of anything on it. It is Wilder's band at 7 bars: 105
minutes, the same wall-clock scale as the 5m Balanced's 14 × 5m = 70 min.
Carrying the 5m length over unchanged (RSI 14 on 15m = 3.5 h) scores 59.04% on
459 window bets but **55.40% on 7,099 unloaded ones** with three years under
54% — the length has to be rescaled, not copied.

| preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017-08 → 2026-03 | worst yr |
|--------|--------:|-------:|------:|--------:|---------------------------:|---------:|
| PM 15m Balanced | 1,383 | 57.19% | 57.86% | 55.89% | **57.50%** (21,602 bets, z +22.0) | 56.09% (2021) |

Per year on the full record, none of it fitted except the last six months:

| year | hit (bets) | | year | hit (bets) | | year | hit (bets) |
|---|---:|---|---|---:|---|---|---:|
| 2017 | 50.70% (785) | | 2021 | 56.09% (2,619) | | 2025 | 57.54% (2,737) |
| 2018 | 58.21% (2,479) | | 2022 | 57.38% (2,541) | | 2026 | 57.32% (1,907) |
| 2019 | 57.71% (2,367) | | 2023 | 58.70% (2,472) | | | |
| 2020 | 58.08% (2,450) | | 2024 | 58.45% (2,628) | | | |

**Every full year sits in a 56.1–58.7% band**, the 18 months right before the
window (2024-09 → 2026-03) score 57.41% on 4,057 bets, and 2017 — the year that
loses on every 5m preset — is at chance rather than under it. Whole record:
22,985 bets, 57.48%, z +22.7, about 7.5 bets a day. Read the hit rates against
49.9%: only 0.13% of 15m candles close exactly at their open (0.48% on 5m).

After the pick was frozen: the prefix test passes with 0 mismatches at three
cut points; bets run 49% long / 51% short and both sides win (window 57.14% /
57.24%, unloaded 58.22% / 56.81%) while 49.56% of window candles close up; and
it is not Reversal's PM 15m BOS relabelled — 21% of bars are shared (Jaccard
15.6%), the exclusive 1,090 bets score 55.87% against a 51.95% base rate for a
15m bar reversing the one before it, and the 293 shared bars run ~62%: the two
strategies agree on the strongest setups and disagree on the rest.

**Where it fails.** The worst month in the window is 2026-06 at 54.3% on 221
bets; the other six range 55.8–60.9%. Expect weeks at 54–55% and read the 57%
as a multi-month average.

**Not shipped.** TSI 9 30/70 if hit rate alone is the brief — its 57.84%
unloaded is real, it simply costs half the bets. RSI 8 35/65 for volume: 1,782
window bets at 56.96%, 28,080 unloaded at 57.52% (the highest z in the sweep,
+25.2), but a narrower band than the marginal favours and train halves 2.8pp
apart. Smoothing (RSI 7, 2 bars: 921 bets, 57.87% / 57.93%) trades a third of
the bets for nothing out of sample; an ATR floor of 0.20% lifts the window hit
to 57.87% and removes a third of the bets. Neither was taken.


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

#### 15-minute preset

Fitted on the **latest six months** of BTCUSDT 15m (2026-03-13 → 2026-09-13),
the protocol of the [Reversal](#15-minute-preset), Oscillators and Gann 15m
presets: train 03-13 → 07-13, holdout 07-13 → 09-13 scored once after the pick
was frozen, and the 8.5 years before the window **never loaded** by any sweep
stage — scored once at the end (16,348 bets there against 1,212 in the
window). Rule fixed before tuning: train bets ≥ 300, both halves above 52%,
every parameter off its grid edge, then highest train hit, read against the
marginals. One stage of 720 configs raced Reversion/Continuation x vol_ma_length {10, 20, 50} x vol_spike_mult {1.5..4.0} x rank gate {off, 90th pct} x min_body_ratio {0..0.6} x trend filter {off, Against, With SMA100}; two extension passes (~35) pushed the body to 0.8 and the spike down to 1.0 because both marginals ran to a grid edge, and tried the wick filter, the MA length and the ATR band on the frozen family.

Reversion is the family and Continuation its mirror (pooled train 55.04% vs 44.92%). Then a negative result about the premise: on 15m the VOLUME carries almost nothing and the BAR does. The spike threshold is flat on the unloaded years from 1.0x to 2.0x (55.8, 56.0, 56.0, 55.3%) and only trades bets for in-window hit above that (4.0x: 59.21% train, 54.74% holdout); the rank gate changes nothing (54.97 vs 55.14 train); but min_body_ratio is monotone in both windows (0.0: 52.96% train / 55.02% holdout -> 0.6: 58.25 / 60.59) and keeps rising to 0.8 on the unloaded years (55.3 -> 56.2%) as the bets fall away. Fading a decisive 15m bar earns ~56% whether or not its volume was a climax. The Against-Trend SMA100 filter that every 5m tier carries adds ~1.7pp on train (56.69 vs 55.00) and nothing on the unloaded years (55.1 vs 55.3% on the pick's family), so it is off; the wick filter is destructive (0.3: 52.23% in the window, 52.39% unloaded). The preset takes the interior of both extended grids: a 1.5x spike on a 20-bar volume mean, a 0.6 body, no rank gate, no trend filter.

| preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017-08 → 2026-03 | worst yr |
|--------|--------:|-------:|------:|--------:|---------------------------:|---------:|
| PM 15m Balanced | 1,212 | 58.25% | 58.07% | 58.63% | **56.04%** (16,348 bets, z +15.4) | 54.03% (2021) |

Per year on the full record, none of it fitted except the last six months:

| year | hit (bets) | | year | hit (bets) | | year | hit (bets) |
|---|---:|---|---|---:|---|---|---:|
| 2017 | 52.83% (672) | | 2021 | 54.03% (1,849) | | 2025 | 55.43% (2,576) |
| 2018 | 57.65% (1,594) | | 2022 | 54.14% (1,714) | | 2026 | 58.90% (1,703) |
| 2019 | 55.77% (1,578) | | 2023 | 57.65% (1,856) | |  |  |
| 2020 | 57.89% (1,686) | | 2024 | 56.13% (2,332) | |  |  |

Train halves 59.90 / 56.32%, about 6.6 bets a day; the 18 months right
before the window score 55.84% on 3,809 bets; whole record 17,560
bets at 56.19% (z +16.4). After the pick was frozen: the prefix test
passes with 0 mismatches at three cuts; bets run 51% long and both
sides win (window 57.49% / 59.03%, unloaded 56.90% / 55.19%); the mirror scores 41.58% in the window and 43.89% unloaded.

**Where it fails.** Worst month 2026-07 at 51.2% on 215 bets; worst
full year 2021 at 54.03%. THE WEAKEST 15m PRESET OUT OF SAMPLE: 56.0% unloaded against 58-59% in the window, and 2021-2022 sit at 54.0-54.1% and 2019 at 55.8%. The edge is concentrated in 2020 and 2023-2026 (57-60%); read the 58% as the regime, not the strategy.

**Not shipped.** The rule's own top row (spike 2.5x, rank 90, body 0.4): 493 window bets at 59.03%, but 53.74% unloaded with 2021 and 2022 at 50.2 / 50.5% — in-window fit. Body 0.7 at spike 1.5x: 802 bets at 59.10%, 56.41% unloaded, on two thirds of the bets. PM 5m Balanced carried over unchanged prints 62.38% on 319 window bets and 54.40% on 4,482 unloaded ones with 2022 at 47.7%. PM 5m Volume as-is: 56.63% window, 53.02% unloaded.

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

#### 15-minute preset

Fitted on the **latest six months** of BTCUSDT 15m (2026-03-13 → 2026-09-13),
the protocol of the [Reversal](#15-minute-preset), Oscillators and Gann 15m
presets: train 03-13 → 07-13, holdout 07-13 → 09-13 scored once after the pick
was frozen, and the 8.5 years before the window **never loaded** by any sweep
stage — scored once at the end (14,674 bets there against 1,076 in the
window). Rule fixed before tuning: train bets ≥ 300, both halves above 52%,
every parameter off its grid edge, then highest train hit, read against the
marginals. One stage of 1,152 configs raced Reversion/Continuation x cci_length {10..30} x cci_threshold {100..250} x wr_length {7..21} x Williams band {-5/-95 .. -20/-80} x wick confirm {off, 0.1, 0.25}; extension passes (~25) pushed the band to -3 and the CCI threshold down to 50 because both marginals ran to a grid edge, and tried the lengths, the recovery filter, the ATR band and a weekend gate on the frozen pick.

Reversion is the family and Continuation its mirror (pooled train 57.10% vs 42.79%), and this is the most robust of the video's ten on 15m: every Reversion config in the neighbourhood scores 59-60% on the unloaded years. The Williams band is monotone in both windows — the closer to the rail the better (-20: 56.31% train / 54.42% holdout, -10: 57.70 / 56.79, -5: 60.50 / 61.12) — and -5, the 5m Balanced's own value, is where the preset sits; -3 is no better unloaded (59.68 vs 59.56%) on half the bets. The CCI threshold is the volume dial: 50 to 150 all score 59.1-59.8% unloaded while the bets fall from 1,309 to 622, so the preset takes Lambert's classic 100 — the knee, interior of the extended grid. cci_length 20 tops train and holdout together; wr_length is flat 7-21. The wick confirmation is monotone against itself on the holdout (0: 57.43%, 0.1: 55.17, 0.25: 51.82) and is off, which makes the recovery filter a no-op. The ATR band is inert.

| preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017-08 → 2026-03 | worst yr |
|--------|--------:|-------:|------:|--------:|---------------------------:|---------:|
| PM 15m Balanced | 1,076 | 60.32% | 59.97% | 60.88% | **59.56%** (14,674 bets, z +23.2) | 56.47% (2025) |

Per year on the full record, none of it fitted except the last six months:

| year | hit (bets) | | year | hit (bets) | | year | hit (bets) |
|---|---:|---|---|---:|---|---|---:|
| 2017 | 52.39% (1,130) | | 2021 | 60.78% (1,754) | | 2025 | 56.47% (2,920) |
| 2018 | 61.14% (1,230) | | 2022 | 60.33% (1,147) | | 2026 | 60.29% (1,501) |
| 2019 | 62.88% (1,091) | | 2023 | 60.98% (1,325) | |  |  |
| 2020 | 64.33% (1,424) | | 2024 | 59.38% (2,228) | |  |  |

Train halves 61.02 / 59.04%, about 5.8 bets a day; the 18 months right
before the window score 56.87% on 4,183 bets; whole record 15,750
bets at 59.61% (z +24.1). After the pick was frozen: the prefix test
passes with 0 mismatches at three cuts; bets run 51% long and both
sides win (window 58.73% / 61.98%, unloaded 59.82% / 59.38%); the mirror scores 39.68% in the window and 40.30% unloaded.

**Where it fails.** Worst month 2026-04 at 58.4% on 161 bets; worst
full year 2025 at 56.47%. The edge decays: 2018-2023 run 60-64% and 2024-2025 59.4 / 56.5%, so the recent years are the estimate. Train halves 61.0 / 59.0%; no window month under 58%.

**Not shipped.** The rule's own top row (CCI 200, band -10): 491 window bets at 61.30%, 59.47% unloaded. CCI 150 at -5: 622 at 61.09%, 59.75% unloaded — a Selective tier. CCI 100 at -10: 2,110 at 57.35%, 58.92% on 30,150 unloaded bets (z +31.0) — a Volume tier. cci_length 30: 878 at 61.05%. Weekend-only: 242 at 61.98%. PM 5m Balanced as-is: 300 at 63.00% in the window, 60.23% unloaded; PM 5m Selective as-is: 509 at 61.30%, 59.60% unloaded — both 5m presets transfer to 15m as they are.

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

#### 15-minute preset

Fitted on the **latest six months** of BTCUSDT 15m (2026-03-13 → 2026-09-13),
the protocol of the [Reversal](#15-minute-preset), Oscillators and Gann 15m
presets: train 03-13 → 07-13, holdout 07-13 → 09-13 scored once after the pick
was frozen, and the 8.5 years before the window **never loaded** by any sweep
stage — scored once at the end (27,527 bets there against 1,770 in the
window). Rule fixed before tuning: train bets ≥ 300, both halves above 52%,
every parameter off its grid edge, then highest train hit, read against the
marginals. One stage of 1,440 configs raced atr_length {14, 20} x jump1 {1.0..2.5} x jump2 {3, 5, no cap} x close_extreme {0, 0.5} x wick {0, 0.2, 0.35} x rsi_length {7, 14} x RSI band {25/75..40/60}; a second pass (~15) tried the jump and RSI neighbourhood, the ATR band and the day-of-week gates on the frozen pick. Filter-off values (wick 0, no cap) were exempt from the grid-edge rule.

The candle filters lose, as on 5m: the rejection wick is monotone against itself (0: 56.43% train / 56.05% holdout, 0.2: 55.37 / 52.05, 0.35: 50.42 / 46.32) and the close-extreme gate changes nothing, so both are off. The jump upper bound is a no-op above 5 ATR (the 5m Volume's 3.0 cap loses 1.5pp on train) and the preset leaves it open. The rule's in-window favourite — a jump of 2.0 ATR — is a fit: 686 window bets at 59.33% with a 60.43% holdout, but 53.94% on the unloaded years with 2021 at 48.9%. The 5m presets' own jump threshold of 1.3 ATR with a faster RSI (7 bars, 35/65) is the config that holds: 1,770 bets at 57.06% in the window and 56.21% on 27,527 unloaded bets, every full year 55.4-57.6%. jump1 1.45 is a hit-for-volume dial (1,400 at 58.07%); rsi_length 7 beats 5 and 10 on both windows; the ATR band is inert. The 5m day-of-week finding transfers in the SAME direction on the unloaded years: weekend-only scores 58.41% on 7,728 unloaded bets and Saturday-only 59.68% on 3,368, against 56.21% all days — but in the six-month window neither beats all-days (56.69% and 56.48% vs 57.06%), so the gate was not fitted here and the preset trades every day.

| preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017-08 → 2026-03 | worst yr |
|--------|--------:|-------:|------:|--------:|---------------------------:|---------:|
| PM 15m Balanced | 1,770 | 57.06% | 57.54% | 56.11% | **56.21%** (27,527 bets, z +20.6) | 55.43% (2022) |

Per year on the full record, none of it fitted except the last six months:

| year | hit (bets) | | year | hit (bets) | | year | hit (bets) |
|---|---:|---|---|---:|---|---|---:|
| 2017 | 52.44% (942) | | 2021 | 55.61% (3,246) | | 2025 | 55.61% (3,291) |
| 2018 | 56.58% (3,001) | | 2022 | 55.43% (3,446) | | 2026 | 57.52% (2,439) |
| 2019 | 56.59% (2,997) | | 2023 | 57.58% (3,482) | |  |  |
| 2020 | 56.65% (2,976) | | 2024 | 56.28% (3,477) | |  |  |

Train halves 60.11 / 55.17%, about 9.6 bets a day; the 18 months right
before the window score 55.94% on 4,984 bets; whole record 29,297
bets at 56.27% (z +21.4). After the pick was frozen: the prefix test
passes with 0 mismatches at three cuts; bets run 51% long and both
sides win (window 56.86% / 57.27%, unloaded 56.83% / 55.54%).

**Where it fails.** Worst month 2026-06 at 53.9% on 284 bets; worst
full year 2022 at 55.43%. Train halves are 60.1 / 55.2%, and 2026-06 ran at 53.9% on 284 bets. The unloaded years are a flat 55.4-57.6% — a real but modest edge with no strong year to lean on.

**Not shipped.** Weekend-only: 538 window bets at 56.69%, 58.41% on 7,728 unloaded (z +14.8) — the 5m day finding, alive out of sample and not in the window. jump1 2.0 (the rule's row): 686 at 59.33% in the window, 53.94% unloaded. jump1 1.45: 1,400 at 58.07%, not scored unloaded. PM 5m All Days as-is: 627 at 57.26%, 55.14% unloaded; PM 5m Volume as-is: 499 at 56.31%, 55.90% unloaded.

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

#### 15-minute preset

Fitted on the **latest six months** of BTCUSDT 15m (2026-03-13 → 2026-09-13),
the protocol of the [Reversal](#15-minute-preset), Oscillators and Gann 15m
presets: train 03-13 → 07-13, holdout 07-13 → 09-13 scored once after the pick
was frozen, and the 8.5 years before the window **never loaded** by any sweep
stage — scored once at the end (21,913 bets there against 1,302 in the
window). Rule fixed before tuning: train bets ≥ 300, both halves above 52%,
every parameter off its grid edge, then highest train hit, read against the
marginals. One stage of 1,536 configs raced Reversion/Breakout x squeeze gate on/off x bb_length {14..50} x bb_mult {1.5..3.0} x %B band {0.95..1.1} x min_body_ratio {0, 0.2, 0.4} x trend filter {off, With EMA200}; a second pass (~15) tried the ATR band and length, the EMA bias, squeeze percentiles 20-60 and a weekend gate on the frozen pick.

Reversion is the family and Breakout its mirror (pooled train 56.69% vs 43.27%), so on 15m as on 5m this trades the band stretch as a fade and the 'squeeze' is switched off: the squeeze gate costs 80% of the bets for nothing (pooled 56.15% vs 56.80% train; on the pick, 238 bets at 60.92% with a 56.47% holdout). Marginals: bb_length 20 is best on both train and holdout (57.21 / 55.99) and 50 loses the holdout (51.66); bb_mult is monotone on train (1.5: 55.90 -> 3.0: 59.66) at a steep cost in bets; the %B band is monotone too (0.95: 56.39 -> 1.1: 57.38); the body filter is mildly positive (0.0: 56.30, 0.4: 57.41). The pick is the 5m Balanced's own band geometry — 20/2.0 with %B 1.05/-0.05 and a 0.2 body — WITHOUT its With-Trend EMA200 filter, which on the pooled holdout loses 1.7pp (53.07 vs 54.75) and removes two thirds of the bets. It is third of the seven rule-eligible rows on train (59.38% against 59.80% for the top row) with 1.7x the bets and the better holdout and unloaded years, so it is the one shipped. The ATR band and length are inert.

| preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017-08 → 2026-03 | worst yr |
|--------|--------:|-------:|------:|--------:|---------------------------:|---------:|
| PM 15m Balanced | 1,302 | 58.68% | 59.38% | 57.31% | **57.46%** (21,913 bets, z +22.1) | 55.68% (2022) |

Per year on the full record, none of it fitted except the last six months:

| year | hit (bets) | | year | hit (bets) | | year | hit (bets) |
|---|---:|---|---|---:|---|---|---:|
| 2017 | 49.93% (769) | | 2021 | 56.75% (2,497) | | 2025 | 57.12% (2,584) |
| 2018 | 58.53% (2,438) | | 2022 | 55.68% (2,674) | | 2026 | 59.16% (1,817) |
| 2019 | 58.90% (2,555) | | 2023 | 58.31% (2,598) | |  |  |
| 2020 | 59.42% (2,568) | | 2024 | 56.76% (2,715) | |  |  |

Train halves 62.94 / 55.86%, about 7.1 bets a day; the 18 months right
before the window score 57.14% on 3,892 bets; whole record 23,215
bets at 57.53% (z +22.9). After the pick was frozen: the prefix test
passes with 0 mismatches at three cuts; bets run 49% long and both
sides win (window 57.35% / 59.94%, unloaded 58.22% / 56.70%); the mirror scores 41.24% in the window and 42.49% unloaded.

**Where it fails.** Worst month 2026-07 at 55.2% on 239 bets; worst
full year 2022 at 55.68%. Train halves are 62.9 / 55.9%; the first two window months ran 61-64% and the rest 55-58%, so read the 58.7% as inflated by the spring and the 57.5% unloaded figure as the estimate.

**Not shipped.** The With-Trend EMA200 variant of the same band (the 5m Volume's filter): 418 window bets at 58.61% and 59.98% on 6,576 unloaded bets (z +16.2), every full year 56.7-63.9% — the highest out-of-sample hit rate in this sweep, on a third of the bets; the pooled marginal is against it but this cell is not, and it is the right Selective tier if one is wanted. bb_length 14 at %B 1.0: 1,744 bets at 56.54%, 57.95% unloaded (z +27.2). The rule's top row (20/2.5, %B 1.0): 750 at 58.40%, 57.11% unloaded. bb_mult 1.5: 4,045 at 55.45% for a Volume tier (56.43% unloaded, z +32.6). Weekend-only: 403 at 60.30%.

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

#### 15-minute preset

Fitted on the **latest six months** of BTCUSDT 15m (2026-03-13 → 2026-09-13),
the protocol of the [Reversal](#15-minute-preset), Oscillators and Gann 15m
presets: train 03-13 → 07-13, holdout 07-13 → 09-13 scored once after the pick
was frozen, and the 8.5 years before the window **never loaded** by any sweep
stage — scored once at the end (25,481 bets there against 1,645 in the
window). Rule fixed before tuning: train bets ≥ 300, both halves above 52%,
every parameter off its grid edge, then highest train hit, read against the
marginals. One stage of 960 configs raced Reversion/Momentum x lookback {10..50} (SMA, StdDev and Keltner lengths tied) x z threshold {1.5..3.0} x Keltner gate on/off x kc_mult {1.0..2.0} x bias MA on/off x trend filter {off, With EMA200}; a second pass (~25) tried the trend filter at other lengths and against the Keltner gate, the ATR band and length, a separate StdDev length and a weekend gate.

Reversion is the family and Momentum its mirror (pooled train 56.45% vs 43.85%). A z-score of 2 on SMA20 / StdDev20 IS %B = 1 on a 20/2.0 Bollinger band by construction — the un-gated z=2 config reproduces BB Squeeze's body-0 config bet for bet (2,081 at 56.80% in the window, 34,98x at 56.86% unloaded) — so what this strategy adds is the Keltner confirmation, and that is what the preset carries: z >= 2 on a 20-bar mean, confirmed by a close outside a 1.5-ATR Keltner channel. Marginals: lookback 20 is best on train and 30-50 lose the holdout (52.95 / 51.32); the z threshold is monotone on train (1.5: 55.66 -> 3.0: 59.30) at a steep cost in bets; the Keltner gate adds about a point (57.04 vs 56.09) for 40% fewer bets; kc_mult is flat 1.0-2.0. The preset is PM 5m Volume's geometry without its bias MA, which on 15m halves the bets for a holdout that is no better. The With-Trend EMA200 filter is the strong result here and it did NOT make the preset: on this family it scores 60.1-60.9% on 2,700-6,200 unloaded bets with train, holdout and unloaded all within a point of each other, but on a quarter of the bets (see NOT SHIPPED).

| preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017-08 → 2026-03 | worst yr |
|--------|--------:|-------:|------:|--------:|---------------------------:|---------:|
| PM 15m Balanced | 1,645 | 57.33% | 57.63% | 56.73% | **56.54%** (25,481 bets, z +20.9) | 54.81% (2022) |

Per year on the full record, none of it fitted except the last six months:

| year | hit (bets) | | year | hit (bets) | | year | hit (bets) |
|---|---:|---|---|---:|---|---|---:|
| 2017 | 50.65% (924) | | 2021 | 55.91% (3,003) | | 2025 | 55.37% (3,276) |
| 2018 | 56.45% (2,776) | | 2022 | 54.81% (2,972) | | 2026 | 57.91% (2,276) |
| 2019 | 58.10% (2,611) | | 2023 | 57.85% (3,051) | |  |  |
| 2020 | 58.45% (2,835) | | 2024 | 56.88% (3,402) | |  |  |

Train halves 59.89 / 55.50%, about 8.9 bets a day; the 18 months right
before the window score 56.01% on 4,924 bets; whole record 27,126
bets at 56.58% (z +21.7). After the pick was frozen: the prefix test
passes with 0 mismatches at three cuts; bets run 49% long and both
sides win (window 55.20% / 59.38%, unloaded 57.51% / 55.60%); the mirror scores 42.61% in the window and 43.40% unloaded.

**Where it fails.** Worst month 2026-08 at 55.4% on 271 bets; worst
full year 2022 at 54.81%. Train halves are 59.9 / 55.5%. The Keltner gate does not change WHEN it fails: the same weeks that hurt BB Squeeze hurt this.

**Not shipped.** PM 5m Balanced carried over unchanged except for the ATR band (z 2.0, KC 1.0, With Trend EMA200): 390 window bets at 60.26% with train 60.38 / holdout 60.00, and 60.13% on 6,245 unloaded bets (z +16.0), worst year 56.51% — the most consistent number across every window in this sweep, at 2 bets a day. It is under the 300-train-bet floor (260) and that is the only reason it is not the preset; anyone who wants hit rate over volume should use it. The rule's own top row (z 2.5, no Keltner): 773 bets at 57.96%, 56.74% unloaded. Against Trend: 1,383 at 56.91%, 55.94% unloaded. Weekend-only: 490 at 58.16%.

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

#### 15-minute preset

Fitted on the **latest six months** of BTCUSDT 15m (2026-03-13 → 2026-09-13),
the protocol of the [Reversal](#15-minute-preset), Oscillators and Gann 15m
presets: train 03-13 → 07-13, holdout 07-13 → 09-13 scored once after the pick
was frozen, and the 8.5 years before the window **never loaded** by any sweep
stage — scored once at the end (21,222 bets there against 1,475 in the
window). Rule fixed before tuning: train bets ≥ 300, both halves above 52%,
every parameter off its grid edge, then highest train hit, read against the
marginals. One stage of 1,728 configs raced all four regime mappings x detector {ADX, Efficiency Ratio} x threshold {20, 25, 30} x channel_length {5..30} x breakout_buffer_atr {0, 0.3, 0.6} x min_body_ratio {0, 0.2, 0.4} x trend filter {off, With EMA200}; a second pass (~20) extended the buffer to 0.9, tried channel 15, the ATR band and a weekend gate on the frozen family.

The switch does nothing on 15m, as it did nothing on 5m: Always Reversion pools 58.16% train / 57.59% holdout, Always Momentum is its mirror at 41.69 / 41.56, and both switching mappings land between them (46.4% and 51.4%) — the regime detector only dilutes a fade with momentum bets that lose. (Pooled over the two switching mappings the detector and threshold marginals are identical by construction, since the two mappings are complements; within 'Trend=Reversion, Range=Momentum' alone, ER 25 at channel 20 scores 55.14% unloaded against 56.65% for the same channel unswitched.) So the preset is a Donchian-break fade, and it is PM 5m Volume's own geometry — a 10-bar channel, a 0.3-ATR buffer, a 0.2 body — without the With-Trend EMA200 filter, which on 15m removes 70% of the bets for a holdout 2pp worse (55.88 vs 58.07 pooled). Marginals: channel 20 tops train (59.75%) but channel 10 scores 57.69% on the unloaded years against 56.65% for 20, on 45% more bets, and is within 1.5pp of it on train; the buffer is flat 0-0.3 and looks monotone beyond (0.6: 61.28% in the window, 0.9: 63.51%) but that is a fit — 0.9 scores 55.32% unloaded with 2021 at 48.0%; the body filter is inert. The ATR band is inert.

| preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017-08 → 2026-03 | worst yr |
|--------|--------:|-------:|------:|--------:|---------------------------:|---------:|
| PM 15m Balanced | 1,475 | 58.03% | 58.35% | 57.45% | **57.69%** (21,222 bets, z +22.4) | 54.10% (2022) |

Per year on the full record, none of it fitted except the last six months:

| year | hit (bets) | | year | hit (bets) | | year | hit (bets) |
|---|---:|---|---|---:|---|---|---:|
| 2017 | 52.60% (905) | | 2021 | 57.34% (2,546) | | 2025 | 56.82% (3,131) |
| 2018 | 57.49% (2,164) | | 2022 | 54.10% (2,290) | | 2026 | 58.79% (2,041) |
| 2019 | 60.50% (1,972) | | 2023 | 58.63% (2,456) | |  |  |
| 2020 | 60.49% (2,230) | | 2024 | 58.00% (2,962) | |  |  |

Train halves 60.30 / 56.54%, about 8.0 bets a day; the 18 months right
before the window score 57.53% on 4,650 bets; whole record 22,697
bets at 57.71% (z +23.2). After the pick was frozen: the prefix test
passes with 0 mismatches at three cuts; bets run 50% long and both
sides win (window 57.71% / 58.36%, unloaded 58.15% / 57.27%); the mirror scores 41.97% in the window and 42.26% unloaded.

**Where it fails.** Worst month 2026-09 at 54.7% on 95 bets; worst
full year 2022 at 54.10%. Train halves are 60.3 / 56.5%. 2022 is the worst full year at 54.1% and 2021-2022 run 54-57% against 60% in 2019-2020: the fade of a channel break is weaker in a trending year.

**Not shipped.** The rule's top row (channel 20): 1,004 window bets at 59.56% with a 59.04% holdout, but 56.65% unloaded with 2022 at 53.4%. PM 5m Volume carried over unchanged (channel 10 + With Trend EMA200): 420 window bets at 58.57% and 60.37% on 5,239 unloaded bets (z +15.0), worst year 55.8% — the high-hit variant, at a third of the bets. Buffer 0.6: 594 at 61.28% in the window, 56.54% unloaded with 2022 at 51.6%. Weekend-only was not better than all days here.

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

#### 15-minute preset

Fitted on the **latest six months** of BTCUSDT 15m (2026-03-13 → 2026-09-13),
the protocol of the [Reversal](#15-minute-preset), Oscillators and Gann 15m
presets: train 03-13 → 07-13, holdout 07-13 → 09-13 scored once after the pick
was frozen, and the 8.5 years before the window **never loaded** by any sweep
stage — scored once at the end (29,560 bets there against 1,795 in the
window). Rule fixed before tuning: train bets ≥ 300, both halves above 52%,
every parameter off its grid edge, then highest train hit, read against the
marginals. Stage 1 (225 configs) raced direction {Both, Long Only, Short Only} x trend filter {off, With, Against EMA200} x rsi_length {5..21} x band {20/80..40/60} at BB 20/2.0. Stage 2 (1,152) tuned rsi_length, band, bb_length {14..50}, bb_mult {1.5..3.0} and the %B band inside the two surviving directions. Stage 3 (~20) tried the ATR band, the candle filters, the bias filter, the trend filter and a weekend gate on the frozen pick. Candle filters at 0 throughout, as every 5m winner set them.

The window favours the SHORT side (Short Only pooled train 59.67% against Both 57.81%, holdout 55.47 vs 55.36) and the 5m sweep found the opposite, Long Only. Neither survives the other's years: Short Only scores 56.21% unloaded with 2017 at 46.1%, Long Only 57.69% unloaded but 55.92% in the window. The side that wins is a regime, not a property of the setup, so the preset trades both; it is Wilder's 30/70 band on a 7-bar RSI with the stock 20/2.0 Bollinger and a 0-1 %B band, i.e. the setup with nothing fitted but the length. Marginals: rsi_length 5-10 flat (57.3-57.9% train), 14 loses the holdout; bb_mult rises monotonically on train (1.5: 56.85 -> 3.0: 58.72) but the wider band costs 6x the bets; bb_length 14-20 beat 30-50 on the holdout (56.4 / 55.8 vs 54.6 / 54.0). The candle filters are actively harmful — min_close_recovery 0.3 drops the pick to 52.86% (holdout 49.55%) and min_wick_ratio 0.3 to 53.03% — which extends the 5m finding that they earn nothing. The ATR band, ATR length and bias filter are inert or only remove bets.

| preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017-08 → 2026-03 | worst yr |
|--------|--------:|-------:|------:|--------:|---------------------------:|---------:|
| PM 15m Balanced | 1,795 | 57.10% | 57.43% | 56.45% | **56.94%** (29,560 bets, z +23.9) | 55.28% (2022) |

Per year on the full record, none of it fitted except the last six months:

| year | hit (bets) | | year | hit (bets) | | year | hit (bets) |
|---|---:|---|---|---:|---|---|---:|
| 2017 | 49.34% (1,058) | | 2021 | 56.48% (3,513) | | 2025 | 55.39% (3,430) |
| 2018 | 57.16% (3,445) | | 2022 | 55.28% (3,569) | | 2026 | 57.85% (2,491) |
| 2019 | 59.12% (3,383) | | 2023 | 58.31% (3,423) | |  |  |
| 2020 | 58.66% (3,408) | | 2024 | 57.03% (3,635) | |  |  |

Train halves 59.76 / 55.17%, about 9.7 bets a day; the 18 months right
before the window score 56.32% on 5,201 bets; whole record 31,355
bets at 56.95% (z +24.6). After the pick was frozen: the prefix test
passes with 0 mismatches at three cuts; bets run 49% long and both
sides win (window 55.92% / 58.23%, unloaded 57.69% / 56.21%).

**Where it fails.** Worst month 2026-08 at 55.0% on 289 bets; worst
full year 2022 at 55.28%. Train halves are 59.76 / 55.17%, and 2026-08 ran at 55.0%. Expect weeks at 54-55%.

**Not shipped.** The rule's own single best row (Both, RSI 10, BB 20/2.5): 570 window bets at 59.12%, but 56.30% unloaded with two years at 53.8% — it is on the same plateau on a third of the bets. With Trend EMA200 on the pick: 337 bets at 61.42% with train 61.40% and holdout 61.47%, a genuine high-hit variant at a fifth of the volume. Weekend-only: 545 bets at 58.17%. The 5m Wknd/Hi Hit families were not re-swept at 15m; their presets carried over as-is produce 19-159 window bets.

## 15-minute presets (fitted on the latest six months)

Every strategy now carries a **PM 15m** preset for the Polymarket 15-minute
up/down market, fitted under one protocol: the six months 2026-03-13 → 09-13
were the only data any sweep stage read (train 03-13 → 07-13 for selection,
holdout 07-13 → 09-13 scored once after each pick was frozen), and the 8.5
years before the window — 2017-08-17 → 2026-03-13 — were scored **once at the
end** as the out-of-sample check. Selection was mechanical (train bets ≥ 300,
both train halves above 52%, every swept parameter off its grid edge, then
highest train hit, read against the marginals); where the rule's single best
row failed the unloaded years and a neighbour on the same train plateau did
not, the neighbour was shipped and the reason is written in the strategy's
file. Each strategy's own section carries the full write-up; the four
strategies without a section of their own are written up below. Hit rates
read against 49.9% — 0.13% of 15m candles close exactly at their open.

| strategy | preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017-08 → 2026-03 | worst full yr | link |
|---|---|--:|--:|--:|--:|--:|--:|---|
| CCI Williams | PM 15m Balanced | 1,076 | 60.32% | 59.97% | 60.88% | **59.56%** (14,674, z +23.2) | 56.47% (2025) | [§](#cci-williams-strategy-9) |
| Candlesticks | PM 15m Balanced | 1,119 | 57.73% | 57.67% | 57.84% | **58.65%** (14,226, z +20.6) | 56.79% (2022) | [§](#candlesticks-beyond-the-video) |
| Harmonic Patterns | PM 15m Balanced | 539 | 57.51% | 58.57% | 55.56% | **58.47%** (9,046, z +16.1) | 56.54% (2021) | [§](#harmonic-patterns-beyond-the-video) |
| Reversal | PM 15m BOS | 793 | 58.64% | 56.83% | 62.55% | **58.02%** (12,006, z +17.6) | 54.70% (2025) | [§](#reversal-beyond-the-video) |
| Momentum Indicators | PM 15m Balanced | 1,242 | 57.57% | 58.16% | 56.39% | **57.85%** (18,312, z +21.2) | 57.37% (2026) | [§](#momentum-indicators-beyond-the-video) |
| Regime Switch | PM 15m Balanced | 1,475 | 58.03% | 58.35% | 57.45% | **57.69%** (21,222, z +22.4) | 54.10% (2022) | [§](#regime-switch-strategy-6) |
| Oscillators | PM 15m Balanced | 1,383 | 57.19% | 57.86% | 55.89% | **57.50%** (21,602, z +22.0) | 56.09% (2021) | [§](#oscillators-beyond-the-video) |
| BB Squeeze | PM 15m Balanced | 1,302 | 58.68% | 59.38% | 57.31% | **57.46%** (21,913, z +22.1) | 55.68% (2022) | [§](#bb-squeeze-strategy-4) |
| CHoCH (Change of Character) | PM 15m Balanced | 966 | 58.39% | 58.19% | 58.77% | **57.41%** (15,310, z +18.3) | 56.54% (2024) | [§](#choch-change-of-character-beyond-the-video) |
| Stoch Wick | PM 15m Balanced | 2,457 | 56.86% | 57.49% | 55.83% | **57.39%** (33,116, z +26.9) | 54.21% (2025) | [§](#stoch-wick) |
| Elliott Wave | PM 15m Balanced | 478 | 59.62% | 62.31% | 54.14% | **57.32%** (6,872, z +12.1) | 54.57% (2025) | [§](#elliott-wave-beyond-the-video) |
| Renko | PM 15m Balanced | 2,238 | 57.42% | 57.62% | 57.05% | **57.21%** (32,046, z +25.8) | 55.88% (2022) | [§](#renko-beyond-the-video) |
| Multi Horizon | PM 15m Balanced | 1,303 | 57.33% | 57.99% | 56.04% | **57.21%** (21,208, z +21.0) | 55.11% (2022) | [§](#multi-horizon-strategy-10) |
| ATR DevExh | PM 15m Balanced | 873 | 58.19% | 59.27% | 56.15% | **57.18%** (13,413, z +16.6) | 54.14% (2022) | [§](#atr-devexh) |
| RSI + BB | PM 15m Balanced | 1,795 | 57.10% | 57.43% | 56.45% | **56.94%** (29,560, z +23.9) | 55.28% (2022) | [§](#rsi--bb-strategy-1) |
| Gann Angles | PM 15m Fan | 1,844 | 57.54% | 57.61% | 57.40% | **56.91%** (28,974, z +23.5) | 55.68% (2021) | [§](#gann-angles-beyond-the-video) |
| Fib Retracement | PM 15m Balanced | 479 | 56.78% | 57.37% | 55.62% | **56.57%** (7,824, z +11.6) | 54.14% (2018) | [§](#fib-retracement-beyond-the-video) |
| Trend Lines | PM 15m Line Break | 705 | 57.45% | 58.87% | 54.73% | **56.54%** (10,292, z +13.3) | 53.35% (2022) | [§](#trend-lines) |
| Zscore MS | PM 15m Balanced | 1,645 | 57.33% | 57.63% | 56.73% | **56.54%** (25,481, z +20.9) | 54.81% (2022) | [§](#zscore-ms-strategy-5) |
| Support & Resistance | PM 15m Level Break | 1,394 | 56.10% | 56.92% | 54.51% | **56.42%** (22,423, z +19.2) | 55.26% (2022) | [§](#support--resistance-beyond-the-video) |
| Jump Exhaustion | PM 15m Balanced | 1,770 | 57.06% | 57.54% | 56.11% | **56.21%** (27,527, z +20.6) | 55.43% (2022) | [§](#jump-exhaustion-strategy-8) |
| Volume Exhaustion | PM 15m Balanced | 1,212 | 58.25% | 58.07% | 58.63% | **56.04%** (16,348, z +15.4) | 54.03% (2021) | [§](#volume-exhaustion-strategy-7) |
| Fair Value Gap | PM 15m Balanced | 482 | 57.05% | 56.48% | 58.01% | **54.16%** (4,939, z +5.8) | 50.76% (2022) | [§](#fair-value-gap) |

The Combined strategy carries three 15m presets that point every voter at
its own 15m preset: *PM 15m Any (OR)* 6,731 window bets at 55.65% (56.21% on
99,648 unloaded bets, z +39.2), *PM 15m Confirmed (2 agree)* 4,188 at 56.30%
(57.27% on 62,382, z +36.3), *PM 15m Conviction (3 agree)* 3,249 at 57.49%
(57.56% on 48,134, z +33.2). Agreement buys hit rate monotonically, as on 5m,
but the voters overlap heavily — the level-break family (Reversal, CHoCH,
Support & Resistance, Trend Lines, Gann) and the oscillator-fade family
(Oscillators, RSI+BB, Stoch Wick, CCI Williams, Momentum, Zscore, BB Squeeze)
each share a large fraction of their bars — so "2 agree" is mostly one idea
confirmed by a sibling. Moon Phase has no 15m preset, on purpose: the best of
132 configs on train scores 49.99% on the unloaded years (see its section).

### What the 22 sweeps have in common

- **Fade wins everywhere.** In all 22, the direction that earns is the fade —
  of a band extreme, a structure break, a decisive bar, a jump, a climax, a
  Renko brick — and the follow / continuation reading is its mirror at 42-47%.
  Fair Value Gap (continuation) and Elliott Wave (follow the count inside the
  retrace) are the two exceptions, and both are the same trade seen from the
  other side: a pullback into a level, bought.
- **The wall-clock scale is ~90 minutes.** Reversal (6 bars), CHoCH (6),
  Trend Lines (6), Support & Resistance (6) and Gann's flat-level alternative
  (5) all landed on a 5-6 bar pivot; Oscillators on a 7-bar RSI (105 min).
  The 5m presets sit at 12-20 bars (60-100 min). It is the same scale; the
  bar count is what changes.
- **Candle confirmation loses.** The rejection wick (Stoch Wick, Jump
  Exhaustion, CCI Williams), the recovery close (RSI+BB, Stoch Wick), the
  reaction bar (Fair Value Gap) and the textbook reversal candlesticks are
  monotone *against* themselves on the holdout, as they were on 5m. The
  opposing-bar timing gate (Multi Horizon, Fib, Elliott) is the one candle
  rule that earns.
- **2022 and 2025 are the weak years.** Sixteen of the 22 have one of them as
  their worst full year; the momentum-fade family decays from 59-62% in 2018-2020 to
  56-57% now. The recent numbers are the live estimate.
- **The weakest four** out of sample are Fair Value Gap (54.2%), Volume
  Exhaustion (56.0%), Jump Exhaustion (56.2%) and Support & Resistance (56.4%); the
  strongest are CCI Williams (59.6%), Candlesticks (58.7%) and Harmonic
  (58.5%).

### Stoch Wick

Fitted on the **latest six months** of BTCUSDT 15m (2026-03-13 → 2026-09-13),
the protocol of the [Reversal](#15-minute-preset), Oscillators and Gann 15m
presets: train 03-13 → 07-13, holdout 07-13 → 09-13 scored once after the pick
was frozen, and the 8.5 years before the window **never loaded** by any sweep
stage — scored once at the end (33,116 bets there against 2,457 in the
window). Rule fixed before tuning: train bets ≥ 300, both halves above 52%,
every parameter off its grid edge, then highest train hit, read against the
marginals. One stage of 720 configs raced Reversion/Breakout x stoch_k_length {5..28} x stoch_d_length {1, 3} x band {5/95..25/75} x min_wick_ratio {0, 0.1, 0.2} x ADX gate on/off; a second pass (~20) tried %D 1-5, the recovery filter, the ATR band, ADX ceilings 20-40, the trend filter and a weekend gate on the frozen pick. Recovery at 0 throughout, as every 5m preset has it.

Reversion is the family and Breakout its exact mirror (pooled train 55.56% vs 44.40%). Inside Reversion the marginals say: the band is monotone — tighter is better (5/95: 58.64% train, 25/75: 54.64%) at a steep cost in bets (187 vs 2,991 a config); k=14 is the best length on both train and holdout (56.01 / 53.95) and 28 loses the holdout; %D 3 beats 1 on train and loses on the holdout; and the rejection WICK, the setup's namesake, is monotone against itself on the holdout (0.0: 54.41%, 0.1: 52.68%, 0.2: 50.80%), with the recovery filter worse still (0.2 drops the pick to 55.11%, holdout 52.51%). The stochastic extreme carries the edge; the candle does not — the same verdict RSI+BB's candle filters got. The rule's own best row (k14 %D3, band 10/90, wick 0.1) has 616 window bets at 59.09% and 58.01% unloaded, but 2025 at 52.0%. Opening the band to 15/85 and switching the wick off puts it on the same plateau with four times the bets, the most even train halves in the sweep (57.13 / 57.83) and a better worst year, so that is the preset. The ADX gate is a hit-for-volume dial (ADX <= 20: 701 bets at 58.06%; off: 2,457 at 56.86%) and is left off; the ATR band and trend filter are inert or only remove bets.

| preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017-08 → 2026-03 | worst yr |
|--------|--------:|-------:|------:|--------:|---------------------------:|---------:|
| PM 15m Balanced | 2,457 | 56.86% | 57.49% | 55.83% | **57.39%** (33,116 bets, z +26.9) | 54.21% (2025) |

Per year on the full record, none of it fitted except the last six months:

| year | hit (bets) | | year | hit (bets) | | year | hit (bets) |
|---|---:|---|---|---:|---|---|---:|
| 2017 | 52.28% (1,754) | | 2021 | 57.69% (4,448) | | 2025 | 54.21% (6,069) |
| 2018 | 60.67% (2,911) | | 2022 | 56.33% (3,437) | | 2026 | 57.55% (3,300) |
| 2019 | 60.16% (2,525) | | 2023 | 58.02% (3,204) | |  |  |
| 2020 | 61.72% (3,054) | | 2024 | 56.74% (4,871) | |  |  |

Train halves 57.12 / 57.82%, about 13.3 bets a day; the 18 months right
before the window score 55.10% on 8,588 bets; whole record 35,573
bets at 57.35% (z +27.7). After the pick was frozen: the prefix test
passes with 0 mismatches at three cuts; bets run 47% long and both
sides win (window 57.01% / 56.73%, unloaded 57.85% / 57.10%); the mirror scores 43.14% in the window and 42.51% unloaded.

**Where it fails.** Worst month 2026-08 at 53.8% on 444 bets; worst
full year 2025 at 54.21%. The edge DECAYS: 2018-2020 run 60-62%, 2025 is the worst full year of every config tried (52-54%) and 2026-08 ran at 53.8% on 444 bets. The recent numbers are the live estimate.

**Not shipped.** PM 5m Volume carried over unchanged (band 5/95, wick 0.1) prints 60.91% on 440 window bets and 59.63% on 5,534 unloaded — the highest hit rate here, on a sixth of the bets; a Selective tier if one is wanted. With Trend EMA200: 798 bets at 58.90% (train 60.81, holdout 55.78). ADX <= 20: 701 at 58.06%. %D 5: 1,625 at 57.66% with the strongest train (58.97) but a holdout under the pick's.

### ATR DevExh

Fitted on the **latest six months** of BTCUSDT 15m (2026-03-13 → 2026-09-13),
the protocol of the [Reversal](#15-minute-preset), Oscillators and Gann 15m
presets: train 03-13 → 07-13, holdout 07-13 → 09-13 scored once after the pick
was frozen, and the 8.5 years before the window **never loaded** by any sweep
stage — scored once at the end (13,413 bets there against 873 in the
window). Rule fixed before tuning: train bets ≥ 300, both halves above 52%,
every parameter off its grid edge, then highest train hit, read against the
marginals. One stage of 1,296 configs raced Reversion/Breakout x velocity_mode {Decelerating, Accelerating, Any} x velocity_lookback {1, 3, 5} x donchian_length {10..100} x donchian_confirm {1..4} x trend filter {off, Against, With EMA200}; a second pass (~25) extended the lookback to {1, 2, 3, 5, 8} and tried the Donchian neighbourhood, the ATR band, the trend filter and a weekend gate on the frozen family.

Reversion is the family and Breakout its mirror (pooled train 56.71% vs 43.28%). The velocity gate is the finding, and it is the 5m one again: fading an extreme reached while ACCELERATING scores 57.2-57.4% on the unloaded years against 55.2% with the gate off and 52.9% for Decelerating — the textbook exhaustion reading is the loser, on 15m as on 5m. The lookback is a plateau (2, 3, 5 and 8 all score 57.2-57.4% unloaded; 1 scores 55.4%); the rule landed on 3 only because it was the sole interior value of the first grid, and its holdout (52.6% on 287 bets) is the weakest on the plateau with the most uneven halves (64.1 / 56.5), so the preset takes 5 — the plateau centre, the most even halves in the sweep (59.7 / 58.8) and the 5m Balanced's own value. Donchian 20 with two confirming bars is the train optimum on both marginals (length 20: 57.29% train; confirm 3-4 lose the holdout). The Against-Trend filter both 5m tiers carry does nothing here (54.8% unloaded on the same family against 55.2% without it); the ATR band is inert.

| preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017-08 → 2026-03 | worst yr |
|--------|--------:|-------:|------:|--------:|---------------------------:|---------:|
| PM 15m Balanced | 873 | 58.19% | 59.27% | 56.15% | **57.18%** (13,413 bets, z +16.6) | 54.14% (2022) |

Per year on the full record, none of it fitted except the last six months:

| year | hit (bets) | | year | hit (bets) | | year | hit (bets) |
|---|---:|---|---|---:|---|---|---:|
| 2017 | 51.38% (650) | | 2021 | 59.35% (1,663) | | 2025 | 54.88% (2,068) |
| 2018 | 59.13% (1,248) | | 2022 | 54.14% (1,448) | | 2026 | 58.77% (1,215) |
| 2019 | 59.66% (1,294) | | 2023 | 57.11% (1,413) | |  |  |
| 2020 | 59.90% (1,469) | | 2024 | 56.49% (1,818) | |  |  |

Train halves 59.71 / 58.84%, about 4.7 bets a day; the 18 months right
before the window score 56.30% on 3,032 bets; whole record 14,286
bets at 57.24% (z +17.3). After the pick was frozen: the prefix test
passes with 0 mismatches at three cuts; bets run 49% long and both
sides win (window 59.07% / 57.34%, unloaded 58.75% / 55.91%); the mirror scores 41.81% in the window and 42.75% unloaded.

**Where it fails.** Worst month 2026-08 at 53.1% on 143 bets; worst
full year 2022 at 54.14%. This is the thinnest of the video's ten on 15m, ~5 bets a day, and its window months range 53-62%. The edge decays: 2018-2021 run 59-60%, 2022-2025 54-57%.

**Not shipped.** donchian_confirm 1 for volume: 2,206 window bets at 56.12% with train 55.98 / holdout 56.39 and 56.29% unloaded (z +23.3) — a Volume tier in all but name, and the better choice if bets matter more than the last point of hit rate. velocity_lookback 3 (the rule's row): 817 bets at 57.41%, 57.31% unloaded, holdout 52.61%. Weekend-only: 218 bets at 61.47% (train 65.49, holdout 53.95). With Trend EMA200: 181 at 61.33%. PM 5m Volume carried over as-is (Donchian 100, confirm 3, Against Trend) is 117 window bets at 47.01% — the 5m geometry does not transfer.

### Fair Value Gap

Fitted on the **latest six months** of BTCUSDT 15m (2026-03-13 → 2026-09-13),
the protocol of the [Reversal](#15-minute-preset), Oscillators and Gann 15m
presets: train 03-13 → 07-13, holdout 07-13 → 09-13 scored once after the pick
was frozen, and the 8.5 years before the window **never loaded** by any sweep
stage — scored once at the end (4,939 bets there against 482 in the
window). Rule fixed before tuning: train bets ≥ 300, both halves above 52%,
every parameter off its grid edge, then highest train hit, read against the
marginals. One stage of 960 configs raced Continuation/Reversion x min_gap_atr_mult {0..1.5} x entry_depth {0..1} x max_gap_age_bars {24..200} x reaction filter on/off x trend filter {off, With EMA200}; a second pass (~15) tried the impulse body, a non-zero reaction size and the ATR band on the frozen pick.

Continuation is the family (pooled train 51.71% vs 47.15% for Reversion — a gap retest that holds goes on with the impulse, as on 5m). Within it the gap size is the lever: bigger gaps are better on both windows (0: 51.29% train / 50.59% holdout; 0.75: 53.77 / 55.63; 1.0: 55.36 / 58.18) at a steep cost in bets; the entry depth peaks at the gap's midpoint (0.5: 53.10% train — ICT's 'consequent encroachment' level, and the only depth that beats 52% on train); the gap's age and the impulse body are inert; the trend filter loses 1.5pp. The reaction filter is a no-op at reaction_min_atr = 0 (its rows are byte-identical to 'off') and at 0.25 ATR it destroys the unloaded years (52.58%, 2026 at 40.6%), which is the 5m verdict on confirmation filters again. The preset takes the rule's row: a gap of at least 0.75 ATR, entered at its midpoint within 48 bars (12 h).

| preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017-08 → 2026-03 | worst yr |
|--------|--------:|-------:|------:|--------:|---------------------------:|---------:|
| PM 15m Balanced | 482 | 57.05% | 56.48% | 58.01% | **54.16%** (4,939 bets, z +5.8) | 50.76% (2022) |

Per year on the full record, none of it fitted except the last six months:

| year | hit (bets) | | year | hit (bets) | | year | hit (bets) |
|---|---:|---|---|---:|---|---|---:|
| 2017 | 57.63% (177) | | 2021 | 53.12% (544) | | 2025 | 53.94% (799) |
| 2018 | 53.17% (489) | | 2022 | 50.76% (593) | | 2026 | 55.17% (638) |
| 2019 | 59.43% (387) | | 2023 | 56.85% (635) | |  |  |
| 2020 | 55.30% (472) | | 2024 | 52.84% (687) | |  |  |

Train halves 57.89 / 55.36%, about 2.6 bets a day; the 18 months right
before the window score 53.04% on 1,186 bets; whole record 5,421
bets at 54.42% (z +6.5). After the pick was frozen: the prefix test
passes with 0 mismatches at three cuts; bets run 54% long and both
sides win (window 60.23% / 53.36%, unloaded 53.88% / 54.46%); the mirror scores 42.95% in the window and 45.76% unloaded.

**Where it fails.** Worst month 2026-05 at 41.9% on 74 bets; worst
full year 2022 at 50.76%. THE WEAKEST 15m PRESET IN THE REPO out of sample: 54.16% on 4,939 unloaded bets (z +5.8), with 2022 at 50.8% and 2021, 2024 and 2025 at 52-54%, against 57% in a fitted window that itself had a 41.9% month (2026-05). The window's 57% is the regime; ~2.7 bets a day. Read it as the 5m note reads its own preset — a stability-checked pick, not a settled edge.

**Not shipped.** gap 0.5 (the volume dial): 952 window bets at 55.67%, 53.79% unloaded. gap 1.0: 249 at 57.03%, 54.29% unloaded. The 5m preset carried over as-is (gap 1.25, depth 0.75, age 200): 155 window bets at 55.48%, 54.02% unloaded with 2021 at 48.4%. Nothing in this family clears 55% on the unloaded years.

### Trend Lines

Fitted on the **latest six months** of BTCUSDT 15m (2026-03-13 → 2026-09-13),
the protocol of the [Reversal](#15-minute-preset), Oscillators and Gann 15m
presets: train 03-13 → 07-13, holdout 07-13 → 09-13 scored once after the pick
was frozen, and the 8.5 years before the window **never loaded** by any sweep
stage — scored once at the end (10,292 bets there against 705 in the
window). Rule fixed before tuning: train bets ≥ 300, both halves above 52%,
every parameter off its grid edge, then highest train hit, read against the
marginals. One stage of 864 configs raced Against/With Signal x trigger {break, bounce} x pivot_left {3..45} x pivot_right {1, 2} x require_direction on/off x max_slope_atr {0.1, 0.5, 2.0} x break_buffer_atr {0, 0.3, 0.8}; a second pass (~12) tried the buffer neighbourhood, the pivot gap and line age, the ATR band and one side at a time on the frozen pick.

Fade the break, as on 5m and 1m: pooled train 51.09% against 48.89% for taking it, and bounces are a coin flip either way (49.99 / 49.96). The 5m finding that flat and counter-sloping lines earn as much as trend-following ones holds (require_direction off 54.61% vs on 53.93% train; the slope cap is inert from 0.1 to 2.0 ATR/bar). The rule's row is a 6-bar (90 min) pivot with a 0.8-ATR buffer: 705 window bets at 57.45% and 56.54% on 10,292 unloaded bets — and 6 bars is where Reversal, CHoCH and Gann's flat-level alternative all landed on 15m, the wall-clock scale again. The buffer is the one dial that matters here (0.5: 54.08% window / 55.62% unloaded; 0.8: 57.45 / 56.54; 1.2: 58.93 / 56.32) and 0.8 is the knee. The long pivots the marginal prefers on the window (20-45 bars, 57-63% holdout) do not survive the unloaded years (20/2: 55.65%, 30/2: 54.40%) — a 2026 artefact, as it was for Gann. Pivot gap, line age and the ATR band are inert. Resistance-line breaks (60.23%) carry more than support-line breaks (54.82%) in the window; both sides are kept.

| preset | 6m bets | 6m hit | train | HOLDOUT | unloaded 2017-08 → 2026-03 | worst yr |
|--------|--------:|-------:|------:|--------:|---------------------------:|---------:|
| PM 15m Line Break | 705 | 57.45% | 58.87% | 54.73% | **56.54%** (10,292 bets, z +13.3) | 53.35% (2022) |

Per year on the full record, none of it fitted except the last six months:

| year | hit (bets) | | year | hit (bets) | | year | hit (bets) |
|---|---:|---|---|---:|---|---|---:|
| 2017 | 51.75% (342) | | 2021 | 55.34% (1,160) | | 2025 | 57.51% (1,332) |
| 2018 | 56.32% (1,147) | | 2022 | 53.35% (1,194) | | 2026 | 57.70% (955) |
| 2019 | 58.51% (1,140) | | 2023 | 55.31% (1,291) | |  |  |
| 2020 | 60.41% (1,114) | | 2024 | 56.81% (1,322) | |  |  |

Train halves 59.29 / 58.47%, about 3.8 bets a day; the 18 months right
before the window score 56.84% on 1,974 bets; whole record 10,997
bets at 56.60% (z +13.8). After the pick was frozen: the prefix test
passes with 0 mismatches at three cuts; bets run 51% long and both
sides win (window 54.82% / 60.23%, unloaded 56.72% / 56.35%); the mirror scores 42.55% in the window and 43.38% unloaded.

**Where it fails.** Worst month 2026-07 at 50.0% on 124 bets; worst
full year 2022 at 53.35%. A modest edge: 56.5% unloaded with 2022 at 53.4% and 2021 / 2023 at 55.3%; 2026-07 ran at 50.0% on 124 bets. As the 5m file notes, this and Gann's flat level are largely the same trade; on 15m Gann's fan preset is the stronger of the two.

**Not shipped.** Buffer 1.2: 504 window bets at 58.93% (holdout 59.78%), 56.32% unloaded. Buffer 0.5: 871 at 54.08%. Pivot 20/1 with no buffer: 544 at 57.17%, 56.15% unloaded. PM 5m Line Break Volume carried over as-is: 414 at 56.52%, 56.09% unloaded; Balanced as-is: 329 at 55.93%, 55.95% unloaded with 2025 at 52.3%.

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
