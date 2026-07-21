# BTC 10-Strategy Backtester

A dashboard for backtesting candle-based BTC trading strategies, inspired by the
TradeSmart video *"I Built a 10-Strategy System for Polymarket Trading."* Price
data is served from a **local SQLite database** of Binance candles (built once
from Binance's public bulk archive), with a live-API fallback for the newest
bars. The framework is built so you can drop in the other nine strategies over
time — the dashboard renders each strategy's parameter form automatically from
the backend schema.

**Four strategies are implemented: #4 — BB Squeeze, #7 — Volume Exhaustion,
#8 — Jump Exhaustion, and #9 — CCI Williams.**

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

The `jump2_atr_mult` upper bound is deliberate: on the very biggest moves price
tends to keep going rather than revert, so those are excluded from fading.

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

## Adding another strategy

1. Create `backend/strategies/<name>.py` with a `Strategy` subclass implementing
   `param_groups()` and `generate_signals(candles, params)`.
2. `register()` it in `backend/strategies/__init__.py`.

That's it — it appears in the dropdown and its params render automatically. Params
support four `kind`s — `int`, `float`, `bool` (checkbox), and `enum` (dropdown,
via `options=[…]`) — so a strategy can expose toggles and choices, not just
numbers. The remaining video strategies are listed as TODOs in that `__init__.py`.

## Layout

```
backend/
  main.py            FastAPI app + routes + static serving
  store.py           DB-backed candle reader: resample-from-1m + live gap-fill
  db.py              SQLite connection + schema (candles, ingest_log)
  binance.py         Binance klines (stdlib urllib, paginated, host fallback)
  data/
    ingest.py        bulk-loader: data.binance.vision zips -> SQLite (idempotent)
  indicators.py      ATR / RSI / extremes / MAs / std / percentile (pure Python)
  engine.py          backtest engine + shared Exit/Backtest params
  registry.py        strategy registry
  strategies/
    base.py          Strategy base class, Param / ParamGroup / Signal
    jump_exhaustion.py
    bb_squeeze.py
    cci_williams.py
    volume_exhaustion.py
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
