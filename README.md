# BTC 10-Strategy Backtester

A dashboard for backtesting candle-based BTC trading strategies, inspired by the
TradeSmart video *"I Built a 10-Strategy System for Polymarket Trading."* Price
data is served from a **local SQLite database** of Binance candles (built once
from Binance's public bulk archive), with a live-API fallback for the newest
bars. The framework is built so you can drop in the other nine strategies over
time — the dashboard renders each strategy's parameter form automatically from
the backend schema.

**Strategy #8 — Jump Exhaustion — is implemented** (the one you asked about).

---

## Quick start

```bash
cd /work/david/PolyMarket/03_BTC_10Strategy
./run.sh                      # or: PORT=8100 ./run.sh
# open http://localhost:8100
```

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

## Adding another strategy

1. Create `backend/strategies/<name>.py` with a `Strategy` subclass implementing
   `param_groups()` and `generate_signals(candles, params)`.
2. `register()` it in `backend/strategies/__init__.py`.

That's it — it appears in the dropdown and its params render automatically. The
nine remaining video strategies are listed as TODOs in that `__init__.py`.

## Layout

```
backend/
  main.py            FastAPI app + routes + static serving
  store.py           DB-backed candle reader: resample-from-1m + live gap-fill
  db.py              SQLite connection + schema (candles, ingest_log)
  binance.py         Binance klines (stdlib urllib, paginated, host fallback)
  data/
    ingest.py        bulk-loader: data.binance.vision zips -> SQLite (idempotent)
  indicators.py      ATR / RSI / rolling close extremes (pure Python)
  engine.py          backtest engine + shared Exit/Backtest params
  registry.py        strategy registry
  strategies/
    base.py          Strategy base class, Param / ParamGroup / Signal
    jump_exhaustion.py
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
