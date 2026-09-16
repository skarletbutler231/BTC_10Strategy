"""FastAPI app: strategy catalog, candle loader, and backtest runner.

Run:  ./run.sh   — port/host come from .env (see .env.example); default 8100.
Open: http://localhost:$PORT
"""

from __future__ import annotations

import dataclasses
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import binance
from . import db
from . import pm_edge
from . import pm_martingale as pmm
from . import polymarket
from . import registry
from . import store
from . import strategies  # noqa: F401 - registers strategies on import
from .engine import EXIT_PARAM_GROUP, run_backtest

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="BTC 10-Strategy Backtester")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# ---- request models ---------------------------------------------------------

class PMStrategyRun(BaseModel):
    """One strategy's book: its signal preset plus its OWN ladder and filters."""
    id: str
    preset: str = ""
    max_depth: Optional[int] = None      # None -> fall back to the request default
    target: Optional[float] = None
    filters: Optional[dict] = None


class PMBacktestRequest(BaseModel):
    """One run of the Polymarket page.

    Depth, target and filters are per strategy, because the fitted answer differs
    per strategy; the fields here are only the fallback for a strategy that does
    not set its own. Execution settings (entry offset, price, fee, ladder) stay
    global — they describe the account, not the edge.
    """
    strategies: list = []          # PMStrategyRun-shaped dicts, in priority order
    # What happens when two books signal the same window. 'independent': every
    # book trades it (separate accounts; correlated signals stack).
    # 'one_per_window': one account, at most one position per window; the first
    # book in `strategies` takes it and the rest yield. See pmm.run_books.
    mode: str = "independent"
    symbol: str = "BTCUSDT"
    interval: str = "5m"           # the MARKET: '5m' or '15m' -- candle interval
                                   # and window length are the same thing here
    start: str = ""                # 'YYYY-MM-DD'; default = coverage start
    end: str = ""
    # per-strategy defaults
    max_depth: int = 1
    target: float = 1.0
    filters: dict = {}
    # global execution settings
    fee: float = 0.0
    fee_model: str = "winnings"    # 'winnings' (take on a win) | 'taker' (on every buy)
    entry_el: int = 5              # seconds into the window we lift the book
    price: str = "exec"            # 'exec' (ask / 1-bid) | 'mid'
    use_book: bool = False         # size against the real resting ladder
    max_price: float = 0.95
    max_cost: float = 0.0
    by: str = "week"               # period breakdown granularity


class BacktestRequest(BaseModel):
    strategy_id: str
    symbol: str = "BTCUSDT"
    interval: str = "5m"
    start: str                     # 'YYYY-MM-DD' or unix seconds/ms
    end: Optional[str] = None      # default: now
    params: dict = {}
    mode: str = "tpsl"             # 'tpsl' (TP/SL sim) or 'polymarket' (binary up/down)
    entry_price: float = 0.5       # polymarket mode: cost per $1 share (the odds)


# ---- helpers ----------------------------------------------------------------

def _to_ms(value: str, *, end: bool = False) -> int:
    """Parse a date string / epoch into unix milliseconds (UTC)."""
    if value is None or value == "":
        return int(datetime.now(timezone.utc).timestamp() * 1000)
    s = str(value).strip()
    if s.isdigit():
        n = int(s)
        return n if n > 10_000_000_000 else n * 1000  # ms vs seconds
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            if end and fmt == "%Y-%m-%d":
                # make an end-date inclusive of the whole day
                dt = dt.replace(hour=23, minute=59, second=59)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    raise HTTPException(400, f"bad date: {value!r} (use YYYY-MM-DD)")


def _load_candles(symbol: str, interval: str, start: str, end: Optional[str]) -> list:
    start_ms = _to_ms(start)
    end_ms = _to_ms(end, end=True)
    if start_ms >= end_ms:
        raise HTTPException(400, "start must be before end")
    try:
        # DB-backed: history from SQLite (resampled from 1m), tail from Binance.
        candles = store.get_candles(symbol, interval, start_ms, end_ms)
    except binance.BinanceError as e:
        raise HTTPException(502, f"candle load failed: {e}")
    if not candles:
        raise HTTPException(404, "no candles returned for that range/symbol")
    return candles


def _markers(candles: list, trades: list) -> list:
    """Chart markers for lightweight-charts: entry arrows + win/loss exits."""
    out = []
    for t in trades:
        if t["side"] == "long":
            out.append({"time": t["entry_time"], "position": "belowBar",
                        "color": "#26a69a", "shape": "arrowUp", "text": "L"})
        else:
            out.append({"time": t["entry_time"], "position": "aboveBar",
                        "color": "#ef5350", "shape": "arrowDown", "text": "S"})
        out.append({
            "time": t["exit_time"],
            "position": "aboveBar" if t["side"] == "long" else "belowBar",
            "color": "#26a69a" if t["win"] else "#ef5350",
            "shape": "circle",
            "text": f"{t['pnl_pct']:+.1f}%",
        })
    out.sort(key=lambda m: m["time"])
    return out


# ---- API --------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"ok": True, "strategies": [s.id for s in registry.all_strategies()]}


@app.get("/api/coverage")
def coverage(symbol: str = "BTCUSDT"):
    """Ingested 1m coverage for a symbol (min/max unix seconds + row count)."""
    cov = store.coverage(symbol)
    return {"symbol": symbol.upper(), "interval": "1m",
            "db_enabled": store.use_db(), **cov}


@app.get("/api/strategies")
def list_strategies():
    """Strategy catalog with param schemas (Exit/Backtest group appended)."""
    exit_group = {
        "title": EXIT_PARAM_GROUP.title,
        "params": [
            {
                "key": p.key, "label": p.label, "default": p.default,
                "kind": p.kind, "min": p.min, "max": p.max, "step": p.step,
                "help": p.help,
            }
            for p in EXIT_PARAM_GROUP.params
        ],
    }
    exit_defaults = {p.key: p.default for p in EXIT_PARAM_GROUP.params}
    out = []
    for s in registry.all_strategies():
        schema = s.schema()
        schema["param_groups"].append(exit_group)
        for name in schema["presets"]:
            for k, v in exit_defaults.items():
                schema["presets"][name].setdefault(k, v)
        out.append(schema)
    return {"strategies": out}


@app.get("/api/candles")
def get_candles(symbol: str = "BTCUSDT", interval: str = "5m",
                start: str = "", end: str = ""):
    candles = _load_candles(symbol, interval, start, end)
    return {"symbol": symbol.upper(), "interval": interval,
            "count": len(candles), "candles": candles}


@app.post("/api/backtest")
def backtest(req: BacktestRequest):
    try:
        strat = registry.get(req.strategy_id)
    except KeyError:
        raise HTTPException(404, f"unknown strategy: {req.strategy_id}")

    candles = _load_candles(req.symbol, req.interval, req.start, req.end)
    params = strat.resolve_params(req.params)
    signals = strat.generate_signals(candles, params)

    if req.mode == "polymarket":
        result = polymarket.run_binary_backtest(candles, signals, req.entry_price)
        markers = polymarket.binary_markers(result["trades"])
    else:
        result = run_backtest(candles, signals, params)
        markers = _markers(candles, result["trades"])

    return {
        "strategy": {"id": strat.id, "name": strat.name},
        "symbol": req.symbol.upper(),
        "interval": req.interval,
        "mode": req.mode,
        "params": params,
        "bars": len(candles),
        "range": {"from": candles[0]["time"], "to": candles[-1]["time"]},
        "candles": candles,
        "signals": [s.to_dict() for s in signals],
        "trades": result["trades"],
        "markers": markers,
        "equity": result["equity"],
        "stats": result["stats"],
    }


@app.get("/api/pm_edge")
def pm_edge_backtest(
    model: str = "binance", direction: str = "follow",
    entry_from: int = 120, entry_to: int = 180,
    delta: float = 0.12, fee: float = 0.04, price: str = "exec",
    start: str = "", end: str = "",
):
    """PM Edge (market-vs-model divergence) backtest over the Polymarket record."""
    lo, hi = pm_edge_coverage_bounds()
    if lo is None:
        raise HTTPException(404, "no resolved Polymarket windows — run ingest_stream")
    start_ts = (_to_ms(start) // 1000) if start else lo
    end_ts = (_to_ms(end, end=True) // 1000) if end else hi
    try:
        cfg = pm_edge.PMEdgeConfig(
            model=model, direction=direction, entry_from=entry_from,
            entry_to=entry_to, delta=delta, fee=fee, price=price)
        res = pm_edge.run(start_ts, end_ts, cfg)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"config": res["config"], "stats": res["stats"],
            "equity": res["equity"], "coverage": {"from": lo, "to": hi},
            "range": {"from": start_ts, "to": end_ts}}


def pm_edge_coverage_bounds():
    conn = db.connect(readonly=True)
    try:
        r = conn.execute("SELECT MIN(start_ts) lo, MAX(start_ts) hi FROM pm_window "
                         "WHERE resolved_up IS NOT NULL").fetchone()
        return (r["lo"], r["hi"]) if r else (None, None)
    except Exception:  # noqa: BLE001 - pm tables not built yet
        return (None, None)
    finally:
        conn.close()


# ---- Polymarket backtesting page -------------------------------------------
# Strategies offered on /pm-backtest, each with its OWN fitted playbook. The
# ladder depth and entry filter that a strategy wants are properties of that
# strategy's edge, not of the account: RSI + BB and Stoch Wick both want a retry
# inside the US session, Volume Exhaustion wants a flat bet all day. Forcing one
# setting on all three would misrepresent two of them, so each row here carries
# its own presets and the page runs them as separate books.
#
# Every `playbook` is one measured configuration — see the README sections for
# how each was fitted and what it is worth over 2026-03-04 .. 2026-09-04.
# Adding a strategy is one entry here; the page renders it from the schema.
# A playbook's `market` is the window it was measured on ('5m' unless set); the
# page shows only the playbooks of the selected market. `default_15m` names the
# 15m default the way `default` names the 5m one.
PM_STRATEGIES = [
    {
        "id": "rsi_bb",
        "default": "Fitted · D2 · 16-01",
        "default_15m": "15m · Fitted · D1 · push3",
        "playbooks": [
            # 15m: real prices 2026-07-03 .. 2026-09-13, taker fee 7%, $1 target.
            # See README "The 15-minute market: RSI + BB PM 15m Volume".
            {"name": "15m · Fitted · D1 · push3", "preset": "PM 15m Volume",
             "market": "15m", "max_depth": 1, "filters": {"hard_push": True},
             "note": "+17.56 on 111 chains, 62.16% hit, maxDD -13.62 (taker fee). "
                     "Depth 1 is the 3.9-year candle answer: on 15m a retry has "
                     "no more edge than the first bet, so the ladder only adds "
                     "stake. push3 is the one filter of ~3,500 that holds in "
                     "every period; it was fitted on Oscillators 5m, not here"},
            {"name": "15m · Unfiltered · D3", "preset": "PM 15m Volume",
             "market": "15m", "max_depth": 3, "filters": {},
             "note": "+49.04 on 157 chains, maxDD -17.79 — the best raw P&L in "
                     "the ten real weeks, on 8x the capital. Priced per dollar of "
                     "bankroll it loses to D1 in every multi-year candle period "
                     "and rung 3 cannot be filled at a $250 target"},
            {"name": "15m · Unfiltered · D1", "preset": "PM 15m Volume",
             "market": "15m", "max_depth": 1, "filters": {},
             "note": "+4.75 on 205 chains, 56.59% at a 0.537 fill — what push3 "
                     "is worth. Negative in the second half and from a $250 "
                     "target on the real ladder"},
            {"name": "Fitted · D2 · 16-01", "preset": "PM 5m Volume - 2yr Train",
             "max_depth": 2, "filters": {"hours": "16-01"},
             "note": "+167.53 over 543 chains, maxDD -23.07, 13.6x peak capital"},
            {"name": "Flat · D1 · 16-01", "preset": "PM 5m Volume - 2yr Train",
             "max_depth": 1, "filters": {"hours": "16-01"},
             "note": "+95.16, the most capital-efficient at 26.8x peak"},
            {"name": "Unfiltered · D2", "preset": "PM 5m Volume - 2yr Train",
             "max_depth": 2, "filters": {},
             "note": "+142.34 but maxDD -42.17 — what the hour filter is worth"},
            {"name": "Volume preset · D2 · 16-01", "preset": "PM 5m Volume",
             "max_depth": 2, "filters": {"hours": "16-01"},
             "note": "+133.30; the preset the 16-01 session was fitted on"},
            # Balanced preset, fitted 2026-09-16. Its session is 08-16 UTC -- a
            # different clock from the Volume preset's 16-01 -- and it moves the
            # edge INTO rung 1, which is why depth 1 beats the two-year sweep's
            # unfiltered depth 3 once the session is applied.
            {"name": "Balanced · D1 · 08-16", "preset": "PM 5m Balanced",
             "max_depth": 1, "filters": {"hours": "08-16"},
             "note": "+41.65 over 228 chains, maxDD -9.56, 17.0x peak capital, "
                     "19/26 green. Best of all 24 clock rotations in both "
                     "halves independently. The session lifts rung 1 from "
                     "+2.16pp to +8.23pp — and once it has, rungs 2-3 stop "
                     "replicating, so depth 1 is 4.0 P&L per $ of (peak + "
                     "drawdown) against depth 3's 2.0 inside the same hours"},
            {"name": "Balanced · D3 · unfiltered", "preset": "PM 5m Balanced",
             "max_depth": 3, "filters": {},
             "note": "+128.74 on 484 chains — the two-year sweep's answer for "
                     "this preset, and right as far as it goes: unfiltered, rung "
                     "1 is only +0.80pp and rungs 2-3 (+5.1pp, +10.3pp) carry "
                     "the book in every period. But it needs 8x the capital "
                     "and 4x the drawdown of the session book, and priced "
                     "against the real ladder it is 2.3 per $ of capital at a "
                     "$50 chain target against the session's 14.1"},
            {"name": "Balanced · D3 · 08-16", "preset": "PM 5m Balanced",
             "max_depth": 3, "filters": {"hours": "08-16"},
             "note": "+66.70 — both fitted answers at once, and worse than "
                     "either: inside the session rung 2 goes +18.8pp in the "
                     "first half to +2.9pp in the second on 28 and 33 bets"},
            {"name": "Balanced · D1 · unfiltered", "preset": "PM 5m Balanced",
             "max_depth": 1, "filters": {},
             "note": "+36.28 on 723 chains, maxDD -20.23 — what the session is "
                     "worth: a third of the trades for more P&L and half the "
                     "drawdown"},
        ],
    },
    {
        "id": "stoch_wick",
        "default": "Fitted · D2 · 16-01",
        "playbooks": [
            {"name": "Fitted · D2 · 16-01", "preset": "PM 5m Volume",
             "max_depth": 2, "filters": {"hours": "16-01"},
             "note": "+145.83, maxDD -17.80, 19.7x peak capital"},
            {"name": "Flat · D1 · 16-01", "preset": "PM 5m Volume",
             "max_depth": 1, "filters": {"hours": "16-01"},
             "note": "+92.06, maxDD -13.86"},
            {"name": "Unfiltered · D2", "preset": "PM 5m Volume",
             "max_depth": 2, "filters": {},
             "note": "+50.64 — unfiltered this strategy is not tradeable once "
                     "a fee and the ladder are priced"},
            # Wknd Balanced preset, fitted 2026-09-16: depth 1, NO filter. The
            # unfiltered book is already strong and nothing survives a holdout.
            {"name": "Wknd Balanced · D1 · no filter",
             "preset": "PM 5m Wknd Balanced",
             "max_depth": 1, "filters": {},
             "note": "+67.87 over 323 chains, maxDD -7.89, 21.4x peak capital, "
                     "21/26 green, rung 1 at +8.72pp and better in the second "
                     "half than the first. No filter survives: the session grid "
                     "ANTI-predicts out of sample (Spearman -0.58, every H1 "
                     "pick ranks 10-24/24 on H2) and rung 2 disagrees with "
                     "itself (-4.3pp then +8.2pp)"},
            {"name": "Wknd Balanced · D2 · no filter",
             "preset": "PM 5m Wknd Balanced",
             "max_depth": 2, "filters": {},
             "note": "+99.40 at a $1 target for 3.3x the capital and 2.1x the "
                     "drawdown; priced against the real ladder it loses money "
                     "from a $600 chain target where depth 1 still makes +$13,075"},
            {"name": "Wknd Balanced · D1 · 17-05 (not supported)",
             "preset": "PM 5m Wknd Balanced",
             "max_depth": 1, "filters": {"hours": "17-05"},
             "note": "+45.48 and it prices better than unfiltered — but its "
                     "rotation rank on the half it was not fitted on is 10/24, "
                     "chance. On a 300-chain weekend preset the hour grid has "
                     "nothing to say; shown so the temptation is visible"},
        ],
    },
    {
        "id": "volume_exhaustion",
        "default": "Fitted · D1 · no filter",
        "playbooks": [
            {"name": "Fitted · D1 · no filter", "preset": "PM 5m Selective",
             "max_depth": 1, "filters": {},
             "note": "+134.17, maxDD -21.59, 21.9x peak capital. No filter "
                     "survived a holdout and there is no session effect here"},
            {"name": "D2 · no filter", "preset": "PM 5m Selective",
             "max_depth": 2, "filters": {},
             "note": "+139.90 — 4% more P&L for 2.3x the capital"},
            {"name": "D2 · 16-01", "preset": "PM 5m Selective",
             "max_depth": 2, "filters": {"hours": "16-01"},
             "note": "+116.15 on a third of the chains"},
        ],
    },
    {
        "id": "jump_exhaustion",
        "default": "Fitted · D1 · 17-06",
        "playbooks": [
            {"name": "Fitted · D1 · 17-06", "preset": "PM 5m Volume - 2yr Train",
             "max_depth": 1, "filters": {"hours": "17-06"},
             "note": "+124.55 over 1084 chains, maxDD -17.02, 31.1x peak "
                     "capital. The overnight session — the opposite of the "
                     "16-01 the other strategies want"},
            {"name": "D3 · 17-06", "preset": "PM 5m Volume - 2yr Train",
             "max_depth": 3, "filters": {"hours": "17-06"},
             "note": "+263.96 at a $1 target but maxDD -50.30; priced against "
                     "the real ladder it earns less than D1, which can run a "
                     "4x larger chain target"},
            {"name": "Unfiltered · D1", "preset": "PM 5m Volume - 2yr Train",
             "max_depth": 1, "filters": {},
             "note": "+127.48 on paper, but +0.0939/chain in the first half "
                     "against +0.0167 in the second — the session filter is "
                     "what stops the decay"},
        ],
    },
    {
        "id": "cci_williams",
        "default": "Fitted · D1 · 16-01",
        "playbooks": [
            {"name": "Fitted · D1 · 16-01", "preset": "PM 5m Selective",
             "max_depth": 1, "filters": {"hours": "16-01"},
             "note": "+117.13 over 560 chains, maxDD -11.67, 31.1x peak "
                     "capital, 24/27 green weeks. The session is not an "
                     "improvement here — unfiltered this strategy is negative "
                     "in the second half at every depth"},
            {"name": "D2 · 16-01", "preset": "PM 5m Selective",
             "max_depth": 2, "filters": {"hours": "16-01"},
             "note": "+160.13 at a $1 target, but rung 2 goes 69.1% in the "
                     "first half to 50.9% in the second — a coin flip. D1 "
                     "beats it at every matched capital level once priced"},
            {"name": "Unfiltered · D1", "preset": "PM 5m Selective",
             "max_depth": 1, "filters": {},
             "note": "+51.74 on paper but -0.0061/chain in the second half; "
                     "priced against the real ladder it loses -$48,177 at a "
                     "$600 chain target"},
        ],
    },
    {
        "id": "candlesticks",
        "default": "Fitted · D1 · 16-02",
        "playbooks": [
            {"name": "Fitted · D1 · 16-02", "preset": "PM 5m Balanced",
             "max_depth": 1, "filters": {"hours": "16-02"},
             "note": "+79.95 over 415 chains, maxDD -12.65, 22.6x peak "
                     "capital, 20/27 green. The strongest session of any "
                     "strategy here: best of all 24 clock rotations, p=0.042"},
            {"name": "D2 · 16-02", "preset": "PM 5m Balanced",
             "max_depth": 2, "filters": {"hours": "16-02"},
             "note": "+89.62 at a $1 target for 2.9x the drawdown; rung 2 goes "
                     "60.0% in the first half to 45.1% in the second"},
            {"name": "Unfiltered · D1", "preset": "PM 5m Balanced",
             "max_depth": 1, "filters": {},
             "note": "+83.85 and maxDD -38.04 — more raw P&L than the fitted "
                     "book on 2.6x the chains, but +0.0241/chain in the second "
                     "half against the session's +0.1485"},
        ],
    },
    {
        "id": "reversal",
        "default": "Fitted · D1 · 14-00",
        "playbooks": [
            {"name": "Fitted · D1 · 14-00", "preset": "PM 5m BOS Balanced",
             "max_depth": 1, "filters": {"hours": "14-00"},
             "note": "+96.57 over 570 chains, maxDD -10.80, 33.9x peak "
                     "capital. Best capital efficiency on the board once "
                     "priced: 24.4x per $ at a $200 chain target"},
            {"name": "D2 · 14-00", "preset": "PM 5m BOS Balanced",
             "max_depth": 2, "filters": {"hours": "14-00"},
             "note": "+125.01, and the only strategy whose rung 2 IMPROVES out "
                     "of sample (51.1% -> 57.1%) — but D1 still wins at every "
                     "matched capital level once the ladder is priced"},
            {"name": "Unfiltered · D2", "preset": "PM 5m BOS Balanced",
             "max_depth": 2, "filters": {},
             "note": "+164.87, the most raw P&L of any book here, on 13.2x "
                     "peak capital vs the fitted book's 33.9x"},
        ],
    },
    {
        "id": "harmonic",
        "default": "Fitted · D3 · 14-17",
        "playbooks": [
            {"name": "Fitted · D3 · 14-17", "preset": "PM 5m Volume",
             "max_depth": 3, "filters": {"hours": "14-17"},
             "note": "+168.39 over 339 chains, maxDD -22.43, 10.6x peak "
                     "capital, 23/27 green. The only book here whose edge is "
                     "in the LADDER: rung 1 sits at breakeven and rungs 2-3 "
                     "clear it in both halves. Needs ~10x the capital of the "
                     "other books at a tradeable chain target"},
            {"name": "D2 · 14-17", "preset": "PM 5m Volume",
             "max_depth": 2, "filters": {"hours": "14-17"},
             "note": "+68.49 — stopping one rung early gives up most of the "
                     "edge, because rung 3 is the best rung this strategy has"},
            {"name": "Unfiltered · D3", "preset": "PM 5m Volume",
             "max_depth": 3, "filters": {},
             "note": "+181.50 at a $1 target and only 15/27 green. Priced "
                     "against the real ladder it is -$703 at a $50 chain "
                     "target and -$227,257 at $800 — the session is what "
                     "makes the rungs affordable"},
        ],
    },
    {
        "id": "momentum",
        "default": "Fitted · D1 · 16-01 + book lean",
        "playbooks": [
            {"name": "Fitted · D1 · 16-01 + book lean", "preset": "PM 5m Volume",
             "max_depth": 1, "filters": {"hours": "16-01", "book_lean": True},
             "note": "+77.32 over 318 chains, maxDD -8.28, rung 1 at 65.41% "
                     "against a 0.5481 fill (+10.60pp) — the only strategy "
                     "here where a SECOND filter survives. It halves the "
                     "chain count, so it gives up P&L at a $1 target and earns "
                     "it at size: at $1,000 a chain it takes the book from "
                     "0.87 to 3.87 P&L per dollar of peak capital. Ends "
                     "2026-07-27 with the order-book capture"},
            {"name": "D1 · 16-01", "preset": "PM 5m Volume",
             "max_depth": 1, "filters": {"hours": "16-01"},
             "note": "+115.29 over 831 chains, maxDD -10.48, 30.7x peak "
                     "capital, 22/27 green. The session is what makes this "
                     "strategy tradeable at all: it lifts rung 1 from +1.69pp "
                     "to +6.03pp, and unfiltered the book is dead at any size. "
                     "Best of all 24 clock rotations, p=0.001"},
            {"name": "D2 · 16-01", "preset": "PM 5m Volume",
             "max_depth": 2, "filters": {"hours": "16-01"},
             "note": "+222.82 at a $1 target — the most raw P&L here — but "
                     "rung 2 does not replicate (+3.87pp in the first half, "
                     "+0.17pp in the second) and D1 beats it at every matched "
                     "capital level once the ladder is priced"},
            {"name": "Unfiltered · D1", "preset": "PM 5m Volume",
             "max_depth": 1, "filters": {},
             "note": "+88.58 on 2,259 chains but maxDD -30.92 and only 15/27 "
                     "green. Priced against the real ladder its P&L/maxDD is "
                     "0.82 at a $50 chain target and negative by $600"},
            {"name": "Balanced · D1 · 14-00", "preset": "PM 5m Balanced",
             "max_depth": 1, "filters": {"hours": "14-00"},
             "note": "+74.73 over 383 chains, maxDD -7.96, rung 1 +8.55pp. "
                     "Loses to the Volume book at a $1 target (it beats this "
                     "in 89% of paired weekly bootstraps) and wins above "
                     "~$400 a chain, where Volume's drawdown blows out — at "
                     "$1,500 Volume is -$4,685 and this is +$60,208"},
        ],
    },
    {
        "id": "elliott_wave",
        "default": "Fitted · D1 · 16-06",
        "playbooks": [
            {"name": "Fitted · D1 · 16-06", "preset": "PM 5m Volume - 2yr Train",
             "max_depth": 1, "filters": {"hours": "16-06"},
             "note": "+58.61 over 257 chains, maxDD -7.45, 15.6x peak capital, "
                     "22/27 green and positive every month. The strongest "
                     "session measured anywhere here: best of all 24 clock "
                     "rotations in BOTH halves independently (p=0.042 each), "
                     "and its whole session grid carries into the second half "
                     "with zero shrinkage"},
            {"name": "Unfiltered · D1", "preset": "PM 5m Volume - 2yr Train",
             "max_depth": 1, "filters": {},
             "note": "+53.17 on twice the chains but maxDD -14.24 and 17/27 "
                     "green — the session doubles the rung-1 edge, +4.05pp to "
                     "+9.33pp, and halves the drawdown"},
            {"name": "D2 · 16-06", "preset": "PM 5m Volume - 2yr Train",
             "max_depth": 2, "filters": {"hours": "16-06"},
             "note": "+67.96 at a $1 target for 1.7x the capital and 1.8x the "
                     "drawdown. Unfiltered, rung 2 here LOSES in both halves "
                     "(-2.72pp then -7.05pp) — the session lifts it only to "
                     "breakeven, so the ladder never pays on this strategy"},
            {"name": "Balanced 2yr · D1 · 16-06",
             "preset": "PM 5m Balanced - 2yr Train",
             "max_depth": 1, "filters": {"hours": "16-06"},
             "note": "+26.09 on 106 chains — the best rung-1 edge of the two "
                     "at +11.28pp, but it trades 2.4x less often and needs 1.6x "
                     "the peak capital, so it books half the P&L"},
        ],
    },
    {
        "id": "trend_lines",
        "default": "Fitted · D1 · 17-00",
        "playbooks": [
            {"name": "Fitted · D1 · 17-00", "preset": "PM 5m Line Break Volume",
             "max_depth": 1, "filters": {"hours": "17-00"},
             "note": "+80.39 over 392 chains, maxDD -7.30, 12.0x peak capital, "
                     "21/27 green and positive every month. The best-behaved "
                     "session grid of any strategy here — it carries into the "
                     "second half with NEGATIVE shrinkage, i.e. it scored "
                     "better out of sample than in"},
            {"name": "D1 · 17-02", "preset": "PM 5m Line Break Volume",
             "max_depth": 1, "filters": {"hours": "17-02"},
             "note": "+86.14 on 503 chains — the same edge two hours wider, "
                     "which trades 28% more often for a slightly thinner "
                     "rung 1 (+7.36pp vs +8.49pp). The boundary is not finely "
                     "determined; 17-00 through 17-04 all work"},
            {"name": "Unfiltered · D1", "preset": "PM 5m Line Break Volume",
             "max_depth": 1, "filters": {},
             "note": "+37.67 on 1,184 chains with maxDD -38.04 — the session is "
                     "this strategy's whole edge. Priced against the real "
                     "ladder, unfiltered is -$485 at a $200 chain target and "
                     "-$34,121 at $600, where 17-00 makes +$14,710 and +$35,589"},
            {"name": "D2 · 17-00", "preset": "PM 5m Line Break Volume",
             "max_depth": 2, "filters": {"hours": "17-00"},
             "note": "+101.52 at a $1 target, and rung 2 inside the session "
                     "really does replicate (+0.53pp then +5.25pp). It is still "
                     "not worth buying: priced with the real ladder D1 beats it "
                     "at every target (11.09 vs 8.34 per $ of capital at $50, "
                     "13.57 vs 3.21 at $600)"},
            {"name": "Line Break Balanced · D1 · 17-00",
             "preset": "PM 5m Line Break Balanced",
             "max_depth": 1, "filters": {"hours": "17-00"},
             "note": "+52.02 on 316 chains — requiring the close to clear the "
                     "line by 0.3 ATR drops the grazes, but here it drops edge "
                     "with them (+6.66pp vs +8.49pp)"},
        ],
    },
    {
        "id": "support_resistance",
        "default": "Fitted · D1 · 17-23",
        "playbooks": [
            {"name": "Fitted · D1 · 17-23", "preset": "PM 5m Level Break Volume",
             "max_depth": 1, "filters": {"hours": "17-23"},
             "note": "+103.29 over 587 chains, maxDD -11.00, 27.5x peak "
                     "capital, 22/27 green. The session triples the rung-1 "
                     "edge, +2.75pp to +8.35pp; priced against the real ladder "
                     "it is 23.4 per $ of capital at a $50 chain target where "
                     "unfiltered is 14.8, and 12.8 at $600 where unfiltered "
                     "is -11.0"},
            {"name": "Unfiltered · D1", "preset": "PM 5m Level Break Volume",
             "max_depth": 1, "filters": {},
             "note": "+142.36 on 2,376 chains — more raw P&L on 4x the trades, "
                     "but maxDD -35.28 and only 17/27 green, and it goes "
                     "negative once the ladder is priced at a $600 target"},
            {"name": "D3 · unfiltered", "preset": "PM 5m Level Break Volume",
             "max_depth": 3, "filters": {},
             "note": "+437.31 at a $1 target, and rung 3 genuinely replicates "
                     "(+5.48pp then +5.29pp) — the one deep ladder here that "
                     "is not noise. It is still wrong: apply the session and "
                     "that edge moves into rung 1, and priced at a $200 target "
                     "this books -$10,358 against the fitted book's +$16,487"},
            {"name": "D2 · 17-23", "preset": "PM 5m Level Break Volume",
             "max_depth": 2, "filters": {"hours": "17-23"},
             "note": "+139.23 for 3.6x the peak capital and 1.7x the drawdown; "
                     "priced, 7.5 per $ of capital at $50 against depth 1's 23.4"},
            {"name": "Level Break Confirmed · D1 · 17-23",
             "preset": "PM 5m Level Break Confirmed",
             "max_depth": 1, "filters": {"hours": "17-23"},
             "note": "+13.56 on 242 chains — requiring confirmation costs most "
                     "of the edge here (+3.37pp vs +8.35pp) and only 14/27 "
                     "weeks are green"},
        ],
    },
    {
        "id": "gann",
        "default": "Fitted · D3 · 15-18",
        "playbooks": [
            {"name": "Fitted · D3 · 15-18", "preset": "PM 5m Volume",
             "max_depth": 3, "filters": {"hours": "15-18"},
             "note": "+151.01 over 276 chains, maxDD -22.99, 11.6x peak "
                     "capital, 23/27 green and positive every month. The "
                     "second book here whose edge is in the LADDER rather "
                     "than the signal: rungs 1-3 all clear breakeven in both "
                     "halves and improve as they go (+2.67pp, +8.61pp, "
                     "+17.33pp). The depth and the hours are ONE finding — "
                     "rotate the window around the clock at depth 1 and it "
                     "ranks 10/24 out of sample, at depth 3 it ranks 2/24"},
            {"name": "D1 · 15-18", "preset": "PM 5m Volume",
             "max_depth": 1, "filters": {"hours": "15-18"},
             "note": "+18.71 on the same 277 signals — the session alone is "
                     "worth little without the rungs, and only 14/27 weeks "
                     "are green. Priced against the real ladder it is 5.0 per "
                     "$ of capital at a $50 chain target where depth 3 is 9.4, "
                     "and 1.2 at $600 where depth 3 is 2.5"},
            {"name": "Unfiltered · D3", "preset": "PM 5m Volume",
             "max_depth": 3, "filters": {},
             "note": "+208.22 at a $1 target on 7x the trades, but maxDD "
                     "-89.26 and rung 2 is FLAT (-0.09pp) where inside the "
                     "session it is +8.61pp. Priced with the real ladder this "
                     "books -$26,395 at a $200 chain target and -$217,422 at "
                     "$600 — the session is what makes the rungs affordable"},
            {"name": "D4 · 15-18", "preset": "PM 5m Volume",
             "max_depth": 4, "filters": {"hours": "15-18"},
             "note": "+140.29 for 2.9x the peak capital and 2.7x the "
                     "drawdown. Rung 4 disagrees with itself across the two "
                     "halves (-22.0pp then +8.3pp), which is where this "
                     "ladder ends"},
            {"name": "D3 · 21-01", "preset": "PM 5m Volume",
             "max_depth": 3, "filters": {"hours": "21-01"},
             "note": "+202.73 on 420 chains — an overnight band that also "
                     "survives, and trades 1.5x as often. It is the honest "
                     "second choice, not the first: priced it matches 15-18 "
                     "at a $200 chain target and dies at $600 (-0.08 per $ of "
                     "capital against +2.52), and its maxDD is 1.6x deeper"},
            {"name": "Balanced · D3 · 15-18", "preset": "PM 5m Balanced",
             "max_depth": 3, "filters": {"hours": "15-18"},
             "note": "+85.06 on 171 chains. Requiring the close to clear the "
                     "ray by 0.8 ATR buys a much better rung 1 (+5.41pp vs "
                     "+2.67pp) but 38% fewer trades and a deeper drawdown, "
                     "and its rung 3 falls back to +6.09pp"},
        ],
    },
    {
        "id": "oscillators",
        "default": "Fitted · D1 · 11-19 + hard push",
        "playbooks": [
            {"name": "Fitted · D1 · 11-19 + hard push", "preset": "PM 5m Volume",
             "max_depth": 1, "filters": {"hours": "11-19", "hard_push": True},
             "note": "+71.76 over 435 chains, maxDD -9.92, 20.2x peak capital, "
                     "21/27 green. Rung 1 goes 60.46% at a 0.5310 fill "
                     "(+7.36pp) against +1.01pp unfiltered. The only book here "
                     "where a SECOND filter earns more as the chain target "
                     "grows: priced against the real ladder the pair is 11.2 "
                     "per $ of capital at a $600 target where the session "
                     "alone is 5.2, and 4.0 against 0.6 on P&L per unit of "
                     "drawdown"},
            {"name": "D1 · 11-19", "preset": "PM 5m Volume",
             "max_depth": 1, "filters": {"hours": "11-19"},
             "note": "+110.93 on 1,266 chains — more raw P&L on 2.9x the "
                     "trades, and the session on its own is solid (rotation "
                     "null 1/24 in the first half, 4/24 in the second). It "
                     "carries twice the drawdown, and at a $600 chain target "
                     "11 of its 27 weeks are red against the fitted book's 7"},
            {"name": "Hard push only · D1", "preset": "PM 5m Volume",
             "max_depth": 1, "filters": {"hard_push": True},
             "note": "+70.33 on 877 chains — the push filter without the "
                     "hours. It and the session ADD rather than substitute: "
                     "each is worth ~+3pp of rung-1 edge alone and +6.35pp "
                     "together, which is why the fitted book uses both"},
            {"name": "Unfiltered · D1", "preset": "PM 5m Volume",
             "max_depth": 1, "filters": {},
             "note": "+69.74 on 3,555 chains at +1.01pp — barely an edge, and "
                     "maxDD -52.26. Priced against the real ladder it is -8.1 "
                     "per $ of capital at a $200 chain target and -34.1 at "
                     "$600, i.e. not tradeable at any size. The filters are "
                     "this strategy, not a refinement of it"},
            {"name": "D2 · 11-19 + hard push", "preset": "PM 5m Volume",
             "max_depth": 2, "filters": {"hours": "11-19", "hard_push": True},
             "note": "+81.55 at a $1 target, but rung 2 is +5.9pp in the first "
                     "half and -5.9pp in the second — it does not replicate. "
                     "Priced it collapses to 5.2 per $ of capital at $200 "
                     "against depth 1's 17.0, and 18/27 green against 21/27"},
            {"name": "D1 · 16-19 + hard push", "preset": "PM 5m Volume",
             "max_depth": 1, "filters": {"hours": "16-19", "hard_push": True},
             "note": "+44.03 on 157 chains at the best rung-1 edge here "
                     "(+12.45pp) and the shallowest drawdown (-5.26), but a "
                     "quarter of the trades and only 17/26 green weeks — "
                     "thinner weeks are redder weeks even at a better edge"},
            {"name": "Balanced preset · D1 · 11-19 + push",
             "preset": "PM 5m Balanced",
             "max_depth": 1, "filters": {"hours": "11-19", "hard_push": True},
             "note": "+3.83 on 268 chains — the same filters on the RSI 14 "
                     "70/30 preset are worth nothing (+0.49pp). Balanced is a "
                     "different oscillator, not a tier of the same one, so "
                     "neither the session nor the push transfers to it"},
        ],
    },
    {
        "id": "atr_devexh",
        "default": "Fitted · D1 · 13-05",
        "playbooks": [
            {"name": "Fitted · D1 · 13-05", "preset": "PM 5m Wknd Volume",
             "max_depth": 1, "filters": {"hours": "13-05"},
             "note": "+58.36 over 253 chains, maxDD -7.27, 16.4x peak capital, "
                     "20/26 green. Best of all 24 clock rotations on the first "
                     "half, the second half AND the whole span. The session is "
                     "what makes it tradeable at size: priced against the real "
                     "ladder, unfiltered is -0.32 per $ of capital at a $600 "
                     "chain target where this is +3.73"},
            {"name": "Unfiltered · D1", "preset": "PM 5m Wknd Volume",
             "max_depth": 1, "filters": {},
             "note": "+57.74 on 395 chains — the same P&L on 56% more trades, "
                     "with maxDD -11.98 and rung 1 at +6.84pp against the "
                     "session's +10.66pp"},
            {"name": "D2 · 13-05", "preset": "PM 5m Wknd Volume",
             "max_depth": 2, "filters": {"hours": "13-05"},
             "note": "+72.82 at a $1 target for 2.4x the capital; rung 2 goes "
                     "+10.8pp in the first half to -6.6pp in the second, and "
                     "priced it is dead by a $600 target (+0.06 per $ vs 3.73)"},
        ],
    },
    {
        "id": "zscore_ms",
        "default": "Fitted · D1 · book lean",
        "playbooks": [
            {"name": "Fitted · D1 · book lean", "preset": "PM 5m Wknd Volume",
             "max_depth": 1, "filters": {"book_lean": True},
             "note": "+39.82 over 151 chains, maxDD -5.22, rung 1 at 66.89% "
                     "against a 0.5472 fill (+12.17pp). The one filter that "
                     "beats unfiltered in BOTH halves here — every session "
                     "fails (grid Spearman -0.50; the first-half pick ranks "
                     "12/24 on the second). Priced, 5.8 P&L per $ of drawdown "
                     "at a $50 chain target against unfiltered's 1.8. Ends "
                     "2026-07-27 with the order-book capture"},
            {"name": "D1 · hard push", "preset": "PM 5m Wknd Volume",
             "max_depth": 1, "filters": {"hard_push": True},
             "note": "+30.84 on 195 chains, +7.30pp — the runner-up, and the "
                     "one to use past 2026-07-27 since it needs only candles. "
                     "Also positive in both halves; prices at 10.0 per $ of "
                     "capital at $50 vs book lean's 11.2"},
            {"name": "Unfiltered · D1", "preset": "PM 5m Wknd Volume",
             "max_depth": 1, "filters": {},
             "note": "+46.32 on 350 chains but decaying: +0.21/chain in the "
                     "first half, +0.04 in the second, and rung 1 falls from "
                     "+9.5pp to +1.7pp. Priced it is +0.79 per $ of capital "
                     "at a $600 target"},
            {"name": "Unfiltered · D2", "preset": "PM 5m Wknd Volume",
             "max_depth": 2, "filters": {},
             "note": "+64.96 — rung 2 is +16.3pp in the first half and -0.9pp "
                     "in the second; the ladder is riding the same decay"},
        ],
    },
    {
        "id": "regime_switch",
        "default": "Fitted · D1 · 16-04",
        "playbooks": [
            {"name": "Fitted · D1 · 16-04", "preset": "PM 5m Wknd Volume",
             "max_depth": 1, "filters": {"hours": "16-04"},
             "note": "+46.58 over 243 chains, maxDD -8.05, 19/26 green, rung 1 "
                     "+8.52pp against +3.96pp unfiltered. A middling session "
                     "by this page's standards — rotation rank 3/24 in each "
                     "half — but the only thing that makes the preset tradeable: "
                     "priced, unfiltered is -0.58 per $ of capital at a $200 "
                     "chain target where this is +6.32. Note the first half "
                     "is nearly flat (+0.02/chain); the edge is recent"},
            {"name": "D1 · hard push", "preset": "PM 5m Wknd Volume",
             "max_depth": 1, "filters": {"hard_push": True},
             "note": "+27.75 on 143 chains, +8.63pp — also positive in both "
                     "halves, on half the trades. Stacked on the session it "
                     "makes things worse, so it is an alternative, not an "
                     "addition"},
            {"name": "Unfiltered · D1", "preset": "PM 5m Wknd Volume",
             "max_depth": 1, "filters": {},
             "note": "+37.16 on 464 chains, maxDD -18.02 — rung 1 is +0.98pp "
                     "in the first half, i.e. nothing, and +4.14pp in the second"},
            {"name": "D3 · unfiltered", "preset": "PM 5m Wknd Volume",
             "max_depth": 3, "filters": {},
             "note": "+107.49 at a $1 target — the two-year sweep's raw-rule "
                     "pick for this preset, which that sweep itself rejects "
                     "(it does not survive fills 1c dearer). Priced here it is "
                     "-3.92 per $ of capital at a $600 target"},
        ],
    },
    {
        "id": "multi_horizon",
        "default": "Fitted · D1 · 21-00",
        "playbooks": [
            {"name": "Fitted · D1 · 21-00", "preset": "PM 5m Volume",
             "max_depth": 1, "filters": {"hours": "21-00"},
             "note": "+36.13 over 243 chains, maxDD -9.84, 19/27 green and "
                     "positive every month. THE WEAKEST BOOK ON THIS PAGE: "
                     "its rotation rank on the half it was not fitted on is "
                     "6/24 (p=0.25), and priced it is 2.3 P&L per $ of drawdown "
                     "at a $50 chain target, 0.8 at $600. Unfiltered, this "
                     "preset has decayed to zero — rung 1 is +0.04pp in the "
                     "second half. Small size only, if at all"},
            {"name": "Unfiltered · D1", "preset": "PM 5m Volume",
             "max_depth": 1, "filters": {},
             "note": "+59.14 on 1,222 chains with maxDD -41.63 — all of it "
                     "from the first half (+0.12/chain, then -0.004). Priced "
                     "against the real ladder it is -11.08 per $ of capital "
                     "at a $600 chain target"},
        ],
    },
    {
        "id": "choch",
        "default": "Fitted · D1 · hard push",
        "playbooks": [
            {"name": "Fitted · D1 · hard push", "preset": "PM 5m Balanced",
             "max_depth": 1, "filters": {"hard_push": True},
             "note": "+41.77 over 328 chains, maxDD -11.35, rung 1 +5.93pp "
                     "against +3.27pp unfiltered, positive in both halves. "
                     "This preset already keeps only BOS events (the filter the "
                     "Volume preset needed), so the push is the next thing. "
                     "Sessions fail here (grid Spearman -0.54). Priced, 6.9 "
                     "per $ of capital at a $50 chain target vs unfiltered's "
                     "2.1 — but 0.7 by $600: small size only"},
            {"name": "Unfiltered · D1", "preset": "PM 5m Balanced",
             "max_depth": 1, "filters": {},
             "note": "+37.29 on 524 chains — rung 1 is -0.30pp in the first "
                     "half and +5.28pp in the second; priced it is negative "
                     "from a $200 chain target"},
            {"name": "Unfiltered · D2", "preset": "PM 5m Balanced",
             "max_depth": 2, "filters": {},
             "note": "+101.17 at a $1 target, the most raw P&L here, and rung "
                     "2 goes +10.8pp in the first half to -3.1pp in the second "
                     "— it does not replicate. 3.7x the capital of depth 1"},
        ],
    },
]

# Entry filters the page renders per strategy. `applies_to` is a list of
# strategy ids, or "*" for every strategy, so a strategy-specific filter can be
# added later without touching the frontend.
PM_FILTERS = [
    {"key": "hours", "label": "Hours (UTC)", "kind": "text", "default": "",
     "placeholder": "e.g. 16-01", "applies_to": "*",
     "help": "Only open chains in these UTC hours. Inclusive, comma-separated, "
             "and wraps midnight: '16-01' means 16,17,…,23,0,1. Blank = all hours."},
    {"key": "book_lean", "label": "Book leaning your way", "kind": "bool",
     "default": False, "applies_to": "*",
     "help": "Only open a chain when the top of book leans toward the side "
             "being bought (top_imb_sd >= 0). Raises the rung-1 edge on both "
             "Momentum presets in both halves, but halves the trade count — so "
             "it costs P&L at a small chain target and earns it at a large one. "
             "Needs resting size: on 5m the capture only has it to 2026-07-27 "
             "(later windows are filtered out rather than silently kept); the "
             "15m record has it throughout. Fails on RSI + BB 15m."},
    {"key": "hard_push", "label": "Hard push into the signal", "kind": "bool",
     "default": False, "applies_to": "*",
     "help": "Only open a chain when price ran at least 0.3% in the 3 bars "
             "BEFORE entry, in the direction that created the signal "
             "(push3 >= 0.3). On a fade strategy that is how violently the "
             "extreme was reached. Fitted on Oscillators, where it nearly "
             "doubles the rung-1 edge on top of the session and — unusually — "
             "earns MORE at size, not less. On RSI + BB 15m it is the one filter "
             "of ~3,500 tested that holds in every period. Note it is oriented "
             "to the bet: the raw 3-bar return is a side selector, not a filter."},
    {"key": "skip_after_bust", "label": "Skip after a bust", "kind": "bool",
     "default": False, "applies_to": "*",
     "help": "Sit out the next signal after a chain busts. Measured as NOT "
             "worth using on any strategy tested — kept switchable."},
]


# How the books share windows -- see PMBacktestRequest.mode / pmm.run_books.
PM_MODES = ("independent", "one_per_window")


def _parse_require(filters: dict) -> tuple:
    """Filter toggles -> ``(key, op, value)`` predicates for MartingaleConfig.

    Only the fitted form of each filter is exposed. `book_lean` is the natural
    zero boundary, not a threshold that invites re-fitting on the page.
    """
    req = []
    if (filters or {}).get("book_lean"):
        req.append(("top_imb_sd", ">=", 0.0))
    if (filters or {}).get("hard_push"):
        req.append(("push3", ">=", 0.3))
    return tuple(req)


def _parse_hours(spec: str):
    """'16-01,09' -> (0,1,9,16,…,23). Inclusive; a range may wrap midnight."""
    if not spec or not str(spec).strip():
        return None
    out = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("-")
        try:
            a, b = int(a), int(b or a)
        except ValueError:
            raise HTTPException(400, f"bad hours: {spec!r} (use e.g. 16-01)")
        if not (0 <= a < 24 and 0 <= b < 24):
            raise HTTPException(400, f"hours must be 0-23: {spec!r}")
        out += [(a + i) % 24 for i in range((b - a) % 24 + 1)]
    return tuple(sorted(set(out))) or None


_PM_WINDOW_TABLES = {"5m": ("pm_window", "pm_l2_market"),
                     "15m": ("pm_window_15m", "pm_l2_market_15m")}
# A capture shorter than this is a probe, not a record -- pm_l2_market_15m holds
# a single PMData day (2026-01-26) until the backfill has quota -- and must not
# stretch the page's date range months ahead of where the quotes begin.
_PM_MIN_COVERAGE = 2 * 86400


def pm_coverage_bounds(series: str = "5m"):
    """Earliest / latest resolved window of one market across both its captures."""
    try:
        conn = db.connect(readonly=True)
    except Exception:  # noqa: BLE001 - DB not built yet
        return (None, None)
    try:
        lo = hi = None
        for t in _PM_WINDOW_TABLES[series]:
            try:
                r = conn.execute(f"SELECT MIN(start_ts) lo, MAX(start_ts) hi FROM {t} "
                                 "WHERE resolved_up IS NOT NULL").fetchone()
            except Exception:  # noqa: BLE001 - table absent
                continue
            if r and r["lo"] is not None and r["hi"] - r["lo"] >= _PM_MIN_COVERAGE:
                lo = r["lo"] if lo is None else min(lo, r["lo"])
                hi = r["hi"] if hi is None else max(hi, r["hi"])
        return (lo, hi)
    finally:
        conn.close()


# The market dict is ~50k windows for a 6-month range and takes a few seconds to
# assemble, which would otherwise be paid on every click. Ladders are heavier
# still, so both are memoised by exactly what they depend on and the caches are
# capped rather than unbounded.
_PM_MARKET_CACHE: dict = {}
_PM_BOOK_CACHE: dict = {}


def _pm_market(lo: int, hi: int, entry_el: int, series: str = "5m") -> dict:
    key = (series, lo, hi, entry_el)
    if key not in _PM_MARKET_CACHE:
        if len(_PM_MARKET_CACHE) >= 4:
            _PM_MARKET_CACHE.pop(next(iter(_PM_MARKET_CACHE)))
        _PM_MARKET_CACHE[key] = pmm.load_market(lo, hi, entry_el, series=series)
    return _PM_MARKET_CACHE[key]


def _pm_books(windows: frozenset, entry_el: int, series: str = "5m") -> dict:
    key = (series, windows, entry_el)
    if key not in _PM_BOOK_CACHE:
        if len(_PM_BOOK_CACHE) >= 2:
            _PM_BOOK_CACHE.pop(next(iter(_PM_BOOK_CACHE)))
        _PM_BOOK_CACHE[key] = pmm.load_books(windows, entry_el, series=series)
    return _PM_BOOK_CACHE[key]


@app.get("/api/pm_backtest/schema")
def pm_backtest_schema():
    """Strategies with their fitted playbooks, the filter controls, and coverage.

    Playbooks are tagged with the market they were measured on and
    ``default_playbook`` is per market, so the page can switch between the 5m
    and 15m records and show only what was fitted on each.
    """
    out = []
    for entry in PM_STRATEGIES:
        try:
            strat = registry.get(entry["id"])
        except KeyError:
            continue
        available = list(strat.presets())
        books = [{**p, "market": p.get("market", "5m")}
                 for p in entry["playbooks"] if p["preset"] in available]
        if not books:
            continue
        defaults = {}
        for m in pmm.SERIES:
            mine = [p["name"] for p in books if p["market"] == m]
            if not mine:
                continue
            want = entry.get("default" if m == "5m" else f"default_{m}")
            defaults[m] = want if want in mine else mine[0]
        out.append({"id": strat.id, "name": strat.name,
                    "description": strat.description,
                    "presets": available,
                    "playbooks": books,
                    "default_playbook": defaults})
    coverage = {}
    for m in pmm.SERIES:
        lo, hi = pm_coverage_bounds(m)
        coverage[m] = {"from": lo, "to": hi}
    return {"strategies": out, "filters": PM_FILTERS, "markets": sorted(pmm.SERIES),
            "coverage": coverage}


@app.post("/api/pm_backtest")
def pm_backtest(req: PMBacktestRequest):
    """Backtest candle strategies against the REAL Polymarket up/down record.

    ``interval`` picks the market: 5m (``pm_window``/``pm_l2_*``) or 15m
    (``pm_window_15m``/``pm_quote_15m``). Both halves of a bet come from the
    market: the entry price is the executable book at a fixed offset into the
    window, and the outcome is the market's own Chainlink-settled resolution —
    never the Binance candle's direction, which lands on the same side of the
    strike only ~85% of the time on 5m (~96% on 15m).

    Each selected strategy is run as its **own book**, on its own ladder depth
    and its own entry filters, and the books are then aggregated. They are not
    merged into one signal stream: two strategies whose fitted depths differ
    cannot share a chain. ``mode`` says what happens when two books want the
    same window: ``independent`` lets every one of them trade it (their chains
    overlap in time, capital adds), ``one_per_window`` gives it to the first
    book in ``strategies`` and holds one position at a time across all of them.
    """
    if req.interval not in pmm.SERIES:
        raise HTTPException(400, f"interval must be one of {sorted(pmm.SERIES)}")
    if req.mode not in PM_MODES:
        raise HTTPException(400, f"mode must be one of {PM_MODES}")
    one_per_window = req.mode == "one_per_window"
    if req.fee_model not in pmm.FEE_MODELS:
        raise HTTPException(400, f"fee_model must be one of {pmm.FEE_MODELS}")
    window = pmm.SERIES[req.interval]
    cov_lo, cov_hi = pm_coverage_bounds(req.interval)
    if cov_lo is None:
        raise HTTPException(404, f"no resolved Polymarket {req.interval} windows in the DB")
    lo = (_to_ms(req.start) // 1000) if req.start else cov_lo
    hi = (_to_ms(req.end, end=True) // 1000) if req.end else cov_hi
    if lo >= hi:
        raise HTTPException(400, "start must be before end")

    picked = [PMStrategyRun(**s) for s in (req.strategies or []) if s.get("id")]
    if not picked:
        raise HTTPException(400, "select at least one strategy")

    market = _pm_market(lo, hi, req.entry_el, req.interval)
    books_in, bars, book_windows = [], 0, 0
    for entry in picked:
        depth = entry.max_depth if entry.max_depth is not None else req.max_depth
        target = entry.target if entry.target is not None else req.target
        filters = entry.filters if entry.filters is not None else req.filters
        try:
            sigs, n = pmm.signals_for(entry.id, entry.preset, lo, hi,
                                      req.symbol, req.interval)
        except KeyError:
            raise HTTPException(404, f"unknown strategy: {entry.id}")
        except ValueError as e:
            raise HTTPException(400, str(e))
        bars = max(bars, n)

        books = {}
        if req.use_book:
            cand = frozenset(w + k * window for w, *_ in sigs
                             for k in range(max(depth, 1)))
            books = _pm_books(cand, req.entry_el, req.interval)
            book_windows += len(books)
        try:
            cfg = pmm.MartingaleConfig(
                max_depth=depth, target=target, fee=req.fee, fee_model=req.fee_model,
                entry_el=req.entry_el, price=req.price, max_price=req.max_price,
                max_cost=req.max_cost, use_book=req.use_book, window=window,
                hours=_parse_hours((filters or {}).get("hours", "")),
                require=_parse_require(filters or {}),
                skip_after_bust=bool((filters or {}).get("skip_after_bust", False))
            ).validate()
        except ValueError as e:
            raise HTTPException(400, str(e))
        books_in.append((entry, sigs, cfg, books))

    # Played together so that in one_per_window mode a chain in one book can
    # block a signal in another; independent mode is the same call with each
    # book on its own clock.
    results = pmm.run_books([(sigs, cfg, books) for _, sigs, cfg, books in books_in],
                            market, one_per_window=one_per_window)
    runs = []
    for (entry, sigs, cfg, _), res in zip(books_in, results):
        runs.append({
            "label": f"{entry.id}:{entry.preset}", "id": entry.id, "preset": entry.preset,
            "signals": len(sigs), "config": asdict_cfg(cfg),
            "chains": res["chains"], "stats": res["stats"], "rungs": res["rungs"],
            "periods": pmm.period_table(res["chains"], req.by),
            "skipped": res["stats"].get("signals_skipped_in_chain", 0),
            "filtered": res["stats"].get("signals_filtered_out", 0),
            "yielded": res["stats"].get("signals_yielded", 0),
        })

    agg = pmm.combine(runs, one_per_window=one_per_window)
    all_chains = agg.get("chains", [])
    labels = agg.get("labels", [])
    return {
        "range": {"from": lo, "to": hi},
        "coverage": {"from": cov_lo, "to": cov_hi},
        "bars": bars, "windows": len(market), "book_windows": book_windows,
        "execution": {"fee": req.fee, "fee_model": req.fee_model, "entry_el": req.entry_el,
                      "price": req.price, "use_book": req.use_book, "by": req.by,
                      "market": req.interval, "mode": req.mode},
        "runs": [{k: v for k, v in r.items() if k != "chains"} for r in runs],
        "stats": agg["stats"],
        "equity": agg["equity"],
        "periods": pmm.period_table(all_chains, req.by),
        # Capped: the table only ever shows the most recent chains.
        "chains": [{"strategy": lab.split(":")[0], "start_ts": c.start_ts,
                    "side": "UP" if c.side_up else "DOWN", "outcome": c.outcome,
                    "pnl": round(c.pnl, 4), "staked": round(c.staked, 4),
                    "rungs": c.rungs}
                   for lab, c in list(zip(labels, all_chains))[-1500:]],
        "chains_total": len(all_chains),
    }


def asdict_cfg(cfg) -> dict:
    """MartingaleConfig -> JSON-safe dict (tuples become lists)."""
    d = dataclasses.asdict(cfg)
    d["hours"] = list(cfg.hours) if cfg.hours else None
    d["require"] = [list(r) for r in cfg.require]
    return d


# ---- static frontend --------------------------------------------------------

_STATIC_REF = re.compile(r'((?:src|href)=")/static/([^"?]+)(")')


def _page(name: str) -> HTMLResponse:
    """Serve a frontend page with its /static/* references stamped by file mtime.

    A plain reload revalidates only the document: browsers keep scripts and
    stylesheets that carry no Cache-Control for a heuristic fraction of their
    age, so an edited app.js can sit behind a fresh index.html for weeks and a
    new control does nothing. Versioning the URL by mtime makes every edit a
    new URL, and the page itself is sent no-cache so the stamps stay current.
    """
    html = (FRONTEND / name).read_text(encoding="utf-8")

    def stamp(m):
        f = FRONTEND / m.group(2)
        v = int(f.stat().st_mtime) if f.exists() else 0
        return f"{m.group(1)}/static/{m.group(2)}?v={v}{m.group(3)}"

    return HTMLResponse(_STATIC_REF.sub(stamp, html),
                        headers={"Cache-Control": "no-cache"})


@app.get("/")
def index():
    return _page("index.html")


@app.get("/pm-edge")
def pm_edge_page():
    return _page("pm_edge.html")


@app.get("/pm-backtest")
def pm_backtest_page():
    return _page("pm_backtest.html")


app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")
