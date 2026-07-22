# BTC 10-Strategy Backtester

A dashboard for backtesting candle-based BTC trading strategies, inspired by the
TradeSmart video *"I Built a 10-Strategy System for Polymarket Trading."* Price
data is served from a **local SQLite database** of Binance candles (built once
from Binance's public bulk archive), with a live-API fallback for the newest
bars. The framework is built so you can drop in the other nine strategies over
time — the dashboard renders each strategy's parameter form automatically from
the backend schema.

**Five strategies are implemented: #4 — BB Squeeze, #7 — Volume Exhaustion,
#8 — Jump Exhaustion, #9 — CCI Williams, and #10 — Multi Horizon.**

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
    multi_horizon.py
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
