# BTC 10-Strategy Backtester

A dashboard for backtesting candle-based BTC trading strategies, inspired by the
TradeSmart video *"I Built a 10-Strategy System for Polymarket Trading."* Price
data is served from a **local SQLite database** of Binance candles (built once
from Binance's public bulk archive), with a live-API fallback for the newest
bars. The framework is built so you can drop in the other nine strategies over
time — the dashboard renders each strategy's parameter form automatically from
the backend schema.

**All ten of the video's strategies are implemented**, plus **Fib Retracement**,
**Fair Value Gap** and **Reversal** as additions beyond them.

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
| + | [Fib Retracement](#fib-retracement-beyond-the-video) | Buy the pullback into a measured swing leg |
| + | Fair Value Gap | Trade the retest of a 3-candle price imbalance |
| + | [Reversal](#reversal-beyond-the-video) | Candles, divergence and structure breaks — N of 3 must agree |
| ✗ | [Moon Phase](#moon-phase-a-measured-negative) | Lunar folklore — **measured, no edge**; kept as a documented null |
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
numbers. All ten of the video's strategies are implemented; `Fib Retracement` is
an addition beyond them, held to the same evidence bar.

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
    fib_retracement.py   beyond the video: swing-leg Fibonacci retracement
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
