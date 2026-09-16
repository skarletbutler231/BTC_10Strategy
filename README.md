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
Patterns**, **Momentum Indicators**, **CHoCH**, **Elliott Wave**, **Renko**,
**Trend Lines**, **Support & Resistance**, **Gann Angles** and **Break of
Structure** — classic chart-analysis tools built on the same framework and held
to the same evidence bar, along with **Moon Phase**, kept as a documented null.
This also includes **Candlesticks** as a formula-based addition.

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

In dashboard dropdown order. Every one ships Polymarket-tuned 5m presets, and
`PM 15m` presets fitted with a holdout for the 15-minute market (see
[Polymarket 15m presets](#polymarket-15m-presets)); the linked sections
document how each was fitted and what it is worth.

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
| + | [Break of Structure](#break-of-structure-beyond-the-video) | BOS and CHoCH as separate signals — fade the structural break |
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
market.db updater (`stream` = Chainlink + Polymarket, `twap` = Chainlink TWAPs,
`pm15m` = the 15-minute market capture, `binance`/`binance1m`/`binance1s` =
Binance candles, `pmdata`/`pmdata15m` = Polymarket L2 order book, `all` = every
job except `pmdata15m`). Each job takes its own `flock` lock, so the fast and slow
jobs run at their own cadences without ever colliding:

```cron
* * * * *    <proj>/run_updaters.sh stream    >> <proj>/data/ingest_stream.log 2>&1
* * * * *    <proj>/run_updaters.sh twap      >> <proj>/data/twap_ingest.log 2>&1
* * * * *    <proj>/run_updaters.sh pm15m     >> <proj>/data/pm15m_ingest.log 2>&1
* * * * *    <proj>/run_updaters.sh binance1s >> <proj>/data/binance_1s.log 2>&1
* * * * *    <proj>/run_updaters.sh binance1m >> <proj>/data/binance_1m_tail.log 2>&1
*/30 * * * * <proj>/run_updaters.sh binance   >> <proj>/data/binance_ingest.log 2>&1
40 1 * * *   <proj>/run_updaters.sh pmdata    >> <proj>/data/pmdata_ingest.log 2>&1
50 1 * * *   <proj>/run_updaters.sh pmdata15m >> <proj>/data/pmdata15m_ingest.log 2>&1
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
- **PMData charges quota per archive download** — 144 units for a 5m day, 48 for
  15m, 12 for 1h — and a repeat download is charged again; only a byte range
  starting after byte 0 (a resume) is free. (The 5m backfill ran under PMData's
  older *day unlocked* billing; the API reference changed by 2026-09.) Either way
  the archives are never re-fetched: rebuilding the tables costs nothing, but
  re-downloading a day you deleted costs quota. On HTTP 429 (quota used up) a
  run stops at once instead of asking for every remaining day, and `--dry-run`
  prints what a run would download and its quota cost without downloading.

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

### 15-minute markets (`--series btc-15m`)

PMData publishes the **btc-15m** series as well: **every day from 2026-01-26 to
2026-09-12 (230 days, 31.5 GB, no gaps)**, ~110–200 MB a day — probed with
zero-quota range requests on 2026-09-13. `ingest_pmdata` folds it with the same
code into its own tables, because every L2 key is the window `start_ts` alone and
a 15m window shares it with the 5m window that opens at the same moment:

| 5m table | 15m table |
|---|---|
| `pm_l2_market` | `pm_l2_market_15m` |
| `pm_l2_quote` | `pm_l2_quote_15m` |
| `pm_l2_book` | `pm_l2_book_15m` |

```bash
python3 -m backend.data.ingest_pmdata --series btc-15m --dry-run   # days + quota, no download
python3 -m backend.data.ingest_pmdata --series btc-15m             # 230 days = 11,040 units
python3 -m backend.data.ingest_pmdata --series btc-15m --status
```

Read it with the same `pm_store` helpers plus `series="15m"` —
`l2_quote_at(start_ts, elapsed, series="15m")`, `l2_fill(..., series="15m")`.

**Loaded as of 2026-09-13: one day.** The full backfill was started and PMData
returned *"Download quota has been used up"* after the first archive
(2026-01-26, 48 units), so the account's quota needs to reset or be raised before
the other 229 days (10,992 units) can be fetched. Re-running the command above
resumes from 2026-01-27 without re-spending the day already on disk. That first
day checks out against the 5m data's own invariants: 64 markets (recording began
08:00 UTC, as the 5m series' first day did), 100,617 second-rows, zero
`bid<=0` / `ask>=1` / crossed rows, and 63 of 64 outcomes derived from the
settled book (`'terminal'` — the feed's `market_resolved` events only begin
~2026-03-28, the same as 5m). One difference from 5m: **15m markets trade before
their window opens** — rows start up to ~15 minutes before `start_ts`, so
`time - start_ts` can be negative.

The 15m markets settle on the **Chainlink BTC/USD 60s TWAP** (Gamma
`cryptoMarketConfig`: `btc-15m-twap-60`), as the 5m markets have since 2026-08-14.

## The 15-minute market capture (`ingest_pm15m`)

Independently of PMData, pmqb runs a standalone 15m recorder next to its trading
platform — `research/capture/capturePM15m.ts`, pm2 app `pmqb-pm15m-capture` —
writing `pmqb/data/pm15m_l2.jsonl`. It never touches `stream.jsonl` or trading
code. Two record types:

- `pm15m_l2` — the YES and NO books (top 20 levels each) plus the Binance BTC mid,
  every ~2 s, **since 2026-07-03 05:30 UTC**.
- `pm15m_outcome` — one per window once Polymarket has resolved it: the outcome,
  the strike (`priceToBeat`) and the settle price (`finalPrice`), read back from
  Gamma. **Added 2026-09-13**: before that the capture recorded books only. Gamma
  publishes the strike only after the close, so the outcome record is where it is
  captured. The recorder re-checks the last hour of windows when it starts, so a
  restart does not lose outcomes.

`ingest_pm15m` folds the file into two tables, as a resumable byte-cursor tail
like `ingest_stream` (only complete lines are consumed, so a line still being
written is picked up next run):

- **`pm_window_15m`** — one row per market: `start_price` (strike), `end_price`
  (settle), `resolved_up`, `resolved_src='gamma'`.
- **`pm_quote_15m`** — per (window, second): YES `yes`/`yes_bid`/`yes_ask`, size at
  the best, total depth over the captured levels, and `btc`. YES side only — the
  book is symmetric (`no_bid = 1 - yes_ask`), so NO adds nothing.

```bash
python3 -m backend.data.ingest_pm15m                    # backfill / append (per-minute cron: pm15m)
python3 -m backend.data.ingest_pm15m --backfill-gamma   # resolve closed windows with no outcome
python3 -m backend.data.ingest_pm15m --status
```

Windows captured before outcome records existed are resolved by
`--backfill-gamma`, which asks Gamma for each closed window without an outcome
and records Polymarket's own answer (never a price comparison of our own). Run it
again after any capture outage. It writes in short bursts so it never holds
`market.db` locked against the per-minute ingest jobs.

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
5. **Times** (UTC / Local) is the basis for every time on the page — the chart
   axis, the crosshair, the trade table, and the Start/End dates. UTC matches
   Binance's candle grid and the backend; Local is this browser's timezone, and
   the note beside the dates shows which (`UTC+09`). Candle timestamps are never
   shifted — only labels change, so DST stays correct — and in Local mode the
   Start/End days are sent as their real epoch boundaries, so `09/10` means the
   local 10th, not the UTC one. The choice is remembered in the browser.

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
- **Polymarket up/down** — models a Polymarket-style **5- or 15-minute binary
  market** (set the interval to match; the presets are named for theirs).
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

Neither mode stakes anything but a flat $1. For a staking scheme priced against
the real book — re-bet the same direction after a loss, sized so the win repays
the chain — see [Recovery-sized martingale](#recovery-sized-martingale-pm_martingale).

## Polymarket 15m presets

Polymarket runs a **15-minute** BTC up/down market next to the 5-minute one.
Every strategy now ships three `PM 15m` presets — **Volume**, **Balanced**,
**Selective** — fitted on 15m bars, with the fitter checked in as
`backend/data/pm_preset_sweep.py` so the run is reproducible:

```bash
python3 -m backend.data.pm_preset_sweep --interval 15m --all --workers 30   # ~70 min on 16 cores
python3 -m backend.data.pm_preset_sweep --interval 15m --report              # re-score the picks
```

It is a multi-process sweep (`fork`-pool, one config per task, the candle list
shared copy-on-write) that scores every configuration by calling the
strategy's own `generate_signals` in the same next-candle mode the dashboard
uses — there is no private re-implementation, so what it measures is what the
**Polymarket up/down** mode runs. 133,016 configurations across 23 strategies,
grids in the file.

### Method

The one the CHoCH presets were fitted with, applied to every strategy:

- **Train** 2024-09-13 → 2025-12-13 (15 months, 43,776 bars): selection
  happened here and only here.
- **Holdout** 2025-12-13 → 2026-09-13 (9 months, 26,374 bars): scored once,
  after the picks were frozen.
- **Unswept** 2017-08 → 2024-09: never loaded by the sweep; reported as a
  second, larger out-of-sample check.
- Tiers are **bands of train bets** — Volume ≥ 1,200, Balanced 500–1,199,
  Selective 200–499 — so the three picks are always distinct configs.
  Admission needs both halves of train ≥ 52% and train z ≥ 2.5; within a band
  the pick is the highest train hit rate less one standard error.
- Lookbacks count bars, so every grid carried both the 5m bar counts and
  their wall-clock thirds (a 5m 12/24/48 is a 15m 4/8/16). The winners split:
  RSI + BB, CCI Williams and CHoCH kept the bar counts; Multi Horizon, Stoch
  Wick and Reversal moved to the wall-clock scale.

The yardstick is 50%, not 48.4%: 15m candles close flat only 0.05% of the
time over the window (3.1% on 1m) and up 49.9%.

### What it found

Pooled over all 68 presets the holdout is **57.47%** on 36,347 bets. 18 presets
*improve* into the holdout, 17 hold within 2pp, 18 shrink 4pp or more — and the
shrinkage is concentrated in the Selective tiers, exactly the 5m pattern. The
table is the recommended tier per strategy (the tier the preset notes call the
pick; ✗ marks the three strategies with none), sorted by holdout:

| Strategy | Pick | Bets 2017–26 | Hit | z | Unswept 17–24 | Train | HOLDOUT | Holdout bets |
|---|---|--:|--:|--:|--:|--:|--:|--:|
| Volume Exhaustion | PM 15m Volume | 17,201 | 56.35% | +16.6 | 56.11% | 55.33% | **59.72%** | 1,775 |
| RSI + BB | PM 15m Volume | 9,420 | 60.14% | +19.7 | 60.08% | 60.73% | **59.69%** | 779 |
| Candlesticks | PM 15m Volume | 8,545 | 59.68% | +17.9 | 60.36% | 57.60% | **59.61%** | 926 |
| Elliott Wave | PM 15m Balanced | 4,797 | 56.87% | +9.5 | 56.35% | 58.00% | **59.50%** | 437 |
| Reversal | PM 15m Volume | 6,283 | 56.69% | +10.6 | 56.17% | 57.46% | **59.46%** | 523 |
| Renko | PM 15m Volume | 8,298 | 58.72% | +15.9 | 59.02% | 57.19% | **59.43%** | 811 |
| CCI Williams | PM 15m Volume | 8,611 | 60.78% | +20.0 | 61.49% | 58.92% | **59.33%** | 804 |
| Regime Switch | PM 15m Balanced | 5,536 | 60.17% | +15.1 | 59.98% | 61.49% | **59.23%** | 569 |
| Multi Horizon | PM 15m Volume | 10,782 | 59.62% | +20.0 | 59.75% | 59.10% | **58.99%** | 790 |
| Momentum Indicators | PM 15m Volume | 16,742 | 59.66% | **+25.0** | 60.09% | 58.32% | **58.72%** | 1,565 |
| Zscore MS | PM 15m Volume | 10,195 | 58.95% | +18.1 | 59.12% | 58.26% | **58.54%** | 849 |
| Gann Angles | PM 15m Volume | 12,819 | 56.83% | +15.5 | 56.67% | 56.93% | **57.96%** | 1,168 |
| CHoCH | PM 15m Volume | 7,970 | 57.47% | +13.3 | 57.31% | 57.98% | **57.82%** | 780 |
| ATR DevExh | PM 15m Balanced | 4,689 | 56.13% | +8.4 | 55.46% | 58.05% | **57.76%** | 419 |
| Stoch Wick | PM 15m Volume | 4,878 | 58.86% | +12.4 | 59.41% | 58.05% | **57.61%** | 552 |
| Jump Exhaustion | PM 15m Volume | 20,627 | 56.51% | +18.7 | 56.41% | 56.57% | **57.27%** | 1,774 |
| BB Squeeze | PM 15m Volume | 9,820 | 59.60% | +19.0 | 59.82% | 59.87% | **57.24%** | 849 |
| Oscillators | PM 15m Volume | 8,993 | 59.41% | +17.9 | 59.60% | 59.87% | **57.09%** | 811 |
| Fib Retracement | PM 15m Balanced | 3,608 | 56.43% | +7.7 | 55.65% | 60.04% | **56.89%** | 283 |
| Support & Resistance | PM 15m Volume | 21,683 | 56.09% | +17.9 | 55.97% | 56.62% | **56.18%** | 1,935 |
| Fair Value Gap ✗ | PM 15m Selective | 1,333 | 55.36% | +3.9 | 53.96% | 59.72% | 58.28% | 151 |
| Harmonic Patterns ✗ | PM 15m Volume | 9,163 | 57.78% | +14.9 | 58.20% | 57.45% | 54.62% | 811 |
| Trend Lines ✗ | PM 15m Balanced | 3,440 | 56.60% | +7.7 | 56.69% | 57.83% | 53.57% | 308 |

Pooled over the twenty picks the holdout is **58.30%** on 18,399 bets. Each
strategy file carries the full three-tier table with per-half train, worst
calendar year and the notes on what won and why; the rest of this section is
what carries across the whole board.

**Every edge is still a fade.** Not one tier in 23 strategies follows the
move. Structure breaks are traded backwards (Reversal, CHoCH, Support &
Resistance, Gann, Trend Lines all pick *Against Signal* / *Continuation* / *Against
Structure*), band and oscillator extremes are faded, the marubozu is faded
after an extension, the Renko brick is faded, Wave 5 is faded. The 5m verdict
holds one timeframe up.

**The same nulls.** Gann's angles do not earn — both admitted tiers set
`unit_atr_mult = 0.002` with only the 1×1 on, which is a flat level, and fade
its break. RSI + BB's rejection-wick and recovery-close filters are zero in
every tier. BB Squeeze's Volume and Balanced tiers do not require a squeeze.
Only the band entry earns in Oscillators (Zone Entry, faded, in all three).

**Three do not survive on 15m.** *Trend Lines* posts 52–54% on the holdout in
every tier and 50.3% in Selective; *Harmonic* keeps 54.6% only in Volume and
collapses to 51% in the other two; *Fair Value Gap* is real (z +9.6 over the
record) but thin — 52.7% on 1,506 holdout bets — with only its 151-bet
Selective tier above 58%. Their presets ship with a NOT RECOMMENDED note so the
failure is on record next to the 5m ones.

**Renko is path-dependent.** Its brick ladder is built from the first loaded
bar, so the exact bet count moves by a few with the loaded start date; the
preset notes quote both the sweep-window and the whole-record runs. Every other
strategy reproduces its sweep numbers to the bet through
`polymarket.run_binary_backtest`.

**Caveats.** The holdout is nine months of one regime, and 2026 has been a
kind tape for reversion — Volume Exhaustion's *improvement* into the holdout
(55.3% → 59.7%) is that tape, not a better preset. The unswept-years column is
the longer check, and it agrees with the holdout to within a point or two for
every pick above. Hit rate is the finding; the EV at 0.50 odds assumes a 0.50
fill, which a real book will not offer at the strike — see
[Capacity: the edge is ~2 cents wide](#capacity-the-edge-is-2-cents-wide) for
what the 5m ladder actually fills at. Real 15m quotes now exist —
`pm_quote_15m` from 2026-07-03 (see
[the 15-minute market capture](#the-15-minute-market-capture-ingest_pm15m)) — and
RSI + BB's Volume pick has been priced against them: 56.59% real hit at a 0.537
fill, which the taker fee turns into +1.1pp of edge. See
[The 15-minute market: RSI + BB *PM 15m Volume*](#the-15-minute-market-rsi--bb-pm-15m-volume).
The other presets are still unpriced. Days and bars are UTC; a bar is stamped by
its open time.

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


## Break of Structure (beyond the video)

*Read price as swing structure and fade the break of it.* A trend is up while
making higher highs, down while making lower lows, and two events settle the
argument:

* **BOS** (Break of Structure) — price closes through the structural level in the
  **same** direction as the prevailing trend. Continuation.
* **CHoCH** (Change of Character) — price closes through it **against** the
  trend. The first mechanical sign the trend has turned.

Both break the same kind of level; only the trend at the time separates them,
which is exactly why they are worth separating. One confirmed swing high and one
swing low are live at a time, a break retires its level so it fires once, and a
newer confirmed pivot replaces it.

**What this adds over Reversal.** `reversal.py` already ships fitted BOS presets,
but its detector is a single condition — last two lows descending, then a close
above the most recent swing high. It cannot express BOS vs CHoCH as separate
switches (its rule fires on what is really a CHoCH), displacement, retest entry,
or a liquidity-sweep precondition. All four are parameters here.

### What the sweep found

Read against the window's own **flat ceiling** of 49.88%, not 50%. Protocol as
elsewhere: `TRAIN 2024-07-30 → 2025-10-01` for selection only, `HOLDOUT` scored
once after freezing, `UNSWEPT 2018-01-01 → 2024-07-30` never consulted.

**Fade the break — emphatically.** Every faded config in Stage A beat every
face-value one (best fade +5.11pp, best face-value −2.73pp). That is the fourth
independent time this repo has landed there, alongside Reversal, Trend Lines and
Support & Resistance.

**Retest entry costs ~2pp** (Break Close 54.97% vs Retest 52.73%). Waiting for
price to return to the broken level is standard SMC advice and it is worth
negative money here.

**Displacement earns nothing.** Its marginal is flat from 0.0 to 1.5 ATR
(56.33% → 56.73%, inside noise). Requiring the breaking candle to be impulsive
does not separate the breaks that revert from the ones that run. Off in every
preset. `max_level_age_bars` is likewise flat to 0.13pp.

Stage B (5,040 + 144 configs) was read off marginals rather than the argmax, and
grids whose optimum hit a boundary were extended — after which the apparent peaks
at buffer 2.0 (+1.13pp) and displacement 2.0 (+1.47pp) turned out to sit on 745
and 514 mean bets, i.e. noise.

| Preset | 2yr bets | 2yr hit | edge | train | HOLDOUT | unswept | z |
|---|---|---|---|---|---|---|---|
| PM 5m Volume | 7,076 | 55.64% | +5.76pp | 55.68% | 55.58% | 59.00% | +9.7 |
| **PM 5m Balanced** | 5,323 | 56.38% | +6.50pp | 56.70% | 55.89% | 58.94% | +9.5 |
| **PM 5m Selective** | 3,640 | 56.29% | +6.41pp | 56.05% | 56.66% | 58.95% | +7.7 |
| *PM 5m Sweep* | 1,393 | 57.07% | +7.19pp | 58.52% | 54.72% | 57.20% | +5.4 |

### How much is already in the repo — measured, not assumed

Reversal ships BOS presets, and Trend Lines, Gann and Support & Resistance all end
up fading the break of a mechanically located level. Comparing `(bar, side)`
signal sets over the two years:

| Preset | shared with ≥1 existing | unique | unique hit | shared hit |
|---|---|---|---|---|
| PM 5m Volume | 86.8% | 13.2% | **52.19%** | 56.16% |
| PM 5m Balanced | 71.3% | 28.7% | 55.74% | 56.62% |
| PM 5m Selective | 56.5% | **43.5%** | 55.56% | 56.84% |
| PM 5m Sweep | 82.2% | 17.8% | 55.24% | 57.47% |

The largest single overlap is with **Gann's `PM 5m Volume` at 60.5% Jaccard** —
unsurprising once you know Gann's fitted optimum collapsed to a horizontal pivot
level, which is very nearly what a structural level is. Overlap with *Reversal's*
own BOS presets is much lower (10–23%), because its detector needs two descending
lows and fires on a CHoCH.

**Read the table this way.** Selectivity buys independence: as the buffer rises
the unique share goes 13% → 29% → 44% while the unique bets keep scoring
55.5–55.7%. ⚠️ **`PM 5m Volume` is 87% duplicate and its unique 13% is weak
(52.19%)** — the tier least worth running alongside the others. *Balanced* and
*Selective* carry genuinely new signal at full strength; **prefer *Selective* as a
Combined voter.**

⚠️ **2017 breaks every tier** — 44.50–46.37% against that year's 46.50% ceiling.
A parabolic run in which broken structure kept going: the standard failure mode of
a fade, and the same year that breaks Reversal and Harmonic. Every full year
2018–2026 clears comfortably.

⚠️ **The edge decays** — all tiers score ~3pp higher across the never-swept
2018–2024 than over the last two years (Volume 59.00% vs 55.64%). The two-year
number is the live estimate.

⚠️ ***Sweep* is the most speculative** — 1,393 bets on a 530-bet holdout, and
58.52% train → 54.72% holdout is the only material post-selection decay here. It
requires the level to be wicked and rejected before the break counts, which is the
one variant Reversal cannot express.

Look-ahead is verified by truncation on all four presets: cutting the series at any
signal bar reproduces that signal exactly.


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

## Polymarket backtesting page (`/pm-backtest`)

A page of its own, because the main dashboard's *Polymarket up/down* mode is a
model, not a record: it assumes a flat user-chosen entry price and resolves each
bet on the **Binance candle's direction**. This page uses neither.

```
./run.sh    ->    http://localhost:$PORT/pm-backtest
```

**Both halves of every bet come from the market.** The entry price is the
executable book — YES at the ask for an UP bet, NO at `1 - yes_bid` for a DOWN
bet — read at a fixed offset into the window, the same offset every time so
there is no hindsight. The outcome is the market's **own Chainlink-settled
resolution**, never the Binance candle, which only lands on the same side of the
strike ~85% of the time. Windows come from both captures merged
(`pm_l2_*` + `pm_window`/`pm_quote`), with the exchange feed's own outcome
preferred over our Chainlink capture over the L2 terminal-book derivation.

| Panel | What it controls |
|---|---|
| **Market** (top bar) | **5m** or **15m** BTC up/down. Each market has its own price record, its own fitted playbooks and its own fee, so switching it swaps the strategy cards, the date range and the fee defaults. |
| **Overlapping signals** | The mode switch: what happens when several books signal the same window. **Every book trades** (mode 1) — each takes its own chain, so correlated signals stack and capital adds. **One trade per window** (mode 2) — the books share one account holding at most one position per window; the first book in the list takes a contested window and the rest yield. See [below](#one-book-per-strategy-not-one-setting-for-all). |
| **Select Strategies** | One card per strategy: enable it, pick a **playbook**, or unfold the card to override its preset, ladder depth, chain target and filters by hand. Only strategies with a playbook measured on the selected market are listed. In mode 2 the list order is the priority order. |
| **Execution** | Entry offset (s), Executable/Mid pricing, **fee model** and rate, max entry price, and **Walk the ladder** — these describe the *account*, so they apply to every book. |

**Fee model.** *Winnings take* is a flat cut of a winning share's profit
(`breakeven(p) = p + fee·(1−p)`). *Taker* is Polymarket's crypto-market fee,
charged on every **buy** as `shares × fee × p × (1−p)` — about 1.7c a share at
55c, and a losing rung loses it too; makers pay nothing. The 15m market defaults
to taker at its documented 0.07, the 5m page keeps its 0% winnings default. The
per-rung **Edge** column is hit rate minus the fee-inclusive breakeven.

### The 15m market

`Market → 15m` reads pmqb's 15-minute capture (`pm_window_15m` / `pm_quote_15m`,
2s book snapshots from 2026-07-03, outcomes from Gamma) and steps rungs 900s
apart. Resting size exists for every 15m window, so *book leaning your way*
works throughout; the full ladder does not (`pm_l2_book_15m` holds one PMData
day, before the record), so *Walk the ladder* is top-of-book on 15m until the
btc-15m backfill has quota. Three RSI + BB playbooks are wired up — see
[The 15-minute market: RSI + BB *PM 15m Volume*](#the-15-minute-market-rsi--bb-pm-15m-volume)
for how they were measured:

| Playbook | Preset | Depth | Filter | Measured (real prices, taker 7%, $1 target, 2026-07-03 .. 09-13) |
|---|---|---:|---|---|
| **15m · Fitted · D1 · push3** | PM 15m Volume | 1 | hard push (`push3 ≥ 0.3`) | +17.56 on 111 chains, 62.16% hit, maxDD −13.62 |
| 15m · Unfiltered · D3 | PM 15m Volume | 3 | — | +49.04 on 157 chains, maxDD −17.79, 8x the capital; loses to D1 per dollar of bankroll on every multi-year candle period |
| 15m · Unfiltered · D1 | PM 15m Volume | 1 | — | +4.75 on 205 chains, 56.59% — what push3 is worth |

### One book per strategy, not one setting for all

The fitted answer differs per strategy — RSI + BB and Stoch Wick both want a
retry inside the US session, CCI Williams, Candlesticks and Reversal each want
a flat bet in an overlapping but differently-bounded window (16-01, 16-02,
14-00), Volume Exhaustion wants a flat bet all day, Jump Exhaustion wants a flat
bet *overnight*, and Harmonic Patterns wants a **three-rung ladder** in a
four-hour window — so
forcing one depth and one filter on all of them would misrepresent every
strategy but the one it was fitted to. Each selected strategy therefore runs as
its **own book**, on its own depth, target and entry filters, and the books are
aggregated afterwards. They are *not* merged into one signal stream: two
strategies whose fitted depths differ cannot share a chain, and their chains may
legitimately overlap in time.

Aggregation follows from that:

* **Capital adds.** ``peak_chain_exposure`` is the SUM of each book's own peak —
  the worst case where every book is deepest at once — not the max.
* **Drawdown is recomputed** on the merged, time-ordered equity curve, because
  two books' bad weeks need not coincide. Over 2026-03-04 .. 2026-09-04 the
  eight fitted books draw down **−76.57** together against −23.07 / −17.80 /
  −21.59 / −17.02 / −11.67 / −12.65 / −10.80 / −22.43 apart — far under their
  −137.03 sum, so the diversification is real, but it is worth measuring rather
  than assuming: five of the eight trade an overlapping afternoon session, so
  they concentrate rather than diversify. Adding Harmonic Patterns is the one
  case that barely moved the joint drawdown at all (−75.34 to −76.57) despite
  its own −22.43, because depth 3 wins 93% of its chains and loses in different
  weeks from the flat books.
* **Per-rung stays per book** — ladders of different depths must not be averaged
  into one row, so that table stacks the books under their own headers.

That is the page's **Every book trades** mode (`mode: "independent"`). The
**One trade per window** switch (`mode: "one_per_window"`) keeps the books but
shares one account between them: at most one position is open in any window,
so a signal that lands on a window some book is already in — as a fresh chain
or as a later rung — is not traded, and when several books fire on the same
free window the first in the list takes it (a book that disagrees on the side
simply loses the window). Filters run first, so a signal a book filters out
does not consume the window for the rest. Each book's stats then separate the
two reasons a signal was not traded — `signals_skipped_in_chain` (its own chain
was still running) and `signals_yielded` (another book held the window) — and
`peak_chain_exposure` is the deepest single chain rather than the sum of book
peaks, because the chains can no longer overlap. The engine call is
`pmm.run_books(books, market, one_per_window=...)`; `pmm.run` is its one-book
case.

Measured on the three fitted 5m books RSI + BB, Stoch Wick and Volume
Exhaustion over 2026-06-01 .. 2026-09-04: mode 1 plays 1,357 chains for
+217.63 at maxDD −35.63 on 22.07 peak capital; mode 2 yields 142 of those
signals and plays 1,221 for +203.05 at maxDD −21.62 on 8.52 — 7% less P&L for
39% of the capital and 61% of the drawdown, which is what not stacking
correlated signals is worth.

### Playbooks

A playbook is one *measured* configuration: signal preset + ladder depth + entry
filters, with the numbers it earned. They live in `PM_STRATEGIES` in
`backend/main.py`, and the page renders them from `/api/pm_backtest/schema`.

| Strategy | Playbook | Preset | Depth | Filter | Measured |
|---|---|---|---:|---|---|
| RSI + BB | **Fitted · D2 · 16-01** | PM 5m Volume - 2yr Train | 2 | 16-01 UTC | +167.53, maxDD −23.07 |
| | Flat · D1 · 16-01 | PM 5m Volume - 2yr Train | 1 | 16-01 UTC | +95.16, 26.8x peak capital |
| | Unfiltered · D2 | PM 5m Volume - 2yr Train | 2 | — | +142.34, maxDD −42.17 |
| | Volume preset · D2 · 16-01 | PM 5m Volume | 2 | 16-01 UTC | +133.30 |
| Stoch Wick | **Fitted · D2 · 16-01** | PM 5m Volume | 2 | 16-01 UTC | +145.83, maxDD −17.80 |
| | Flat · D1 · 16-01 | PM 5m Volume | 1 | 16-01 UTC | +92.06, maxDD −13.86 |
| | Unfiltered · D2 | PM 5m Volume | 2 | — | +50.64 — what the filter is worth |
| Volume Exhaustion | **Fitted · D1 · no filter** | PM 5m Selective | 1 | — | +134.17, 21.9x peak capital |
| | D2 · no filter | PM 5m Selective | 2 | — | +139.90 for 2.3x the capital |
| | D2 · 16-01 | PM 5m Selective | 2 | 16-01 UTC | +116.15 on a third of the chains |
| Jump Exhaustion | **Fitted · D1 · 17-06** | PM 5m Volume - 2yr Train | 1 | 17-06 UTC | +124.55, maxDD −17.02, 31.1x peak capital |
| | D3 · 17-06 | PM 5m Volume - 2yr Train | 3 | 17-06 UTC | +263.96 but maxDD −50.30 |
| | Unfiltered · D1 | PM 5m Volume - 2yr Train | 1 | — | +127.48 on paper; +0.0939/chain H1 vs +0.0167 H2 |
| CCI Williams | **Fitted · D1 · 16-01** | PM 5m Selective | 1 | 16-01 UTC | +117.13, maxDD −11.67, 31.1x peak capital |
| | D2 · 16-01 | PM 5m Selective | 2 | 16-01 UTC | +160.13, but rung 2 is 69.1% H1 / 50.9% H2 |
| | Unfiltered · D1 | PM 5m Selective | 1 | — | +51.74 on paper; −$48,177 once the ladder is priced |
| Candlesticks | **Fitted · D1 · 16-02** | PM 5m Balanced | 1 | 16-02 UTC | +79.95, maxDD −12.65, 22.6x peak capital |
| | D2 · 16-02 | PM 5m Balanced | 2 | 16-02 UTC | +89.62 for 2.9x the drawdown |
| | Unfiltered · D1 | PM 5m Balanced | 1 | — | +83.85, maxDD −38.04; +0.0241/chain in H2 vs +0.1485 |
| Reversal | **Fitted · D1 · 14-00** | PM 5m BOS Balanced | 1 | 14-00 UTC | +96.57, maxDD −10.80, 33.9x peak capital |
| | D2 · 14-00 | PM 5m BOS Balanced | 2 | 14-00 UTC | +125.01; rung 2 improves out of sample, D1 still wins priced |
| | Unfiltered · D2 | PM 5m BOS Balanced | 2 | — | +164.87, most raw P&L here, at 13.2x peak capital |
| Harmonic Patterns | **Fitted · D3 · 14-17** | PM 5m Volume | **3** | 14-17 UTC | +168.39, maxDD −22.43, 10.6x peak capital |
| | D2 · 14-17 | PM 5m Volume | 2 | 14-17 UTC | +68.49 — rung 3 is this strategy's best rung |
| | Unfiltered · D3 | PM 5m Volume | 3 | — | +181.50 on paper; −$227,257 once the ladder is priced |

All at a $1 chain target, fee 0, entry +5s, over 2026-03-04 .. 2026-09-04. Bold
is each strategy's default. Running the eight defaults together: **5,606 chains,
+1,034.12, maxDD −76.57, 66.2% hit rate, 25/27 green weeks.**

Harmonic Patterns is the only book here that defaults to a depth **greater than
1**, and the only one whose edge sits in the ladder rather than in rung 1. It is
also by far the most capital-hungry: its peak chain exposure is $15.89 against
$2.85-$4.00 for the flat books, so it alone accounts for more than a quarter of
the portfolio's $55.94.

Note that Jump Exhaustion's session is the *opposite* of the other two that have
one — it wants the overnight 17-06, they want 16-01 — which is exactly why the
page keeps a filter per book rather than one filter for all.

Results: stat cards, an aggregate equity curve, a **per-period table** (week or
month), the per-rung table **per book**, and the individual chains with the fill
price of each rung.

### Adding a strategy or a filter

Both are one edit in `backend/main.py`, and the page picks them up from
`/api/pm_backtest/schema` with no frontend change:

```python
PM_STRATEGIES = [
    {"id": "rsi_bb", "default": "Fitted · D2 · 16-01",
     "default_15m": "15m · Fitted · D1 · push3", "playbooks": [
        {"name": "Fitted · D2 · 16-01", "preset": "PM 5m Volume - 2yr Train",
         "max_depth": 2, "filters": {"hours": "16-01"}, "note": "..."},
        {"name": "15m · Fitted · D1 · push3", "preset": "PM 15m Volume",
         "market": "15m", "max_depth": 1, "filters": {"hard_push": True}, "note": "..."},
    ]},
]
PM_FILTERS = [
    {"key": "hours", "label": "Hours (UTC)", "kind": "text", "default": "",
     "applies_to": "*", "help": "..."},
]
```

A filter's `key` must be handled in `pm_backtest()` (`hours` and
`skip_after_bust` are), and `applies_to` is a list of strategy ids or `"*"`.
A playbook's `market` is the window it was measured on (`"5m"` when absent) and
the page only offers it under that market; `default_15m` names the 15m default
the way `default` names the 5m one. A playbook naming a preset the strategy does
not have is dropped rather than erroring, so renaming a preset degrades
gracefully.

**Eight strategies are wired up so far** — RSI + BB, Stoch Wick, Volume
Exhaustion, Jump Exhaustion, CCI Williams, Candlesticks, Reversal and Harmonic
Patterns; the rest get added one at a time as each is checked against the real
record. Being measured is not the same as being listed: Zscore MS has a
documented result below and is deliberately *not* on the page.

**Cost note.** Assembling the market dict for a 6-month range takes a few
seconds and the ladders longer, so both are memoised in `backend/main.py` by
exactly what they depend on (range + entry offset; candidate windows + offset)
with capped caches. A first run is ~4-5s, repeats ~1s.

## Recovery-sized martingale (`pm_martingale`)

*If a signal loses, re-bet the same direction in the very next 5-minute window,
sized so that the win repays the chain.* Implemented in
`backend/pm_martingale.py`, driven by `backend/data/pm_martingale_backtest.py`.

```bash
python3 -m backend.data.pm_martingale_backtest --from 2026-03-04 --to 2026-09-04
python3 -m backend.data.pm_martingale_backtest --depth 2 --fee 0.02 --target 50
python3 -m backend.data.pm_martingale_backtest --use-book --to 2026-07-27 --target 50
python3 -m backend.data.pm_martingale_backtest --selftest     # ladder arithmetic
```

Each 5m window is a **separate** market that re-opens near 50/50, so unlike a
martingale on a single position the ladder never has to buy an ever-worsening
price. What it does have to buy is an ever-larger *size*, and that is where it
dies — see capacity below.

### The sizing, and why it isn't doubling

Doubling ignores what a share actually pays. Each rung instead solves for the
share count whose win repays everything already lost plus the chain's target:

```
breakeven(p) = p + fee * (1 - p)         # the hit rate a fill at p needs
profit/share = 1 - breakeven(p) = (1 - p) * (1 - fee)
shares_k     = (prior_loss + target) / (1 - breakeven(p_k))
cost_k       = shares_k * p_k
```

`prior_loss` is the cash sunk in rungs 1..k-1, all of it lost. By construction a
chain that wins at *any* rung banks exactly `target`, so depth changes only how
often a chain closes green and how much a red one costs — which is the whole
question. Rung 1 has `prior_loss = 0`, so the base stake floats with the price
and the profit is what stays constant.

Entries are the **real** book at a fixed `+5s` into the window (YES ask for UP,
`1 - yes_bid` for DOWN — no hindsight, same offset every time), and outcomes are
the market's own Chainlink resolution, never the Binance candle's direction.
With `--use-book` each rung is sized against the **resting ladder** from
`pm_l2_book` by fixed-point iteration, because size and fill price are mutually
dependent: a bigger stake eats deeper, which raises the price, which demands
more shares to recover the same loss.

### Fitted depth: **2**

RSI + BB *PM 5m Volume*, 2026-03-04 → 2026-09-04 (the last 6 months), 1,266
signals against 52,423 resolved + quoted windows, `target` $1, fee 0, one chain
at a time (signals firing mid-chain are skipped):

| depth | chains | chain win | bets | bet hit | PnL | peak chain exposure | max DD | PnL/maxDD |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1,247 | 55.81% | 1,247 | 55.81% | **+53.06** | 4.00 | −25.60 | 2.07 |
| **2** | 941 | 80.23% | 1,357 | 55.64% | **+89.85** | 12.33 | −41.62 | **2.16** |
| 3 | 867 | 91.00% | 1,410 | 55.96% | **+96.74** | 27.06 | −74.77 | 1.29 |
| 4 | 844 | 94.67% | 1,441 | 55.45% | −126.69 | 52.77 | −233.50 | −0.54 |
| 5 | 836 | 97.01% | 1,464 | 55.40% | −257.58 | 124.05 | −471.47 | −0.55 |
| 6 | 831 | 98.44% | 1,474 | 55.50% | −27.33 | 266.93 | −378.36 | −0.07 |

The per-rung table is the mechanism. Each row conditions on every earlier rung
having lost, and `breakeven` is just the price paid:

| rung | bets | hit | avg fill | breakeven | edge | avg cost |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 831 | 55.84% | 0.5362 | 53.62% | **+2.22pp** | 1.23 |
| 2 | 362 | **57.46%** | 0.5438 | 54.38% | **+3.08pp** | 2.74 |
| 3 | 154 | 55.19% | 0.5573 | 55.73% | −0.54pp | 6.16 |
| 4 | 69 | 42.03% | 0.5423 | 54.23% | −12.20pp | 13.21 |
| 5 | 39 | 51.28% | 0.5528 | 55.28% | −4.00pp | 31.75 |
| 6 | 19 | 63.16% | 0.5563 | 55.63% | +7.53pp | 80.15 |

**Rung 2 is the only retry that earns.** It hits *better* than the signal itself
— which is the same fact the Multi Horizon section reports from the other
direction ([Why *not* to skip windows after a loss](#why-not-to-skip-windows-after-a-loss)):
a loss means the stretch grew, so the next bet is stronger, and the run only
ends when a bet finally wins. From rung 3 on, the fill price has crept up
(0.5362 → 0.5573) while the hit rate has not, and the edge is gone. Rungs 5-6
look positive again on 39 and 19 bets — that is noise, and the depth-6 PnL of
−27 versus depth-5's −258 is the same noise seen from the P&L side.

Depth 3 books the highest raw PnL, and it is **not** the pick:

- **It is not a real improvement over 2.** In a paired weekly block bootstrap
  (4,000 resamples), depth 2 beats depth 1 in **83.5%** of them; depth 3 beats
  depth 2 in **55.2%** — a coin flip. Depth 2 also has the best 5th percentile
  (−5.52 vs depth 3's −28.44) and the highest P(profit), 93.9% vs 89.3%.
- **It dies first under fees.** At a 2% or 4% winnings fee, depth 2 is the top
  depth outright; depth 3 falls behind, then negative.
- **It needs 2.2x the capital** ($27.06 vs $12.33 peak exposure per $1 of
  target) and 1.8x the drawdown for 8% more PnL.

Depth ≥4 is not a milder version of the same thing — it is a different bet. It
wins 95-99% of its chains and still loses money, because the rare bust now costs
40-1,000x the target. Its sign flips on the entry offset alone (depth 5 is −258
at +5s, +46 at +30s), which is the signature of a result carried by three or
four chains.

### Capacity: the edge is ~2 cents wide

The whole edge is 2.2pp of hit rate, i.e. ~2c of price. Walking the real ladder
for the shares each rung needs, mean slippage against the top-of-book price:

| target | rung 1 | rung 2 | rung 3 | rung 4 | rung 5 |
|---:|---:|---:|---:|---:|---:|
| $1 | 0.00c | 0.00c | 0.04c | 0.17c | 0.17c |
| $10 | 0.07c | 0.11c | 0.34c | 0.65c | 1.28c |
| $100 | 0.62c | 1.07c | 2.67c | 4.70c | 10.68c |
| $1,000 | 4.94c | 9.78c | 17.82c | 27.41c | 33.37c |

Rung 3 at a $100 target slips 2.67c — *more than the entire edge*. Sized in
`--use-book` mode (ladder span 2026-03-04 → 2026-07-27), PnL per $1 of target:

| target | fee | D=1 | D=2 | D=3 | D=4 |
|---:|---:|---:|---:|---:|---:|
| $10 | 0% | 38.48 | **72.93** | 68.78 | −173.84 |
| $10 | 2% | 27.67 | **57.57** | 47.09 | −215.50 |
| $50 | 0% | 30.96 | **55.48** | 27.86 | −155.34 |
| $50 | 2% | 19.86 | **39.10** | 3.67 | −217.01 |
| $100 | 2% | 11.27 | **12.41** | −26.42 | −134.54 |
| $250 | 0% | **4.22** | −1.66 | −57.36 | −91.02 |

Depth 2 is best in every cell until size kills the whole scheme somewhere
between a $100 and $250 chain target — at which point flat betting is the only
thing still standing, and not for long.

### Can the losing weeks be filtered out? Not as weeks.

Nine of the 27 weeks are red, and they look like they ought to be predictable.
They are not: they are indistinguishable from chance. Shuffling the 941 chain
P&Ls across the same week sizes produces **9.9** red weeks on average against
the 9 observed (p=0.82), and a weekly-P&L spread of 11.03 against the observed
11.50 (p=0.37). The chi-square of weekly bust counts is 36.4 on 26 dof
(dispersion 1.40, p≈0.09). There is no week-level structure to find, so no
week-level rule can find one — and a week is only knowable as red once it is
over anyway.

The tradeable question is whether **individual bets** can be filtered, which
shrinks the red weeks as a by-product. Ten features observable at entry were
scanned — entry price, spread, best-ask size, 5c depth imbalance, RSI, %B,
ATR%, hour, weekday, and whether the previous chain busted — each cut at its
**H1 median**, then judged against a permutation null (a random filter keeping
the same number of chains, 20,000 draws) and finally on an untouched H2:

| filter (fitted on H1) | H2 chains | H2 PnL | vs random subset | H2 $/chain |
|---|---:|---:|---:|---:|
| *unfiltered* | 449 | +31.47 | — | +0.0701 |
| **hour 13-23 UTC** | 194 | **+48.23** | p=0.043 | **+0.2486** |
| skip after a bust | 390 | +35.83 | p=0.048 | +0.0919 |
| RSI high | 225 | +43.30 | p=0.086 | +0.1925 |
| everything else (7) | — | — | p=0.26-0.87 | — |

Only the hour survives. *Skip after a bust* clears the permutation null on H1
but collapses on H2 (+0.0919 vs a +0.0701 baseline) and actually **raises** the
red-week count there, 6 → 7 — the same verdict the Multi Horizon section reaches
at bar level in [Why *not* to skip windows after a loss](#why-not-to-skip-windows-after-a-loss).
Entry price, spread, book depth, %B, ATR%, weekday and ask size are all noise.

### The one filter that survives: trade the US session

The effect is in the **signal**, not the ladder. At depth 1 — a plain flat bet,
no martingale at all — the fill price barely moves between sessions but the hit
rate does:

| hours (UTC) | bets | hit | avg fill | edge |
|---|---:|---:|---:|---:|
| 00-12 | 681 | 54.19% | 0.5385 | **+0.33pp** |
| 13-23 | 566 | **57.77%** | 0.5409 | **+3.69pp** |
| all | 1,247 | 55.81% | 0.5396 | +1.85pp |

**Essentially the entire edge is earned between 13:00 and 23:59 UTC.** The
Asian and early-European sessions price the band fade correctly; the US session
does not. The same cut lifts P&L per chain for **every** preset at **both**
depths (8 of 8: +0.014 to +0.200), and the 16:00-19:59 and 20:00-23:59 blocks
are the two that agree in sign across H1 and H2, so this is not one preset's
accident.

Applied to the depth-2 ladder over the full six months:

| | unfiltered | hours 13-23 |
|---|---:|---:|
| chains | 941 | 430 |
| PnL (target $1) | +89.85 | **+102.76** |
| per chain | +0.0955 | **+0.2390** |
| ROI on cash staked | +3.89% | **+10.44%** |
| max drawdown | −41.62 | **−18.45** |
| green weeks | 18/27 (67%) | **21/27 (78%)** |
| worst week | −20.85 | **−5.30** |
| worst chain | −9.74 | **−5.59** |

More money on 54% fewer bets, less than half the drawdown, and no red week worse
than −5.30. It does not eliminate losing weeks — six remain, and they cannot be
eliminated, because they are chance. It makes them shallow.

```bash
python3 -m backend.data.pm_martingale_backtest --depth 2 --hours 13-23 --by week
```

Caveat on the filter specifically: the hour was one of ten features scanned, so
some selection remains even though the cut itself was H1's median rather than a
hand-picked boundary. What raises it above the other nine is that it reproduces
out of sample, across presets, and at depth 1 — i.e. it is a property of when
the strategy is right, not of the staking scheme.

### Stoch Wick: the filter matters more than the ladder

Same protocol, `stoch_wick` / *PM 5m Volume*, 1,605 signals over the same six
months. It lands on the **same answer — depth 2, `--hours 16-01`** — but for a
different reason, and the unfiltered version is not tradeable at all.

Its raw edge is a quarter of RSI+BB's: rung 1 hits **53.87% at a 0.5327 fill**,
`+0.60pp`, which a 2% winnings fee plus ladder slippage eats completely. Priced
against the real book, **unfiltered Stoch Wick loses money at every depth and
every size**: depth 1 is −1.24 per $1 of target at a $1 chain and −18.88 at
$100. The session filter is what makes it a strategy:

| hours (UTC) | rung-1 bets | hit | fill | edge |
|---|---:|---:|---:|---:|
| all | 1,318 | 53.87% | 0.5327 | +0.60pp |
| **16-01** | 577 | **58.23%** | 0.5318 | **+5.05pp** |

Hours **06:00-12:59 UTC are negative in both halves** (6, 7, 8, 10, 11, 12 all
agree, 9 disagrees), and 16, 17, 19, 21, 23, 0 are positive in both. Dropping the
morning is worth ~4.5pp of hit rate — a bigger lift than the same filter gives
RSI+BB, on a strategy that has nothing without it.

At `16-01`, depth 2 is again the pick:

| depth | chains | PnL | max DD | peak capital | PnL/peak | PnL/maxDD | rung-N edge |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 632 | +92.06 | −13.86 | 4.88 | 18.86 | 6.64 | — |
| **2** | 577 | **+145.83** | −17.80 | 7.42 | **19.65** | **8.19** | **+6.58pp** (235 bets) |
| 3 | 559 | +156.62 | −38.10 | 29.46 | 5.32 | 4.11 | −2.16pp (91 bets) |
| 4 | 549 | +265.85 | −51.56 | 38.39 | 6.92 | 5.16 | +11.10pp (43 bets) |
| 5 | 545 | +116.44 | −130.29 | 72.81 | 1.60 | 0.89 | −22.60pp (15 bets) |

**23/27 green weeks (85%)**, the best of anything measured here, worst week
−7.05.

**Depth 4 is a trap worth documenting.** It books nearly twice depth 2's P&L and
beats it in 96% of paired weekly resamples, because rung 4 hits 65-66% — and
that survives a time-shift null decisively (400 random placements of the same
signal pattern average a 51.5% rung-4 hit and −104 P&L; the real run gets 66%
and +385, p<0.0025). It is still not usable:

- **The ladder is not monotone.** Rung 3 *loses* (−2.16pp) and rung 4 makes it
  back. A real conditional edge does not skip a rung.
- **It is the outlier of nine.** Rung-4 edge across the nine PM-preset
  strategies runs −12.2 to +12.4pp with a mean near +0.6pp on 30-140 bets each;
  Stoch Wick is simply the maximum of that spread. RSI+BB's rung 4 is −12.2pp.
- **The market-wide effect is far too small to explain it.** Across all 52,440
  windows, P(revert) after k consecutive same-direction windows is 50.5-51.5%
  (k=3: 51.46%, z=+3.26) — real, but nowhere near the ~54% breakeven, let alone 66%.
- **43 bets** at `16-01`.

So the honest reading is that rung 4 is a genuine feature *of this sample* and
not of the market. Depth 2's rung 2 (+6.58pp on 235 bets, monotone with rung 1's
+5.05pp) is the one to trade.

```bash
python3 -m backend.data.pm_martingale_backtest --strategy stoch_wick \
    --preset "PM 5m Volume" --depth 2 --hours 16-01 --by week
```

### Zscore MS: the first strategy where the ladder does *not* pay

Same protocol, `zscore_ms` / *PM 5m Volume*, 1,381 signals. The answer is
**depth 1 — no martingale — with `--hours 13-18`**, and the reason is worth more
than the number.

Unfiltered, this strategy has essentially nothing at rung 1: `+0.44pp`, and it
does not replicate (H1 `+1.49pp`, H2 `-0.94pp`). Everything it earns unfiltered
comes from rung 2 (`+2.97 / +4.61pp`, agreeing across halves on 204/234 bets),
with rung 3 negative in both halves. Priced against the real ladder at a 2% fee,
the unfiltered strategy tops out at **+$1,040** at any depth or size.

The hour structure is real but sits **earlier** than the other two strategies.
Ranking all 1,371 (depth x contiguous-session) combos by whether they are
positive in *both* halves, the share of survivors containing each hour is a
smooth single-peaked curve — 23-26% at 04:00-07:00 UTC rising to 63-66% at
15:00-19:00. That smoothness is what a real session effect looks like; a spiky
profile would not be.

Inside `13-18`, **the signal itself is strong enough that the retry has little
left to capture**:

| session | depth | chains | PnL | max DD | PnL/maxDD | per-rung edge |
|---|---:|---:|---:|---:|---:|---|
| none | 2 | 1,025 | +54.01 | −59.12 | 0.91 | r1 +0.0 (1025), r2 +2.6 (471) |
| **13-18** | **1** | **302** | **+46.49** | **−10.09** | **4.61** | **r1 +7.5pp (302)** |
| 13-18 | 2 | 242 | +54.38 | −14.54 | 3.74 | r1 +7.9 (242), r2 +2.4 (90) |
| 13-18 | 3 | 230 | +83.56 | −20.90 | 4.00 | r1 +8.1, r2 +2.4, r3 +12.3 (36) |
| 15-01 | 3 | 426 | +184.97 | −32.68 | 5.66 | r1 +1.8, r2 +6.6, r3 +10.6 (74) |

Rung 1 at `13-18` hits **60.26% at a 0.5278 fill** — the strongest single-rung
edge of the three strategies documented here — and rung 2 adds only `+2.4pp` on
top. `13-18` is not a knife edge: every neighbouring session (12-18, 13-17,
13-19, 13-20, 14-18, 14-19, 15-19, 12-19, 13-21) is positive at both depths with
rung-1 edges of +2.6 to +9.2pp.

The decision is made by **matched capital**, priced against the real ladder at a
2% fee:

| combo | best target | PnL | peak capital | PnL per $ capital | PnL/maxDD |
|---|---:|---:|---:|---:|---:|
| **13-18 D=1** | $600 | **+17,696** | 2,360 | **7.50** | **2.21** |
| 13-18 D=2 | $250 | +7,459 | 2,437 | 3.06 | 1.62 |
| 15-01 D=3 | $400 | +21,822 | 8,555 | 2.55 | 1.11 |
| none D=2 | $50 | +1,040 | 490 | 2.12 | 0.39 |

At essentially the **same capital** ($2,360 vs $2,437), flat betting makes
**2.4x** what depth 2 makes. Depth 3 at `15-01` books the most money in absolute
terms — and wins a paired weekly bootstrap against `13-18 D=1` in 99.9% of
resamples — but needs 3.6x the capital and gives back nearly its whole profit in
its worst drawdown (PnL/maxDD 1.11).

**The general lesson across the three strategies:** the ladder is a *substitute*
for signal quality, not a complement. Where a filter finds hours in which the
signal is genuinely good (Zscore MS at 13-18, rung 1 `+7.5pp`), the retry adds
almost nothing and costs capital. Where the signal is mediocre (Stoch Wick
unfiltered `+0.60pp`, Zscore MS at 15-01 `+1.8pp`), rungs 2-3 are what earn.

```bash
python3 -m backend.data.pm_martingale_backtest --strategy zscore_ms \
    --preset "PM 5m Volume" --depth 1 --hours 13-18 --by week
```

### Volume Exhaustion: a strategy that needs neither a ladder nor a filter

`volume_exhaustion` / *PM 5m Selective*, 1,539 signals, same six months. The
answer is **depth 1 and no entry filter** — and it is the best raw signal of the
four measured here, which is exactly why nothing bolted onto it helps.

| depth | chains | PnL | max DD | peak capital | PnL/peak | per-rung edge |
|---:|---:|---:|---:|---:|---:|---|
| **1** | 1,518 | **+134.17** | **−21.59** | 6.14 | **21.9** | **r1 +4.4pp (1,518)** |
| 2 | 1,308 | +139.90 | −28.17 | 14.08 | 9.9 | r1 +4.2, r2 **+0.9** (550) |
| 3 | 1,242 | +83.93 | −83.12 | 24.45 | 3.4 | r3 −2.3 (231) |
| 4 | 1,215 | −1.34 | −115.83 | 58.29 | −0.0 | r4 −3.1 (108) |

Rung 2 is worth `+0.9pp` on 550 bets. Depth 2 buys 4% more P&L for 2.3x the
capital and 1.3x the drawdown; priced against the real ladder at a 2% fee it is
worse in absolute terms too — **+$11,601** at depth 1 (peak capital $1,031,
11.26 per $ of capital) against **+$3,624** at depth 2. Depth 3 is negative.

**The filter search, run as widely as the data allows.** 364 candidate
predicates over twelve feature families — `rel_vol`, `vol_rank`, `atr_pct`,
book `spread`, `ask_sz`, `bid_sz`, 5c depth imbalance, the entry fill price,
`climax`, `mode`, every contiguous UTC session of 6-24h, and weekday — plus
skip-after-bust. Each family's best cut was fitted on H1 and then judged on H2
against a permutation null:

| best H1 cut | H1 PnL | H2 chains | H2 PnL | p |
|---|---:|---:|---:|---:|
| *(no filter)* | +87.18 | 743 | **+46.98** | — |
| vol_rank ≤ 99.2 | +68.89 | 593 | +59.73 | 0.036 |
| hour in 09-02 (18h) | +78.42 | 568 | +42.17 | 0.302 |
| bid_sz ≤ 127.75 | +90.91 | 150 | +15.13 | 0.329 |
| atr_pct ≤ 0.19 | +60.37 | 563 | +46.81 | 0.207 |
| climax == down | +63.49 | 348 | +11.34 | 0.755 |
| fill ≥ 0.51 | +69.50 | 478 | +17.80 | 0.803 |
| spread ≤ 0.01 | +86.26 | 417 | +7.20 | 0.893 |
| weekday in Mon-Fri | +45.35 | 588 | +16.54 | 0.955 |
| rel_vol ≥ 3.14 | +70.88 | 531 | +9.36 | 0.961 |
| ask_sz ≤ 333.06 | +77.02 | 269 | −19.94 | 0.994 |
| depth_imb ≥ −0.30 | +58.39 | 366 | −19.03 | 0.998 |

One of twelve clears p<0.05 — which is what twelve tests give you for free, and
Bonferroni puts it at 0.43. It is also **not monotone**: binning `vol_rank`, the
rung-1 edge runs +6.34/+7.39pp (90-96), +4.09/+3.97 (96-98.5), +0.10/+3.10
(98.5-99.2), then +9.85/−2.47 (99.2-99.7, the only band whose halves disagree)
and +2.78/+2.01 (99.7+). The cut earns by slicing exactly at the disagreeing
band, so it is a lucky boundary rather than a mechanism.

**There is no session effect here at all.** Ranking all 1,299 depth x session
combos by whether both halves are positive, hour-inclusion among the survivors
is flat at **72-83% across all 24 hours** — against Zscore MS's 23% → 66% swing.
And 75% of every combo tested is positive in both halves, versus ~25% by chance:
this strategy is broadly robust, which is the same fact as "no filter finds
anything".

**Stacking makes it worse, measurably.** Greedy forward selection on H1 P&L per
chain adds four filters and lifts H1 from +0.1125 to +0.5179 per chain — while
H2 *total* P&L falls from **+46.98 to +8.50** on 51 surviving chains. Re-running
the greedy on H1 *total* P&L with a nested gate (the filter must also help both
halves of H1) picks one filter, `bid_sz ≤ 127.75`, which then fails on H2
(+15.13). The H1-selected filter loses and the H1-rejected one wins: at this
signal-to-noise, selection is close to anti-informative.

Weekly at depth 1: **20/27 green (74%)**, worst week −6.64, max drawdown −21.59.

```bash
python3 -m backend.data.pm_martingale_backtest \
    --strategy volume_exhaustion --preset "PM 5m Selective" --depth 1 --by week
```

### Jump Exhaustion: an overnight strategy, and exactly one filter

`jump_exhaustion` / *PM 5m Volume - 2yr Train*, 2,380 signals. The answer is
**depth 1 with `--hours 17-06`** — and the session it wants is the **opposite**
of the one RSI + BB and Stoch Wick want.

Ranking all 1,299 depth x session combos by whether both halves are positive,
hour-inclusion among the survivors is a clean **inverted bowl**: 83-85% at
22:00-01:00 UTC falling to 41-44% at 12:00-14:00. This strategy fades overshoots,
and overshoots revert in thin overnight tape while US-hours jumps are news-driven
and keep going. Every overnight session tested is positive in both halves.

The filter is doing something specific: **it repairs the second half.**
Unfiltered, the strategy decays badly across the sample — H1 +0.0939 per chain,
H2 **+0.0167**. Inside `17-06` it is +0.1282 / +0.1021, i.e. H2 recovers six-fold
while H1 barely moves.

| session | depth | chains | PnL | max DD | peak capital | PnL/peak | H1 /ch | H2 /ch |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| all | 1 | 2,344 | +127.48 | −30.34 | 4.00 | 31.9 | +0.0939 | +0.0167 |
| all | 2 | 1,810 | +140.57 | −64.57 | 12.33 | 11.4 | +0.1680 | **−0.0101** |
| all | 3 | 1,630 | +335.77 | −62.97 | 27.06 | 12.4 | +0.3572 | +0.0548 |
| **17-06** | **1** | **1,084** | **+124.55** | **−17.02** | 4.00 | **31.1** | **+0.1282** | **+0.1021** |
| 17-06 | 3 | 775 | +263.96 | −50.30 | 20.45 | 12.9 | +0.4412 | +0.2402 |
| 19-04 | 1 | 753 | +99.42 | −16.35 | 4.00 | 24.9 | +0.1243 | +0.1401 |

**Depth stays at 1**, and the ladder is the usual mirage. Rung 2 does *not*
replicate (+6.16pp on H1, **−3.62pp** on H2 — unfiltered depth 2 is outright
negative in the second half); rung 3 does (+7.42 / +4.23) and rungs 5-6 look
superb on 8-30 bets a half. Priced against the real ladder at a 2% fee, depth
wins on paper and loses in practice:

| combo | best target | PnL | peak capital | PnL per $ capital | PnL/maxDD |
|---|---:|---:|---:|---:|---:|
| **17-06 D1** | $400 | **+17,999** | 1,728 | **10.42** | **2.13** |
| 19-04 D1 | $400 | +14,978 | 1,724 | 8.69 | 1.62 |
| 17-06 D3 | $100 | +13,405 | 2,504 | 5.35 | 2.27 |
| all D3 | $100 | +10,293 | 4,023 | 2.56 | 1.00 |
| all D1 | $175 | +5,676 | 850 | 6.68 | 0.65 |
| 19-04 D2 | $175 | +4,232 | 2,951 | 1.43 | 0.52 |

Depth 3 books more than depth 1 at a $1 target and less than half as much once
it has to buy real size — it cannot run past a $100 chain target before the
ladder eats the edge, while depth 1 runs to $400.

**How many filters survive: one.** 367 candidate predicates over ten feature
families (`jump_atr`, `atr_pct`, `rsi`, `close_pos`, book spread, `ask_sz`,
`bid_sz`, depth imbalance, fill price, side) plus every contiguous session and
weekday. Fitted on H1 and judged on H2, none beat the unfiltered baseline. More
tellingly, adding a **second** filter on top of `19-04` makes things worse in
every one of nine families — the session alone scores +51.43 on H2, the best
stacked variant +44.45, the rest +17.65 to +32.77, all with p between 0.275 and
0.853. Greedy stacking on H1 total P&L picks `bid_sz ≤ 353.2`, which then
delivers +4.71 on H2 against the baseline's +20.03.

> **A methodology trap worth naming.** `close_pos >= 0.0` came out of the scan at
> p=0.000 — because `close_pos` is never negative, so the "filter" keeps every
> chain and its permutation null compares a set against itself; the p-value is
> pure floating-point noise in the summation order. Any filter whose kept-count
> equals the unfiltered count is a no-op and must be dropped before scoring, not
> after.

Weekly at depth 1 / `17-06`: **20/27 green (74%)**, worst week −9.58, max
drawdown −17.02, ending at +124.55.

```bash
python3 -m backend.data.pm_martingale_backtest --strategy jump_exhaustion \
    --preset "PM 5m Volume - 2yr Train" --depth 1 --hours 17-06 --by week
```

### CCI Williams: the session filter carries the whole strategy

**Answer: depth 1, `--hours 16-01`, and no other entry filter.** Over
2026-03-04 .. 2026-09-04 on the `PM 5m Selective` preset, 1,536 signals land on a
resolved window.

```bash
python3 -m backend.data.pm_martingale_backtest --strategy cci_williams \
    --preset "PM 5m Selective" --depth 1 --hours 16-01 --by week
```

Unfiltered, **no depth is positive in both halves** — the whole apparent edge
sits before the 2026-06-04 split:

| depth | chains | PnL | maxDD | PnL/$cap | H1 /ch | H2 /ch |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1,536 | +51.31 | −51.53 | 12.0 | +0.0764 | **−0.0061** |
| 2 | 1,254 | +101.38 | −55.55 | 10.4 | +0.2061 | **−0.0351** |
| 3 | 1,169 | −112.82 | −228.88 | −4.2 | +0.0654 | **−0.2485** |

Ranking a session x depth grid by raw per-chain P&L puts *depth 5* on top, with
rows reading `+1.0000` per chain and a max drawdown of −1.70. That is the
martingale illusion in its purest form: a chain that always wins banks exactly
the target, so per-chain P&L converges on the target and says nothing about
edge. The tell is the capital — those rows need $65-126 of peak exposure to
earn $1 a chain (PnL/$cap 1.6-3.2) against depth 1's 19.5. **Median PnL per $ of
peak capital by depth: D1 19.5, D2 13.2, D3 2.5, D4 2.0, D5 1.6.**

**The session effect here is real, and it is the only thing that is.** Across
484 depth-1 sessions the H1->H2 Spearman rank correlation is **+0.762**, 20/20
of the top-20 H1 sessions stay positive in H2, and choosing on H1 alone by
capital efficiency lands on a session worth +0.0916/chain in H2 against the
unfiltered −0.0061. Rotating the session around the clock at fixed width gives a
smooth profile — every rotation from `09-22` to `15-04` positive, every one from
`00-13` to `03-16` negative — so it is a diurnal effect, not one lucky bin
(rank 2/24, p = 0.083).

The boundary itself is **not finely determined**: `14-01`, `15-01`, `16-01` and
`17-01` are statistically indistinguishable on H1 (best worst-quarter +0.3529 to
+0.4002). `16-01` is used because it wins the priced test at every matched
target and matches the session RSI + BB and Stoch Wick already trade.

**367 predicates over 13 families, and none of them survive.** The honest test
is choosing on H1 and reading H2:

| | depth 1 | depth 2 |
|---|---|---|
| chosen by H1 total P&L | −0.0173/ch — **loses** to unfiltered | −0.0560/ch — **loses** |
| top-25 by H1, mean H2 | +0.0289 (**87% shrinkage**) | +0.0563 (**87%**) |
| Spearman H1->H2 | **+0.150** | +0.374 |

Compare the session's +0.762. And the decisive diagnostic: **9 of 13 families
have *both* tails beating the baseline** — `atr_pct <=` and `atr_pct >=`, `fav
<=` and `fav >=`, `imb` in both directions. A family that agrees with itself
carries information; a family where either tail "works" is measuring the act of
subsetting a slightly-negative baseline. Stacked on top of the session,
**0/13 families improve on it**, every one costing between −5.66 and −49.83 of
H2 P&L.

Two constant-feature traps were caught by the no-op guard rather than scored:
`mode == reversion` (the preset only emits reversion signals) and
`wr_edge >= 0` (a distance is never negative). Both keep 100% of chains, so a
permutation null compares a set with itself — the `close_pos >= 0` artifact
again, now guarded for.

**Depth 1, twice over.** Rung 2 does not replicate — 69.1% in H1 against
**50.9% in H2**, a coin flip — and rung 3 runs 33.3% / 46.8%. Priced against the
real ladder with a 2% winnings fee, depth 1 dominates depth 2 at *every* matched
target (PnL/$cap 25.1 vs 18.8 at a $1 target, 22.1 vs 15.0 at $200, 15.5 vs 7.6
at $600), so there is no capital level at which the retry is the better bet.

| 16-01, depth 1 | PnL | peak capital | PnL/$cap | PnL/maxDD |
|---|---:|---:|---:|---:|
| $200 target | +16,815 | 724 | 23.24 | 6.54 |
| $400 target | +30,054 | 1,591 | 18.89 | 5.58 |
| **$600 target** | **+42,540** | **2,494** | **17.06** | **5.05** |
| $800 target | +42,692 | 3,691 | 11.57 | 3.63 |

Unfiltered at the same targets the strategy is not merely worse but *negative*:
−3,290 at $200 and −48,177 at $600. The filter is not an improvement here — it
is the difference between a business and a loss.

At the $1 unit target over the full six months: **560 chains, +117.13, maxDD
−11.67, 31.1x peak capital, 62.7% hit rate, 24/27 green weeks (89%)**, worst
week −4.55.

*(The scan tables above are computed on the 1,536 signals that land on a
resolved window; the engine is also handed the 27 that do not, where it opens a
chain and aborts it. An aborted chain earns nothing but still shifts which later
signals are blocked, which is why the page reports 560 chains and +117.13 where
the scan reports 555 and +114.13. The page's figure is the one to trade on.)*

### Multi Horizon: the one that does not clear the bar

**Answer: depth 1, and no entry filter — including no session filter.** On the
`PM 5m Volume` preset over 2026-03-04 .. 2026-09-04 (1,228 signals on a resolved
window) this strategy does not survive its own holdout, and the honest
recommendation is not to trade it on this preset. The depth question has a clean
answer; the filter question has a *negative* one, and that is the finding.

**Depth 1, unambiguously.** No depth is positive in both halves, and the
capital ranking is the most lopsided of any strategy measured — median PnL per $
of peak capital among both-halves-positive combos: **D1 15.0, D2 3.9, D3 2.2,
D4 1.3, D5 1.0.** Rung 2 goes 59.7% in H1 to **46.4% in H2**. (Depth 7 shows up
"positive in both halves" in the raw scan; depth 8 on the same signals flips to
−0.5604/chain, which is what a 3-chain artifact looks like.)

| depth | chains | PnL | maxDD | PnL/$cap | H1 /ch | H2 /ch |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1,228 | +66.77 | −41.63 | 16.7 | +0.1174 | **−0.0028** |
| 2 | 1,007 | +66.52 | −86.62 | 5.4 | +0.2339 | **−0.0844** |
| 3 | 937 | +31.90 | −124.73 | 1.2 | +0.1904 | **−0.1111** |

**The session filter looks real by every screen except the one that matters.**
The H1->H2 Spearman across 461 depth-1 sessions is **+0.732** — as high as CCI
Williams' +0.762 — and 20/20 of the top-20 H1 sessions stay positive in H2 at
only 40% shrinkage. But the effect size is negligible and the placement is
arbitrary:

* Choosing on H1 sub-blocks alone picks `16-09`, worth **+0.0087/chain** on H2
  (+3.69 across the entire second half) against an unfiltered −0.0028.
* The **time-shift null kills it**: rotating an 18-hour window around the clock,
  `16-09` ranks **12/24, p = 0.500** — dead median. CCI Williams' session ranked
  2/24 with a smooth diurnal profile. Here there is no profile to speak of; hour
  inclusion among both-halves survivors spans only 14.9%-26.8% (CCI Williams:
  5%-31%).
* The sessions that *price* best, `15-01` and `14-01`, rank **148/424** and
  **89/424** on the H1 criterion. Their good numbers are hindsight, not a rule
  anyone could have followed.

**585 predicates over 20 families, none survive**, and this time the failure is
not subtle — the top-25 by H1 average *negative* on H2:

| | depth 1 | depth 2 |
|---|---|---|
| chosen by H1 total P&L | −0.1060/ch — **loses** to unfiltered | −0.1361/ch — **loses** |
| top-25 by H1, mean H2 | −0.0360 (**113% shrinkage**) | −0.0948 (**120%**) |
| Spearman H1->H2 | +0.153 | +0.162 |
| families with BOTH tails beating baseline | 11 of 20 | — |

Priced against the real ladder with a 2% fee, the honestly-selectable
configuration is not tradeable:

| | PnL | peak capital | PnL/$cap | **PnL/maxDD** |
|---|---:|---:|---:|---:|
| `16-09` D1 @ $400 (H1-chosen) | +4,909 | 1,515 | 3.24 | **0.36** |
| unfiltered D1 @ $100 | +1,403 | 436 | 3.22 | **0.28** |
| `15-01` D1 @ $400 (hindsight) | +10,107 | 1,484 | 6.81 | 1.52 |
| *CCI Williams `16-01` D1 @ $600, for scale* | *+42,540* | *2,494* | *17.06* | *5.05* |

A drawdown roughly three times the profit. The month table says why — this is
decay, not a bad filter:

| | Mar | Apr | May | Jun | Jul | Aug |
|---|---:|---:|---:|---:|---:|---:|
| unfiltered /chain | +0.2193 | +0.0939 | +0.0925 | −0.0320 | −0.0883 | +0.0689 |
| `16-09` /chain | +0.1599 | +0.0847 | +0.2148 | +0.1181 | −0.0860 | −0.0018 |

The filter tracks the decay rather than escaping it. **Not added to the page** —
a playbook is a measured configuration worth trading, and this is a measured
configuration worth skipping. Kept here because a negative result found by the
same procedure is what makes the positive ones credible.

### Candlesticks: one pattern, one session, no ladder

**Answer: depth 1, `--hours 16-02`, no other entry filter.** On `PM 5m Balanced`
over 2026-03-04 .. 2026-09-04, 1,070 signals land on a resolved window.

```bash
python3 -m backend.data.pm_martingale_backtest --strategy candlesticks \
    --preset "PM 5m Balanced" --depth 1 --hours 16-02 --by week
```

**Read the preset before scanning it.** The strategy advertises nine pattern
families; on this preset `patterns` is `['pat_marubozu']` on all 1,070 signals
and `mode` is always `fade`. So "which pattern fired" is not a filter here, it is
a constant — and the no-op guard caught 14 such predicates before scoring,
including `has_pat_marubozu == True`, `mode == fade`, `body_ratio <= 1`,
`pattern_dir <= 1`, `pattern_dir >= -1` and `weekday >= 0`.

Depth 1 and depth 2 are both positive in both halves — the first strategy
measured where the unfiltered baseline survives its own holdout — but the ladder
still does not pay:

| depth | chains | PnL | maxDD | PnL/$cap | H1 /ch | H2 /ch |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1,070 | +84.96 | −38.04 | **23.9** | +0.1406 | +0.0241 |
| 2 | 1,020 | +82.14 | −70.82 | 9.0 | +0.1623 | +0.0049 |
| 3 | 988 | −36.93 | −122.19 | −1.4 | +0.0078 | −0.0789 |

Rung 2 goes 60.0% in H1 to **45.1% in H2**, and priced against the real ladder
depth 1 dominates at *every* matched target (PnL/$cap 12.5 vs 5.2 vs 2.7 vs 2.0
at a $1 target; 5.6 vs 2.2 vs 0.5 vs 0.8 at $600).

**The session is the real find, and it is the strongest one measured.** Rotating
the 11-hour window around the clock, `16-02` ranks **1/24, p = 0.042** — the best
of every rotation — and the profile is smooth: every window from `12-22` to
`19-05` positive, every one from `01-11` to `06-16` negative. Choosing on H1
sub-blocks alone, **8/8** of the top sessions beat the unfiltered baseline on H2.
The band `16-02` .. `20-02` is statistically tied on H1; `16-02` is named because
it is the widest of the tied set and therefore the least boundary-fitted.

| | PnL | peak capital | PnL/$cap | PnL/maxDD |
|---|---:|---:|---:|---:|
| $200 target | +9,913 | 912 | **10.86** | **4.95** |
| $400 target | +16,663 | 2,474 | 6.73 | 4.00 |
| $600 target | +19,478 | 2,950 | 6.60 | 2.92 |
| unfiltered D1 @ $150 | +3,509 | 705 | 4.98 | 0.49 |

**466 predicates over 16 families, none survive.** 74% shrinkage, Spearman
H1->H2 of only **+0.079**, and 8 of 16 families have both tails beating the
baseline. Fitting by H1 total P&L or by H1 capital efficiency both *lose* to
unfiltered. Stacked on the session, 1/6 families improve — chance.

At the $1 unit target: **414 chains, +78.95, maxDD −12.65, 22.2x peak capital,
62.1% hit rate, 20/27 green weeks (74%)**, worst week −5.78.

### Reversal: the one that has not decayed

**Answer: depth 1, `--hours 14-00`, no other entry filter.** On
`PM 5m BOS Balanced` over the same range, 1,271 signals land on a resolved
window.

```bash
python3 -m backend.data.pm_martingale_backtest --strategy reversal \
    --preset "PM 5m BOS Balanced" --depth 1 --hours 14-00 --by week
```

This preset pins the detector too: `vote_count`, `required` and
`detectors_enabled` are all constant at 1 and `votes` is a single BOS vote
collinear with the side, so its only strategy-side numeric feature is
`atr_pct`. Everything else scanned is market-side.

**It is the only strategy measured whose second half is better than its first.**
Unfiltered depth 1 runs +0.0401/chain in H1 against **+0.0721 in H2**; every
depth from 1 to 8 is positive in both halves, 80% of session x depth combos
survive the both-halves test (against 24% for CCI Williams), and the top-20
sessions by H1 show **−4% shrinkage** — H2 slightly better than H1. Month by
month inside the session there is no trend to speak of: +0.284, +0.150, +0.193,
+0.129, +0.213, +0.173 from March to August.

**Depth 1 anyway.** Rung 2 here does *not* collapse — it goes 51.1% in H1 to
57.1% in H2, the only strategy where the retry improves out of sample — and yet
depth 1 still wins at every matched capital level, because the ladder's cost
grows faster than its hit rate:

| target | D1 | D2 | D3 | D4 |
|---:|---:|---:|---:|---:|
| $1 | **26.7x** | 9.0x | 2.0x | 1.1x |
| $200 | **21.3x** | 7.7x | 1.3x | 0.8x |
| $800 | **11.4x** | 4.9x | −0.1x | 0.7x |

The session ranks **2/24 (p = 0.083)** in the time-shift null with a smooth
profile, 8/8 H1-chosen sessions beat the baseline on H2, and **0/10 families
improve** when stacked on top of it. The `13-00` .. `15-23` band is tied on H1;
`14-00` sits mid-band with 569 chains.

**316 predicates over 14 families, none survive** — and here the failure is
unusually clean: the Spearman correlation between a predicate's H1 rank and its
H2 rank is **negative** at both depths (−0.153 at D1, −0.240 at D2). Fitting a
filter on the first half actively anti-predicts the second.

| | PnL | peak capital | PnL/$cap | PnL/maxDD |
|---|---:|---:|---:|---:|
| $200 target | +13,922 | 572 | **24.36** | **5.82** |
| $400 target | +24,278 | 1,239 | 19.59 | 4.83 |
| $600 target | +31,741 | 2,018 | 15.73 | 4.00 |
| $800 target | +35,059 | 2,830 | 12.39 | 3.15 |

That is the best capital efficiency of any strategy measured — 15.73 per dollar
at a $600 chain target against CCI Williams' 17.06 at the same target on half
the capital, and 24.36 at $200.

At the $1 unit target: **569 chains, +95.57, maxDD −10.80, 33.5x peak capital,
60.6% hit rate, 19/27 green weeks (70%)**, worst week −4.98.

### Harmonic Patterns: the first strategy that actually wants a ladder

**Answer: depth 3, `--hours 14-17`, no other entry filter.** On `PM 5m Volume`
over 2026-03-04 .. 2026-09-04, 2,501 signals land on a resolved window — the
largest sample of any strategy measured, and the only one whose answer is not
depth 1.

```bash
python3 -m backend.data.pm_martingale_backtest --strategy harmonic \
    --preset "PM 5m Volume" --depth 3 --hours 14-17 --by week
```

**Rung 1 is a coin flip; rungs 2 and 3 are the edge.** Unfiltered, the per-rung
hit rates replicate across the holdout *and improve monotonically*, at an
essentially constant fill price — the recovery mechanism working exactly as the
design intends, and the first clean instance of it since RSI + BB:

| rung | H1 hit @ fill | H2 hit @ fill | bets H1/H2 |
|---|---|---|---|
| 1 | 53.7% @ 0.538 | 52.5% @ 0.526 | 1163 / 1051 |
| 2 | **54.9%** @ 0.534 | **55.5%** @ 0.530 | 523 / 499 |
| 3 | **55.4%** @ 0.537 | **55.9%** @ 0.533 | 233 / 222 |
| 4 | 54.6% @ 0.549 | 44.8% @ 0.526 | 97 / 96 |

Rung 1 sits at breakeven (~53% at a 0.53 fill); rungs 2 and 3 clear it by 1.3-2.6
points in *both* halves; rung 4 disagrees with itself and stops the ladder. A
loss means price has pushed further past the projected completion zone, so the
next bet is a better one — which is why depth 3, not depth 1, is the answer here.

Depth 3 dominates at every matched capital level inside the session:

| target | D1 | D2 | **D3** | D4 | D5 |
|---:|---:|---:|---:|---:|---:|
| $1 | 2.2x | 6.6x | **7.9x** | 3.6x | 1.5x |
| $200 | 0.4x | 4.6x | **6.9x** | 2.2x | 1.0x |
| $600 | −3.0x | 1.4x | **6.6x** | 2.1x | 1.4x |

**The session is not optional — and it only exists at depth 3.** Unfiltered,
priced against the real ladder with a 2% fee, the strategy is destroyed by its
own size: +139 at a $10 target, −703 at $50, **−227,257 at $800**. Inside
`14-17` the same depth 3 makes **+67,423** at a $600 target (peak capital
10,158, PnL/$cap 6.64, PnL/maxDD 4.08); the $200 target is the efficient point
at 6.92x on 3,305 of capital. In the time-shift null `14-17` ranks **1/24,
p = 0.042** at depth 3 — but **10/24 (p = 0.417)** at depth 1 and **12/24
(p = 0.500)** at depth 2. The session and the ladder are not two independent
findings here; the hours only matter once you are running rungs in them.

**637 predicates over 28 families, and the failure is total**: all *six*
H1-fitting criteria lose to unfiltered at both depths, the H1->H2 Spearman
across predicates is **negative** at both (−0.036, −0.063), shrinkage is 87% and
101%, and 15 of 28 families have both tails beating the baseline.

That includes the one filter this strategy seems designed for. Per pattern at
depth 3, only three of seven families are positive in both halves — AB=CD
(+0.1253/+0.0420), Crab (+0.1516/+0.4903), Shark (+0.2480/+0.0640) — while
Gartley, Bat, Cypher and Butterfly are not. But "trade only the Crab" is not a
rule the data supports, because it is not one the data would have *told* you:
fitting the pattern on H1 alone picks **Shark** at depth 2 (H1 +0.1043 -> H2
−0.0582) and **Cypher** at depth 3 (H1 +0.4781 -> H2 −0.1369), losing to
unfiltered by 0.12 and 0.26 per chain. Crab's H1 rank is fourth of seven.

(This was originally rejected on the weaker grounds that the pattern predicates
beat the baseline 7 times in 13 — "chance". That reasoning is sound for a
7-valued field but *not* for a binary one, as the CHoCH section below explains,
so the claim is re-verified here by direct holdout instead.)

**Two traps this strategy sets.** `prz_lo` and `prz_hi` are absolute BTC prices:
a threshold on them ("only above $70k") is a *date* filter over a trending
six-month window, so they are dropped and only the scale-free
`prz_width_pct` is scanned. And `bias` is perfectly collinear with `side_up`
(bullish <-> up on all 2,501 signals), so it is a duplicate family rather than an
independent one. The no-op guard separately caught 10 predicates including
`patterns_n >= 1`, `bars_c_to_d >= 3` and `hour >= 0`.

At the $1 unit target: **338 chains, +168.76, maxDD −22.43, 10.6x peak capital,
93.5% chain win rate, 23/27 green weeks (85%)**, worst week −10.80. The session
is positive in every month but June.

*One caveat on the sizing.* Inside `14-17` the rung-2 hit rate does **not**
replicate (65.0% H1 against 52.2% H2) — that slice carries only 69-80 rung-2
bets per half. The evidence that rungs 2 and 3 earn is the unfiltered table
above, on 499-539 and 222-233 bets; the session is what makes those rungs
affordable, not what makes them work.

### CHoCH: the half the strategy is named after loses money

**Answer: depth 2, filtered to `event == BOS`, and no session filter.** On
`PM 5m Volume` over 2026-03-04 .. 2026-09-04, 1,614 signals land on a resolved
window. This is the only strategy whose surviving filter is *categorical* rather
than a session, and the only one where the two halves of its own signal
definition behave completely differently.

| subset | D | chains | PnL | maxDD | PnL/$cap | H1 /ch | H2 /ch | both+ |
|---|---:|---:|---:|---:|---:|---:|---:|:--|
| all | 1 | 1,614 | +50.81 | −44.55 | 8.3 | +0.0281 | +0.0347 | yes |
| all | 2 | 1,614 | +123.49 | −72.11 | 10.0 | +0.1686 | −0.0126 | no |
| **BOS** | **2** | **986** | **+143.46** | **−49.14** | **11.6** | **+0.2205** | **+0.0738** | **yes** |
| BOS | 1 | 986 | +52.76 | −32.43 | 8.6 | +0.0150 | +0.0904 | yes |
| CHoCH | 1 | 628 | **−1.95** | −34.45 | −0.5 | +0.0485 | −0.0541 | no |
| CHoCH | 2 | 628 | **−19.97** | −70.18 | −2.2 | +0.0883 | −0.1503 | no |
| CHoCH | 3 | 628 | **−81.20** | −158.08 | −3.6 | −0.0570 | −0.2007 | no |

**The CHoCH events lose money at every depth**, and get worse the deeper the
ladder. All of this strategy's P&L comes from the BOS half — the breaks *with*
the prevailing bias, not the changes of character. Its rung 1 collapses from
57.1% in H1 to 50.6% in H2, while BOS's holds at 54.6% / 57.4%.

**But the filter is only selectable at depth 2**, and that is the interesting
part. Fitting on H1 alone:

* at **depth 1** H1 ranks CHoCH (+0.0485) above unfiltered (+0.0281) above BOS
  (+0.0150) — so an honest procedure picks the **wrong half**, and is paid
  −0.0541/chain on H2 instead of the +0.0904 BOS would have earned;
* at **depth 2** H1 ranks BOS top (+0.2205) and it stays top on H2 (+0.0738)
  against an unfiltered −0.0126.

Against a permutation null of random equal-sized subsets, BOS scores p = 0.036 at
depth 1 and p = 0.070 at depth 2. The effect is real at both depths; only at
depth 2 can it be *found* without hindsight.

**The session is the worst of any strategy measured** — Spearman H1->H2 across
503 depth-1 sessions is **−0.381**, shrinkage is 150%, and only 3 of the top 20
H1 sessions stay positive. Both H1-fitting criteria hand back a negative H2.
There is no session filter here to find. Of the remaining 392 predicates over 15
families, none survive either: Spearman −0.013 and −0.067, shrinkage 69% and
109%, 8 of 15 families with both tails beating the baseline.

Priced against the real ladder with a 2% fee, unfiltered depth 1 is dead from a
$50 chain target (−1 at $50, −104,609 at $800). BOS depth 2 is not:

| BOS, depth 2 | PnL | peak capital | PnL/$cap | PnL/maxDD |
|---|---:|---:|---:|---:|
| $50 target | +3,948 | 637 | 6.20 | 1.38 |
| $200 target | +12,748 | 2,715 | 4.70 | 1.02 |
| $400 target | +16,960 | 5,787 | 2.93 | 0.61 |

**This is the weakest tradeable book measured.** PnL/maxDD never exceeds 1.57 at
any target, against 4.95 for Candlesticks, 4.08 for Harmonic and 5.05 for CCI
Williams — the drawdown is comparable to the profit throughout. Depth 3 prices
*better* (+40,917 at $600, PnL/$cap 2.6, and 5.2x at $200 against depth 2's 4.7x)
but is rejected because it is not positive in both halves (H2 −0.0099); that is
what the holdout is for.

A second honest caveat: BOS's two halves disagree about *where* its edge sits.
In H1 it is rung 2 (63.3% against rung 1's 54.6%); in H2 it is rung 1 (57.4%
against rung 2's 51.4%). The depth-2 book is positive in both halves for
different reasons each time, which is weaker evidence than Harmonic's
monotonically replicating ladder.

At the $1 unit target: **986 chains, +143.46, maxDD −49.14, 11.6x peak capital,
81.2% chain win rate, 22/27 green weeks (81%)**, worst week −19.02.

**A scoring bug this strategy exposed.** The "both tails beat the baseline"
family heuristic silently mis-reads *binary* categoricals. For a two-valued
field the four predicates (`== A`, `!= A`, `== B`, `!= B`) are only **two
distinct subsets**, so a perfectly one-directional effect scores 2/4 — which the
heuristic had been reporting as chance. `event` scored 2/4 here while being the
single most informative filter in the whole study. The heuristic is sound for
ordered numeric families, where the two tails really are different subsets; for
categoricals, count distinct subsets, not predicates.

### Searching depth x session together — and what the search is worth

A grid of 1,299 combinations (depths 1-3 x every contiguous UTC session of 6-24
hours, min 150 chains) makes the overfitting risk concrete. Its own top pick is
worthless: the best combo on H1 (`D=3 13-18`, +0.6769/chain) scores **−0.1634**
per chain on H2. The top 20 on H1 average +0.4831 and deliver +0.1019 on H2 —
**79% shrinkage**. Fit the other way round and the top 20 shrink from +0.5150 to
+0.2698. Never read a grid maximum as a result.

What the grid *is* good for is showing where a rule sits in the distribution.
Only **68 of 1,299 combos (5.2%)** beat `D=2 13-23` on **both** halves, against
the ~25% you would expect if the halves were independent — the coarse rule was
already near the 95th percentile of robustness. The 68 survivors cluster tightly
on a **later** session, which is where the one real refinement lives.

Per-hour edge at depth 1 (hit rate minus fill price), the two halves held apart,
is the evidence that does not come from the grid:

| hour UTC | 15 | 16 | 17 | 18 | 23 | 0 | 12 | 14 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| H1 | **−20.3** | +13.4 | +15.4 | +22.3 | +2.9 | +8.4 | +13.6 | +11.0 |
| H2 | **−8.7** | +13.2 | +11.8 | +11.1 | +11.7 | +9.7 | +6.0 | +8.7 |

**Hour 15 UTC is the single most reliably negative hour in the whole day, and
`13-23` contains it.** Hours 19-22 and 1-11 mostly disagree between halves.
Shifting the session to **16:00-01:59 UTC** drops hour 15, keeps every hour that
is positive in both halves except 12 and 14, and stays one contiguous block:

| | unfiltered | `--hours 13-23` | **`--hours 16-01`** |
|---|---:|---:|---:|
| H1 $/chain | +0.1187 | +0.2311 | **+0.2976** |
| H2 $/chain | +0.0701 | +0.2486 | **+0.4446** |
| chains | 941 | 430 | 359 |
| PnL (target $1) | +89.85 | +102.76 | **+133.30** |
| per chain | +0.0955 | +0.2390 | **+0.3713** |
| max drawdown | −41.62 | −18.45 | **−17.65** |
| green weeks | 18/27 | 21/27 | **22/27 (81%)** |
| worst week | −20.85 | −5.30 | −13.26 |
| rung-2 hit | 56.49% @ 0.543 | 61.46% @ 0.537 | **65.13% @ 0.528** |

It beats `13-23` in **94.4%** of paired weekly block resamples (4,000, 2% fee)
with the best 5th percentile of any candidate (+85.50 vs +45.14), and holds
+123.53 even at a 4% winnings fee.

**Depth still stays at 2.** `D=3 16-01` books more on paper (+148.32 vs +133.30)
and is the trap the first depth sweep already identified. Rung 3's own edge is
noise — 17 to 44 bets per half, swinging from −6.4pp to +7.9pp — so depth 3 is
not earning, it is adding variance: worse drawdown (−26.61 vs −17.65), fewer
green weeks (20/27), a worst week of −22.85. And priced against the real ladder
at a 2% fee, the extra size sinks it — best achievable P&L over the ladder span:

| combo | best target | PnL | peak exposure | max DD | PnL/maxDD |
|---|---:|---:|---:|---:|---:|
| D=1 13-23 | $130 | +1,605 | 477 | −3,119 | 0.51 |
| D=2 13-23 | $130 | +6,006 | 2,013 | −3,904 | 1.54 |
| **D=2 16-01** | $220 | **+17,266** | 4,164 | −5,792 | **2.98** |
| D=3 16-01 | $170 | +9,709 | 6,216 | −6,180 | 1.57 |
| D=3 15-01 | $170 | +10,900 | 6,216 | −6,180 | 1.76 |

```bash
python3 -m backend.data.pm_martingale_backtest --depth 2 --hours 16-01 --by week
```

`--hours` is inclusive and wraps midnight, so `16-01` means 16,17,…,23,0,1.

**Caveats.** One 6-month path, ~1,270 signals, one preset; depth was chosen on
that same window, so the *choice* of 2 is in-sample even though the per-rung
mechanism that justifies it is not. Chains that run past the capture are counted
as realised losses (5-6 per run). Fills assume you take the ladder that was
resting at +5s and that your own order does not move it. And the ladder is
`pm_l2_book`, which stops on 2026-07-27, so the `--use-book` rows cover the first
4.8 months of the six.

### The 15-minute market: RSI + BB *PM 15m Volume*

The engine runs the 15-minute market too. `--interval 15m` reads
`pm_window_15m` / `pm_quote_15m` (pmqb's 15m capture: the real +5s book from
2026-07-03, Polymarket's own outcomes via Gamma) and steps rungs 900s apart.
`--fee-model taker` charges Polymarket's crypto taker fee on every **buy**,
`shares × 0.07 × p × (1 − p)` — about 1.7c a share at 55c, most of the edge —
instead of a take on winnings, so a losing rung loses its fee too. `--require`
filters entries on the signal's features.

```bash
python3 -m backend.data.pm_martingale_backtest --interval 15m --preset "PM 15m Volume" \
    --from 2026-07-03 --to 2026-09-13 --depth 1 --fee 0.07 --fee-model taker \
    --require "push3>=0.3" --by week
```

**Answer: depth 1 (no ladder) + `push3 >= 0.3`.** Real prices, 2026-07-03 →
2026-09-13, taker fee, $1 target, H1/H2 split at 2026-08-08:

| | chains | hit | PnL | /chain | max DD | H1 /chain | H2 /chain |
|---|---:|---:|---:|---:|---:|---:|---:|
| D=1 unfiltered | 205 | 56.59% | +4.75 | +0.0232 | −16.59 | +0.1321 | −0.0869 |
| **D=1 `push3 >= 0.3`** | 111 | 62.16% | +17.56 | +0.1582 | −13.62 | +0.2700 | **+0.0444** |
| D=3 unfiltered | 157 | 58.04% | +49.04 | +0.3124 | −17.79 | +0.3963 | +0.2182 |
| D=3 `push3 >= 0.3` | 91 | 62.77% | +35.52 | +0.3903 | −22.38 | +0.7793 | −0.0438 |

Sized against the real top-20 ladder (extracted from `pm15m_l2.jsonl`, since
`pm_quote_15m` keeps only the best level), PnL by chain target:

| target | D=1 unfiltered | **D=1 push3** | peak $ | PnL/maxDD | D=3 unfiltered | D=3 push3 |
|---:|---:|---:|---:|---:|---:|---:|
| $10 | +43 | **+173** | 39 | 1.25 | +484 | +351 |
| $50 | +112 | **+795** | 115 | 1.12 | +2,198 | +1,619 |
| $100 | +69 | **+1,536** | 234 | 1.06 | +2,266 | +3,470 |
| $250 | −828 | **+3,410** | 593 | 0.87 | +5,737 | +5,085 |
| $500 | −4,499 | **+5,231** | 1,195 | 0.63 | +1,394 | +9,884 |

The filter is what makes the flat bet tradeable at all. D=3 books more per
target here, but it needs 3–8x the capital (peak $397 at a $25 target against
D=1's $234 at $100), and at matched capital the flat bet earns about twice as much.

**Ten weeks cannot pick a depth, so the depth comes from 3.9 years of candles.**
The Binance 15m candle agrees with Polymarket's own 15m resolution on **96.2%**
of 6,963 windows (the 5m proxy is ~85%), so next-candle direction is a usable
stand-in for 4,120 signals from 2022-09-13: **A** to 2024-09-13 (never swept),
**B** to 2025-12-13 (the preset's train span), **C** to 2026-07-03. Per-rung hit,
each rung conditional on every earlier one losing:

| | rung 1 | rung 2 | rung 3 | rung 4 |
|---|---:|---:|---:|---:|
| all 4,120 | 60.41% | 59.90% | 62.84% | 60.49% |

**Flat.** Unlike 5m, a loss makes the next 15m bet neither better nor worse, so
every rung has the same edge and the ladder only multiplies the stake. Priced at
the real mean fill (0.537) plus the taker fee, PnL / (peak exposure + |max DD|) —
the bankroll a run needs:

| depth | A | B | C | real window (proxy) |
|---:|---:|---:|---:|---:|
| **1** | **10.22** | **7.81** | **3.91** | 1.12 |
| 2 | 6.41 | 4.86 | 1.46 | 0.85 |
| 3 | 8.31 | 4.92 | 2.32 | **2.54** |
| 4 | 4.71 | 2.00 | 3.22 | 0.99 |

Walked on the median real book (248 shares within 1c, 1,186 within 5c, 3,602 in
the top 20), D=1 leads A, B and C at every target to $500. It still leads with a
2pp haircut on the fill (0.557) and on the 25th-percentile book — in all but one
cell, C at a $1 target and the haircut fill, where D=4 has 2.00 against 1.94.
D=3 wins only the ten real weeks, and D=2 wins nothing. Capacity also points to
D=1: on the median book, rung 4 cannot be filled at a $100 target or rung 3 at
$250, while D=1 still earns at $250 under every stress.

**The candle flatters fades by ~2pp, and it does so one-sidedly.** On the 205
real-window signals the candle says 58.54% and Polymarket 56.59% — 7 candle wins
resolved as losses, 3 the other way. Across every window, when the two disagree
Polymarket sides with the *previous* bar 60.5% of the time (74% after a bar of
0.75–1.0 average ranges). The strike is a Chainlink 60s TWAP, which still carries
the move being faded. That is why the 0.557 fill above is the check that matters.

**`push3` is the only filter that survives, and it was not fitted on this data.**
It is Oscillators' 5m filter unchanged: the 3-bar % move *into* the signal,
oriented to the bet (see `push_pct`). D=1 PnL/chain, unfiltered → push3:
A +0.1131 → +0.1285, B +0.1188 → +0.1565, C +0.1031 → +0.1251, real H1
+0.1321 → +0.2700, real H2 −0.0869 → +0.0444. Permutation null on the real window
p = 0.034, and it still wins every period at the 0.557 fill. Cuts of 0.3 and 0.4
both work; 0.2 is too loose.

**Everything else fails, searched as widely as the data allows** (61 candle,
cross-strategy, 5m and history features, plus 10 book features):

- *1,006 candle predicates over 50 families*, fitted on A+B and judged on C:
  40 reach z ≥ +2 and 37 reach z ≤ −2, 21/40 replicate (chance), Spearman
  A+B → C +0.08. Eight clear z ≥ 1 in all of A, B and C against 3.0 under an
  outcome shuffle (p = 0.087) — suggestive for the family, established for no
  member.
- *780 real-price predicates* including the book (fill, spread, top/5c/20-level
  imbalance oriented to the bet, depth, the −60s → +5s drift, Binance's move to +5s,
  the previous window's resolution), fitted on H1 and judged on H2 at D=1/2/3:
  Spearman +0.016 / +0.071 / +0.033, top-20 shrinkage 128% / 99% / 56%, and
  29 of 51 numeric families beat baseline in *both* tails.
- *The 5m-fitted filters*: `fill >= 0.60` (24 chains; `fill <= 0.55` also beats
  baseline, so the family is noise), `top_imb_sd >= 0`, the sessions 16-01 /
  13-23 / 14-00 / 17-06, weekend-only and skip-after-bust. None holds in both
  halves.
- *Sessions*: a 408-window hour grid anti-predicts at D=1 (Spearman A+B → C
  −0.300).
- *Second filters on top of push3*: 757 subsets — 42 at z ≥ +2 against 43 at
  z ≤ −2, Spearman A+B → C −0.201, and 1 replicates in A, B and C against 3.0 by
  chance (p = 0.83). None also beats push3 in both real halves.
- *Greedy stacking* on A+B at D=3: nine filters double the in-sample PnL/chain
  (+0.31 → +0.65) while C's total PnL falls from +108.92 to +85.76 and the real
  window's from +58.28 to +24.79.

Two near misses, both finds of the real window that the longer record rejects.
`pos672_b >= 0.75` (price in the top quarter of its 1-week range, oriented to the
bet), stacked on push3, is the best real-window book at every size (+9,244 at a
$500 target, PnL/maxDD 1.98 against 0.63), but over B it is worse than push3 alone
(3.65 against 7.93). `abs_run >= 4` (four or more consecutive candles *into* the
signal — 100% of such runs) never busts at D=3 in the real window (48 chains),
but over A it is worse than unfiltered (2.10 against 8.31).

**Caveats.** Ten weeks of real prices and 111 filtered chains; H2 alone is a
losing month for the unfiltered preset, and push3 only shrinks it to +0.0444 a
chain. Fills take the +5s book from a ~2s-sampled top-20 ladder and assume your
order does not move it. Real rung fills creep 0.537 → 0.540 → 0.550. Makers pay
no fee, so a resting entry that fills would keep the ~1.7c a share these numbers
give away.

### The "no-loss" depth for every 15m preset — and why it is not one

A recovery ladder never loses only if it is deeper than the longest run of
losing windows any chain ever met. Measured for all 68 `PM 15m` presets with the
engine at depth 40 (so nothing busts and the deepest rung used *is* the no-loss
depth), on the whole 15m record 2017-08 → 2026-09-16 via the candle proxy,
the trailing two years, and the real window (real fills, Gamma outcomes,
2026-07-03 →). Capital is the peak chain exposure per **$1 of target** at the
real 0.537 fill plus the taker fee, and the last figure is the record's whole
P&L (= its chain count, every chain wins) divided by that capital.

Cells: **depth needed on the whole record** / trailing 2 years / real window ·
peak capital per $1 target · P&L ÷ capital over nine years.

| Strategy | Volume | Balanced | Selective |
|---|---|---|---|
| RSI + BB | **12** / 11 / 6 · $16,318 · 0.44 | **12** / 11 / 5 · $16,318 · 0.27 | **12** / 11 / 4 · $16,318 · 0.17 |
| Stoch Wick | **12** / 12 / 6 · $16,318 · 0.23 | **12** / 12 / 6 · $16,318 · 0.15 | **11** / 11 / 7 · $7,271 · 0.25 |
| ATR DevExh | **11** / 10 / 7 · $7,271 · 0.78 | **11** / 10 / 7 · $7,271 · 0.47 | **10** / 7 / 6 · $3,239 · 0.40 |
| BB Squeeze | **12** / 10 / 7 · $16,318 · 0.49 | **12** / 10 / 9 · $16,318 · 0.23 | **12** / 8 / 4 · $16,318 · 0.11 |
| Zscore MS | **12** / 11 / 9 · $16,318 · 0.43 | **11** / 9 / 6 · $7,271 · 0.49 | **9** / 9 / 7 · $1,443 · 0.90 |
| Regime Switch | **12** / 10 / 9 · $16,318 · 0.43 | **12** / 10 / 9 · $16,318 · 0.30 | **10** / 10 / 9 · $3,239 · 0.67 |
| Volume Exhaustion | **13** / 13 / 7 · $36,623 · 0.41 | **12** / 10 / 7 · $16,318 · 0.27 | **10** / 10 / 8 · $3,239 · 0.51 |
| Jump Exhaustion | **12** / 11 / 9 · $16,318 · 0.92 | **11** / 11 / 7 · $7,271 · 0.90 | **11** / 11 / 7 · $7,271 · 0.32 |
| CCI Williams | **14** / 14 / 7 · $82,190 · 0.09 | **12** / 10 / 7 · $16,318 · 0.26 | **10** / 10 / 4 · $3,239 · 0.72 |
| Multi Horizon | **12** / 11 / 8 · $16,318 · 0.50 | **12** / 10 / 7 · $16,318 · 0.31 | **12** / 10 / 3 · $16,318 · 0.13 |
| Fair Value Gap | **12** / 12 / 8 · $16,318 · 0.72 | **12** / 12 / 8 · $16,318 · 0.19 | **13** / 13 / 5 · $36,623 · 0.04 |
| Fib Retracement | **12** / 10 / 6 · $16,318 · 0.53 | **12** / 9 / 6 · $16,318 · 0.22 | **12** / 7 / 5 · $16,318 · 0.09 |
| Candlesticks | **10** / 9 / 7 · $3,239 · 2.41 | **10** / 8 / 7 · $3,239 · 1.34 | **10** / 9 / 7 · $3,239 · 0.40 |
| Reversal | **14** / 14 / 7 · $82,190 · 0.08 | **14** / 14 / 7 · $82,190 · 0.07 | **11** / 11 / 7 · $7,271 · 0.20 |
| Harmonic Patterns | **14** / 14 / 7 · $82,190 · 0.10 | **14** / 14 / 7 · $82,190 · 0.04 | **12** / 12 / 7 · $16,318 · 0.15 |
| CHoCH | **12** / 12 / 7 · $16,318 · 0.49 | **10** / 8 / 4 · $3,239 · 1.16 | **10** / 8 / 4 · $3,239 · 0.57 |
| Momentum Indicators | **14** / 14 / 8 · $82,190 · 0.20 | **12** / 9 / 6 · $16,318 · 0.21 | **10** / 7 / 7 · $3,239 · 0.28 |
| Elliott Wave | **14** / 14 / 7 · $82,190 · 0.10 | **14** / 14 / 10 · $82,190 · 0.06 | **14** / 14 / 6 · $82,190 · 0.02 |
| Renko | **11** / 11 / 8 · $7,271 · 0.99 | **10** / 7 / 5 · $3,239 · 0.88 | **10** / 9 / 9 · $3,239 · 0.40 |
| Trend Lines | **12** / 12 / 6 · $16,318 · 0.64 | **10** / 10 / 6 · $3,239 · 1.06 | **13** / 13 / 4 · $36,623 · 0.06 |
| Support & Resistance | **14** / 14 / 8 · $82,190 · 0.24 | **9** / 9 / 7 · $1,443 · 2.29 | **9** / 9 / 7 · $1,443 · 2.06 |
| Gann Angles | **13** / 13 / 9 · $36,623 · 0.35 | **12** / 12 / 8 · $16,318 · 0.39 | — |
| Oscillators | **11** / 11 / 7 · $7,271 · 1.24 | **12** / 11 / 8 · $16,318 · 0.21 | **11** / 10 / 6 · $7,271 · 0.26 |

**The answer is 9–14 rungs, median 12, and it belongs to the tape, not to any
strategy.** The longest run of same-direction 15m candles in the record is 16;
there are 95 runs of 10 or more and 20 of 12 or more. A chain that opens against
one of them loses until it ends, whatever signal opened it — which is why the
deepest chains of 57 of the 68 presets fall on the same four dates (2021-08-04,
2022-05-05, 2025-11-13, 2026-06-24). Only 923 of 353,317 chains across all
presets ever reached rung 8.

**Why this is not a promise.**

- *The record's maximum is one chain.* The 2-year depth is 1–5 rungs shallower
  than the full-record depth for 30 of 68 presets (RSI + BB: 11 vs 12; Fib
  Selective: 7 vs 12; Renko Balanced: 7 vs 10). A ladder sized to the last two
  years would have busted on the full record, and the real window already
  needed 10 rungs for Elliott Wave Balanced in ten weeks.
- *One more loss than the record costs 2.24x the whole stack.* Each rung
  multiplies the sunk outlay by `1 / (1 − breakeven)` = 2.24 at these prices:
  after 8 losses $642 is gone per $1 of target, after 10 $3,239, after 12
  $16,318, after 14 $82,190. At the median depth 12 a single 13-rung streak —
  four rungs deeper than anything in 95 long runs, but one deeper than the
  record — costs $36,623 per $1 target, five times the nine-year profit.
- *It does not pay even when it holds.* P&L ÷ peak capital over nine years is
  below 1.0 for 61 of 68 presets — i.e. under ~5% a year on the capital the
  ladder must hold in reserve — and the best (Candlesticks Volume, 2.41;
  Support & Resistance Balanced, 2.29) need a $3,239 / $1,443 reserve per $1 of
  target. RSI + BB *Volume* at depth 1 earns 4–10 per dollar of bankroll on
  the same proxy in every multi-year period (table above); at depth 12 it is 0.44.
- *It cannot be filled.* Rung 10 of a $1 target buys ~$1,800 of shares; the
  median 15m book holds ~$650 within 5c of the best price. At any real target
  the top rungs walk off the ladder long before they could recover.

So: the depth that never lost is 9–14, it needs $1,400–$82,000 of reserve per $1
of target, it would have returned under 5% a year on that reserve, and one
streak longer than the record — which the 2-year-vs-9-year comparison shows is
ordinary — wipes it out several times over. Use the fitted depths (1 for most,
3 where the rungs were shown to earn) and treat a bust as a cost of business,
not something a deeper ladder can abolish.

### Fitted depth for every 5m preset on two years of history

The real 5m book only goes back to 2026-02-13, so depth was re-fitted for all
122 `PM 5m` presets on **two years of candles** (2024-09-13 → 2026-09-13, split
into a first year H1 and a last year H2), with 2023-09-13 → 2024-09-13 as an
extra year that no sweep ever trained on, and the real window (real +5s fills,
real resolutions, 2026-02-13 → 2026-09-16) as the confirmation. Measured
2026-09-16 with the engine above; scratch `depth5m.py` / `report5m.py`.

*Proxy.* Outcome = the Binance 5m candle's direction. Over the real window it
agrees with the market's own resolution on **95.5%** of 60,274 windows (98% in
Feb–Mar, 87–91% in Aug–Sep), and in the disagreements the market sides with the
*previous* bar 58.7% of the time, so the proxy flatters a fade slightly: at
depth 1 the real hit rate is a median **0.56pp below** the proxy's on the same
signals. Every rung buys at a constant fill equal to that preset's real mean
rung-1 fill (0.508–0.564, median 0.530), plus the 0.07 taker fee on every buy;
a +1c and a +2c variant stress the fill. Target $1 per chain.

*Rule.* Start at depth 1. Add a rung only while the next rung is itself
positive-edge (hit ≥ breakeven) on ≥ 50 bets in **both** years, chain P&L
stays positive in both years, and two-year PnL / (peak chain exposure +
|max drawdown|) improves. No skipping a rung. A preset whose depth 1 is not
positive in both years gets no depth at all.

**Rung hit rates are flat on the long record.** Pooled over all 122 presets,
each rung conditional on every earlier rung losing:

| | rung 1 | rung 2 | rung 3 | rung 4 | rung 5 |
|---|---:|---:|---:|---:|---:|
| 2y proxy, bets | 316,977 | 142,349 | 64,252 | 28,858 | 13,235 |
| 2y proxy, hit | 55.09% | 54.86% | 55.09% | 54.14% | 55.14% |
| 2y proxy, edge vs breakeven | +0.55pp | +0.34pp | +0.58pp | −0.35pp | +0.65pp |
| real window, hit | 54.51% | 55.26% | 54.97% | 54.24% | 57.10% |
| real window, mean fill | 0.527 | **0.535** | **0.538** | 0.538 | 0.546 |
| real window, edge | +0.07pp | +0.04pp | −0.52pp | −1.28pp | +0.79pp |

A loss does not make the next 5m bet better across the board — the rung-2 lift
seen on RSI + BB *Volume* over six months does not hold on two years (its rung 2
is −0.79pp in H1, +0.61pp in H2). And on the real book rung 2 is 0.8c dearer
than rung 1 and rung 3 1c dearer, which the constant-fill proxy cannot see.
So depth mostly multiplies the stake: the median PnL / (peak + |maxDD|) across
presets is 1.49 / 0.94 / 0.81 / 0.37 at depths 1–4 on two years, 1.12 / 0.66 /
0.52 / 0.38 on the last year alone, and a depth chosen on the first year that
was deeper than 1 beat depth 1 in the second year only **21 times out of 64**.
At the 0.07 taker fee the whole thing is thin: with every fill 2c dearer the
median preset is negative at every depth.

**Picks: depth 1 for 65 presets, 2 for 12, 3 for 4, and no depth for 41** (not
positive in both years at any depth: every ATR DevExh weekday preset, the three
big Volume Exhaustion presets, Jump Exhaustion *Volume* / *All Days* /
*Balanced*, Harmonic *Volume*, Reversal *BOS Volume*, Trend Lines, Support &
Resistance, Fib *Volume*, Gann *Angled Fan*, …). Of the 16 deeper picks only
**7** keep a depth above 1 with a +1c fill, and only **9** have the real window
also prefer a depth above 1. The ones that survive every check:

- **RSI + BB *Balanced* → depth 3.** Rungs 2 and 3 are positive in 2023-24, H1,
  H2 *and* the real window (rung 3: +5.9 / +5.9 / +6.6 / +8.1pp); PnL/cap
  2.01 → 2.95 → 6.60 at depths 1–3; real +113.3 on 599 chains at $1.
- **Jump Exhaustion *Sat Hi Hit* and *Sat Volume* → depth 2.** Rung 2 positive
  in every period (+9.2 / +4.5 / +4.6 / +4.6pp for Sat Hi Hit); +1c, the last
  year and the real window all say 2; real +36.5 / +46.6.
- **Harmonic *Balanced* → depth 2** on the two-year rule and the real window
  (+40.6), but +1c drops it to 1 — treat it as depth 1–2.

Zscore MS *Volume* and Regime Switch *Volume* pick depth 2 on the proxy and pass
+1c, but **lose on the real window at every depth** (−62 and −121 at depth 1):
they are the thin-edge high-volume presets where the 0.56pp proxy optimism is
the whole margin. Regime Switch *Wknd Volume* / *Wknd Balanced* and Reversal
*BOS Balanced* pick depth 3 and the real window agrees, but +1c drops them to
no depth at all.

Full table. *fill* = real mean rung-1 fill used by the proxy; **depth** = the
rule above on two years; *+1c* = the same rule with every fill 1c dearer;
*1y* / *real* = the best depth on the last year alone / the real window alone
(argmax, positive P&L, ≥ 50 bets on the deepest rung); *2y PnL/cap* = two-year
PnL / (peak + |maxDD|) at depths 1 · 2 · 3; *real P&L* = the real window at the
recommended depth, $1 target. "—" = no positive depth.

| Strategy | Preset | sig/2y | fill | **depth** | +1c | 1y | real | 2y PnL/cap D1 · D2 · D3 | real P&L @ depth (chains) |
|---|---|---:|---:|:---:|:---:|:---:|:---:|---|---:|
| RSI + BB | Volume | 4,824 | 0.537 | **1** | — | 1 | 3 | +2.24 · -0.11 · +0.15 | -7.9 (1484) |
| RSI + BB | Balanced | 2,763 | 0.534 | **3** | 3 | 3 | 3 | +2.01 · +2.95 · +6.60 | +113.3 (599) |
| RSI + BB | Hi Hit | 110 | 0.545 | — | — | 1 | — | +4.28 · +1.14 · +0.57 | — |
| RSI + BB | Wknd Volume | 1,290 | 0.540 | — | — | 3 | 1 | +0.12 · -0.05 · +0.85 | — |
| RSI + BB | Wknd Balanced | 965 | 0.537 | **1** | 1 | 1 | 2 | +4.26 · +2.06 · +0.89 | +31.4 (298) |
| RSI + BB | Wknd Hi Hit | 193 | 0.524 | **1** | 1 | 1 | 1 | +1.55 · +0.72 · +0.71 | +19.4 (70) |
| RSI + BB | Volume - 2yr Train | 7,159 | 0.535 | **1** | — | 1 | 3 | +2.27 · +0.20 · +0.91 | +10.9 (2270) |
| Stoch Wick | Volume | 7,022 | 0.533 | **1** | — | 1 | 4 | +1.13 · -0.33 · -0.24 | -30.2 (1807) |
| Stoch Wick | Balanced | 2,157 | 0.532 | **2** | — | 2 | 2 | +0.56 · +0.77 · +1.17 | +82.5 (405) |
| Stoch Wick | Hi Hit | 400 | 0.520 | **1** | 1 | 2 | 1 | +1.74 · +1.17 · +1.39 | +1.6 (111) |
| Stoch Wick | Wknd Volume | 2,780 | 0.542 | **1** | — | 1 | 2 | +0.57 · -0.23 · +0.20 | +71.8 (612) |
| Stoch Wick | Wknd Balanced | 1,636 | 0.536 | **1** | — | 1 | 1 | +1.51 · -0.03 · +0.43 | +57.5 (389) |
| Stoch Wick | Wknd Hi Hit | 129 | 0.564 | **1** | 1 | 1 | — | +1.09 · -0.09 · -0.79 | -0.3 (23) |
| Stoch Wick | Volume - 2yr Train | 5,148 | 0.529 | **1** | — | 1 | — | +2.05 · -0.42 · -0.78 | -15.9 (1340) |
| ATR DevExh | Volume | 1,957 | 0.525 | — | — | — | — | -0.78 · -0.72 · -0.60 | — |
| ATR DevExh | Balanced | 1,126 | 0.526 | — | — | — | — | -0.54 · -0.55 · -0.52 | — |
| ATR DevExh | Hi Hit | 695 | 0.535 | — | — | 1 | 2 | +0.88 · +0.62 · +0.10 | — |
| ATR DevExh | Wknd Volume | 1,937 | 0.533 | **1** | — | 1 | 1 | +2.33 · +1.68 · +0.85 | +48.8 (453) |
| ATR DevExh | Wknd Balanced | 402 | 0.529 | — | — | 1 | — | -0.41 · -0.08 · -0.50 | — |
| ATR DevExh | Wknd Hi Hit | 328 | 0.537 | **1** | — | 1 | 1 | +0.22 · -0.14 · +0.23 | +12.7 (64) |
| ATR DevExh | Volume - 2yr Train | 2,195 | 0.524 | **1** | — | 4 | — | +0.75 · -0.27 · +0.81 | -4.0 (494) |
| BB Squeeze | Volume | 4,399 | 0.526 | — | — | 5 | — | +0.61 · +1.78 · +3.03 | — |
| BB Squeeze | Balanced | 761 | 0.534 | **1** | 1 | 1 | 1 | +1.88 · +1.85 · +0.81 | +0.9 (182) |
| BB Squeeze | Hi Hit | 150 | 0.529 | — | — | 1 | — | +2.26 · +2.46 · +1.41 | — |
| BB Squeeze | Wknd Volume | 593 | 0.543 | **1** | 1 | 1 | 2 | +2.37 · +1.56 · +2.49 | -6.5 (187) |
| BB Squeeze | Wknd Balanced | 218 | 0.532 | **1** | 1 | 1 | — | +3.23 · +2.72 · +3.12 | -0.4 (90) |
| BB Squeeze | Wknd Hi Hit | 148 | 0.515 | **1** | 1 | 1 | 1 | +9.84 · +7.30 · +7.03 | +8.8 (55) |
| BB Squeeze | Volume - 2yr Train | 4,889 | 0.524 | **2** | — | 5 | — | +2.52 · +2.71 · +1.73 | -107.9 (1126) |
| Zscore MS | Volume | 5,456 | 0.531 | **2** | 2 | 2 | — | +3.67 · +4.64 · +3.23 | -62.1 (1191) |
| Zscore MS | Balanced | 1,289 | 0.533 | — | — | 3 | — | +0.28 · +0.31 · +2.15 | — |
| Zscore MS | Hi Hit | 147 | 0.553 | **1** | 1 | 1 | 1 | +2.18 · +0.98 · +0.45 | +4.9 (53) |
| Zscore MS | Wknd Volume | 1,200 | 0.535 | **1** | 1 | 1 | 2 | +3.63 · +2.89 · +1.61 | +36.9 (421) |
| Zscore MS | Wknd Balanced | 803 | 0.526 | **2** | — | 3 | — | +0.53 · +1.24 · +0.56 | -27.1 (175) |
| Zscore MS | Wknd Hi Hit | 157 | 0.527 | **1** | 1 | 1 | 1 | +3.11 · +4.34 · +2.47 | +9.5 (65) |
| Zscore MS | Volume - 2yr Train | 5,456 | 0.531 | **2** | 2 | 2 | — | +3.67 · +4.64 · +3.23 | -62.1 (1191) |
| Regime Switch | Volume | 5,566 | 0.520 | **2** | 2 | 2 | — | +4.71 · +4.92 · +4.35 | -120.6 (1333) |
| Regime Switch | Balanced | 1,409 | 0.528 | **1** | — | 1 | 1 | +4.32 · +3.16 · +2.19 | +9.1 (326) |
| Regime Switch | Hi Hit | 155 | 0.523 | **1** | 1 | 1 | — | +3.14 · +3.05 · +2.25 | +3.5 (33) |
| Regime Switch | Wknd Volume | 1,934 | 0.528 | **3** | — | 3 | 3 | +2.20 · +2.70 · +2.76 | +74.9 (431) |
| Regime Switch | Wknd Balanced | 1,309 | 0.529 | **3** | — | 3 | 3 | +1.47 · +2.08 · +3.60 | +88.9 (300) |
| Regime Switch | Wknd Hi Hit | 160 | 0.524 | **1** | 1 | 1 | — | +1.46 · +1.04 · +2.05 | +11.6 (47) |
| Regime Switch | Volume - 2yr Train | 4,038 | 0.528 | **2** | — | 2 | — | +1.73 · +3.84 · +2.73 | -34.1 (956) |
| Volume Exhaustion | Selective | 6,076 | 0.531 | — | — | 1 | 1 | +0.04 · -0.29 · -0.47 | — |
| Volume Exhaustion | Max Hit | 167 | 0.526 | **1** | 1 | 1 | 1 | +4.48 · +1.34 · +0.48 | +23.1 (68) |
| Volume Exhaustion | Volume | 5,485 | 0.530 | — | — | 1 | 1 | +0.17 · -0.24 · +0.38 | — |
| Volume Exhaustion | Balanced | 3,748 | 0.530 | — | — | 1 | 2 | +0.48 · -0.49 · -0.43 | — |
| Volume Exhaustion | Hi Hit | 150 | 0.554 | **1** | 1 | 1 | — | +1.12 · +0.08 · +0.25 | +2.3 (46) |
| Volume Exhaustion | Wknd Volume | 1,071 | 0.526 | **1** | 1 | 1 | 2 | +3.03 · +2.71 · +4.92 | +21.5 (266) |
| Volume Exhaustion | Wknd Balanced | 842 | 0.529 | **1** | 1 | 3 | 1 | +2.62 · +2.05 · +4.30 | +25.1 (230) |
| Volume Exhaustion | Wknd Hi Hit | 798 | 0.538 | **1** | 1 | 1 | 1 | +2.55 · +1.19 · +2.25 | +38.5 (259) |
| Volume Exhaustion | Volume - 2yr Train | 4,389 | 0.532 | **1** | — | 1 | 1 | +0.91 · -0.23 · +0.47 | +52.0 (1343) |
| Jump Exhaustion | Sat Hi Hit | 801 | 0.539 | **2** | 2 | 2 | 2 | +3.95 · +4.25 · +3.76 | +36.5 (232) |
| Jump Exhaustion | Sat Volume | 1,531 | 0.532 | **2** | 2 | 3 | 2 | +2.43 · +3.31 · +3.02 | +46.6 (412) |
| Jump Exhaustion | All Days | 6,867 | 0.534 | — | — | 3 | 3 | -0.36 · -0.36 · +0.21 | — |
| Jump Exhaustion | Volume | 5,569 | 0.534 | — | — | — | 4 | -0.20 · -0.12 · -0.05 | — |
| Jump Exhaustion | Balanced | 3,027 | 0.536 | — | — | 1 | 3 | +0.33 · +0.46 · +0.28 | — |
| Jump Exhaustion | Hi Hit | 430 | 0.527 | **2** | 2 | 2 | 1 | +1.37 · +2.18 · +0.79 | +53.1 (114) |
| Jump Exhaustion | Wknd Volume | 1,727 | 0.538 | **1** | 1 | 1 | 3 | +4.37 · +3.31 · +3.22 | +77.2 (571) |
| Jump Exhaustion | Wknd Balanced | 736 | 0.532 | — | — | 1 | 1 | +0.91 · +1.07 · +3.57 | — |
| Jump Exhaustion | Wknd Hi Hit | 110 | 0.537 | **1** | 1 | 1 | — | +0.86 · +1.52 · +1.69 | +12.6 (32) |
| Jump Exhaustion | Volume - 2yr Train | 8,894 | 0.533 | — | — | — | 3 | -0.20 · -0.23 · -0.04 | — |
| CCI Williams | Selective | 6,558 | 0.532 | **1** | — | 1 | — | +1.58 · +0.76 · -0.27 | -47.0 (1776) |
| CCI Williams | Max Hit | 202 | 0.524 | **1** | 1 | 1 | 1 | +3.86 · +0.94 · +1.04 | +5.8 (66) |
| CCI Williams | Volume | 8,444 | 0.532 | **1** | — | 2 | 5 | +1.41 · +0.73 · +0.07 | -5.6 (2137) |
| CCI Williams | Balanced | 2,676 | 0.541 | — | — | 2 | 2 | -0.18 · +0.08 · -0.83 | — |
| CCI Williams | Hi Hit | 113 | 0.525 | **1** | 1 | 1 | — | +1.78 · +0.08 · +0.16 | +1.6 (37) |
| CCI Williams | Wknd Volume | 3,046 | 0.540 | **1** | — | 2 | 1 | +2.89 · +1.09 · +0.10 | +21.7 (791) |
| CCI Williams | Wknd Balanced | 1,776 | 0.540 | **1** | — | 2 | 2 | +4.39 · +2.36 · +1.10 | +8.7 (477) |
| CCI Williams | Wknd Hi Hit | 135 | 0.528 | — | — | — | — | +0.22 · -0.23 · -0.72 | — |
| CCI Williams | Volume - 2yr Train | 8,444 | 0.532 | **1** | — | 2 | 5 | +1.41 · +0.73 · +0.07 | -5.6 (2137) |
| Multi Horizon | Selective | 1,919 | 0.547 | — | — | 2 | 1 | +0.59 · +0.33 · +0.46 | — |
| Multi Horizon | Max Hit | 378 | 0.547 | **1** | 1 | 1 | 1 | +1.51 · +0.87 · +0.32 | +5.3 (95) |
| Multi Horizon | Volume | 4,619 | 0.534 | **1** | — | 1 | 1 | +0.97 · -0.47 · +1.01 | +38.4 (1420) |
| Multi Horizon | Balanced | 1,519 | 0.525 | — | — | 3 | 3 | +0.93 · +0.65 · +1.40 | — |
| Multi Horizon | Hi Hit | 105 | 0.533 | **1** | 1 | 1 | — | +2.15 · +1.15 · +1.11 | +1.9 (29) |
| Multi Horizon | Wknd Volume | 1,127 | 0.537 | **1** | 1 | 1 | 2 | +2.21 · +0.67 · +1.80 | +38.1 (373) |
| Multi Horizon | Wknd Balanced | 792 | 0.528 | **1** | 1 | 1 | 1 | +2.76 · +1.86 · +1.31 | +21.2 (268) |
| Multi Horizon | Wknd Hi Hit | 367 | 0.531 | **1** | 1 | 1 | 1 | +3.47 · +2.71 · +1.47 | +32.7 (135) |
| Multi Horizon | Volume - 2yr Train | 4,619 | 0.534 | **1** | — | 1 | 1 | +0.97 · -0.47 · +1.01 | +38.4 (1420) |
| Fair Value Gap | Volume - 2yr Train | 2,243 | 0.514 | — | — | 1 | — | +0.30 · -0.64 · -0.72 | — |
| Fib Retracement | Volume | 3,683 | 0.514 | — | — | 5 | 4 | -0.90 · -0.56 · +0.30 | — |
| Fib Retracement | Balanced | 1,694 | 0.522 | **1** | 1 | 4 | — | +3.17 · -0.02 · -0.58 | -16.1 (453) |
| Fib Retracement | Selective | 1,245 | 0.518 | — | — | 4 | — | -0.44 · +0.13 · -0.15 | — |
| Fib Retracement | Hi Hit | 303 | 0.511 | — | — | 2 | 2 | +0.11 · +2.02 · +0.17 | — |
| Candlesticks | Volume | 11,767 | 0.519 | **1** | — | 4 | — | +1.99 · +1.72 · +2.68 | -88.3 (3000) |
| Candlesticks | Balanced | 4,587 | 0.528 | **1** | — | 1 | 1 | +1.41 · +0.70 · -0.31 | +31.8 (1214) |
| Candlesticks | Selective | 1,829 | 0.532 | — | — | 1 | 1 | -0.16 · -0.72 · -0.81 | — |
| Candlesticks | Hi Hit | 482 | 0.535 | — | — | — | 1 | -0.02 · +0.19 · -0.26 | — |
| Reversal | BOS Volume | 7,860 | 0.528 | — | — | 4 | 2 | -0.07 · +1.67 · +1.85 | — |
| Reversal | BOS Balanced | 5,612 | 0.532 | **3** | — | 1 | 4 | +1.04 · +1.31 · +1.67 | +176.9 (1492) |
| Harmonic Patterns | Volume | 10,023 | 0.532 | — | — | 6 | 5 | -0.61 · -0.81 · -0.00 | — |
| Harmonic Patterns | Balanced | 2,436 | 0.530 | **2** | 1 | 1 | 2 | +2.07 · +3.27 · +1.24 | +40.6 (633) |
| Harmonic Patterns | Selective | 915 | 0.528 | **1** | 1 | 3 | 1 | +3.44 · +2.77 · +4.13 | +4.8 (265) |
| CHoCH | Volume | 6,780 | 0.535 | **1** | — | 2 | 2 | +2.06 · +1.98 · -0.02 | +9.0 (1884) |
| CHoCH | Balanced | 2,173 | 0.533 | **1** | — | 3 | 2 | +1.34 · +0.97 · +2.25 | +18.9 (618) |
| CHoCH | Selective | 1,061 | 0.530 | **1** | — | 2 | 2 | +2.05 · +2.64 · +0.36 | -15.2 (319) |
| Momentum Indicators | Volume | 9,736 | 0.525 | **1** | — | 2 | 5 | +4.66 · +4.17 · +2.52 | -6.1 (2600) |
| Momentum Indicators | Balanced | 4,168 | 0.524 | **1** | 1 | 1 | 1 | +12.30 · +3.80 · +0.68 | +40.0 (1088) |
| Momentum Indicators | Selective | 504 | 0.517 | **1** | 1 | 1 | 1 | +9.35 · +5.48 · +4.35 | +22.8 (148) |
| Elliott Wave | Volume | 2,175 | 0.524 | — | — | 3 | — | -0.69 · -0.44 · +1.00 | — |
| Elliott Wave | Balanced | 1,410 | 0.528 | — | — | 2 | — | +0.43 · +0.36 · +0.33 | — |
| Elliott Wave | Selective | 377 | 0.526 | **1** | — | 1 | 1 | +1.28 · +0.16 · +1.31 | +2.0 (126) |
| Elliott Wave | Hi Hit | 113 | 0.508 | **1** | 1 | 1 | — | +4.24 · +2.18 · +2.41 | -1.1 (29) |
| Elliott Wave | Volume - 2yr Train | 1,919 | 0.533 | **1** | — | 1 | 1 | +1.03 · -0.47 · +1.49 | +29.3 (587) |
| Elliott Wave | Balanced - 2yr Train | 815 | 0.533 | **1** | 1 | 1 | 1 | +2.07 · +1.40 · +2.22 | +30.5 (227) |
| Renko | Volume | 905 | 0.523 | — | — | 1 | 1 | +1.60 · +1.30 · +0.91 | — |
| Renko | Balanced | 559 | 0.524 | — | — | 1 | — | +1.14 · +2.41 · +2.04 | — |
| Renko | Selective | 126 | 0.525 | **1** | 1 | 1 | — | +1.07 · +0.17 · +0.02 | +14.9 (29) |
| Renko | Hi Hit | 91 | 0.533 | — | — | — | — | -0.01 · -0.10 · -0.43 | — |
| Renko | Volume - 2yr Train | 1,732 | 0.530 | **2** | — | 4 | 2 | +1.94 · +2.05 · +0.66 | +31.3 (436) |
| Renko | Balanced - 2yr Train | 736 | 0.526 | — | — | 1 | 1 | +1.25 · +2.55 · +2.11 | — |
| Trend Lines | Line Break Volume | 5,143 | 0.529 | — | — | 2 | 3 | +0.04 · -0.01 · +0.56 | — |
| Trend Lines | Line Break Balanced | 4,171 | 0.532 | — | — | 3 | 1 | -0.52 · +0.00 · +0.20 | — |
| Support & Resistance | Level Break Volume | 10,230 | 0.529 | — | — | 2 | 3 | +0.07 · +0.66 · +1.02 | — |
| Support & Resistance | Level Break Confirmed | 3,872 | 0.526 | — | — | 3 | — | +0.08 · -0.11 · -0.46 | — |
| Gann Angles | Volume | 8,568 | 0.533 | **1** | — | 1 | 3 | +2.49 · +0.94 · +0.73 | +4.8 (2305) |
| Gann Angles | Balanced | 5,372 | 0.536 | **1** | — | 2 | 1 | +2.67 · +1.82 · -0.38 | +45.9 (1467) |
| Gann Angles | Selective | 2,590 | 0.534 | — | — | 1 | 2 | +0.59 · +0.16 · -0.34 | — |
| Gann Angles | Angled Fan | 45,900 | 0.512 | — | — | 4 | — | -0.95 · -0.93 · -0.88 | — |
| Oscillators | Volume | 15,747 | 0.522 | **1** | — | 2 | — | +1.68 · +1.46 · +0.91 | -94.0 (4125) |
| Oscillators | Balanced | 5,005 | 0.532 | **1** | 1 | 1 | 1 | +3.07 · +0.33 · +0.70 | +51.1 (1504) |
| Oscillators | Selective | 1,284 | 0.537 | **1** | 1 | 1 | 1 | +1.88 · -0.82 · -0.76 | +10.8 (392) |

Caveats. The `2yr Train` presets were fitted on exactly this span, so their
two-year numbers are in-sample (the rung structure is not what they were fitted
on, but the hit rate is); 2023-24 and the real window are the clean checks for
them. The proxy fill is constant per preset, so it understates rungs 2+ by
~1c — the +1c column is the fairer read for any depth above 1. Sessions and
entry filters were left out on purpose; this is the depth question alone.

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
