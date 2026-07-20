"""FastAPI app: strategy catalog, candle loader, and backtest runner.

Run:  ./run.sh   (or  python3 -m uvicorn backend.main:app --port 8100)
Open: http://localhost:8100
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import binance
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

class BacktestRequest(BaseModel):
    strategy_id: str
    symbol: str = "BTCUSDT"
    interval: str = "5m"
    start: str                     # 'YYYY-MM-DD' or unix seconds/ms
    end: Optional[str] = None      # default: now
    params: dict = {}


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
    result = run_backtest(candles, signals, params)

    return {
        "strategy": {"id": strat.id, "name": strat.name},
        "symbol": req.symbol.upper(),
        "interval": req.interval,
        "params": params,
        "bars": len(candles),
        "range": {"from": candles[0]["time"], "to": candles[-1]["time"]},
        "candles": candles,
        "signals": [s.to_dict() for s in signals],
        "trades": result["trades"],
        "markers": _markers(candles, result["trades"]),
        "equity": result["equity"],
        "stats": result["stats"],
    }


# ---- static frontend --------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")
