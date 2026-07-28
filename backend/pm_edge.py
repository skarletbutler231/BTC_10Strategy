"""PM Edge — Polymarket market-vs-model divergence strategy.

Not a candle strategy: it trades the Polymarket 5-minute UP/DOWN market directly,
using the disagreement between the market's YES price and a price-displacement
model (Binance- or Chainlink-fed) that pmqb computed each tick.

Rule (per 5-minute window):
  * In the entry band [entry_from, entry_to] seconds, take the FIRST tick where
    |model_pUp - yes| >= delta.
  * FOLLOW the model: bet UP if model_pUp > yes, else DOWN (fade = the opposite).
  * Enter at the executable book price (buy YES at ask / NO at 1-bid; 'mid' uses
    the yes mid). Hold to Chainlink settlement.
  * PnL per $1 stake: win -> (1-c)/c * (1-fee) ; lose -> -1   (c = entry odds).

Defaults are the sweep-selected optimum: Binance model, follow, 120-180s, delta
0.12 (see README). The Chainlink model's own optimum is later, ~180-210s.

Reads pm_quote / pm_window from the market DB (see backend.db; honours MARKET_DB).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

from . import db

MODEL_COL = {"binance": "p_up_bin", "chainlink": "p_up_chain"}


@dataclass
class PMEdgeConfig:
    model: str = "binance"        # 'binance' | 'chainlink'
    direction: str = "follow"     # 'follow' | 'fade'
    entry_from: int = 120         # seconds into the window
    entry_to: int = 180
    delta: float = 0.12           # min |model_pUp - yes| to fire
    fee: float = 0.04             # platform take on winnings (0.04 = 4%)
    price: str = "exec"           # 'exec' (ask/1-bid) | 'mid' (yes)
    stake: float = 1.0

    def validate(self) -> "PMEdgeConfig":
        if self.model not in MODEL_COL:
            raise ValueError(f"model must be one of {list(MODEL_COL)}")
        if self.direction not in ("follow", "fade"):
            raise ValueError("direction must be 'follow' or 'fade'")
        if self.entry_from >= self.entry_to:
            raise ValueError("entry_from must be < entry_to")
        return self


def _entry_cost(side_up: bool, yes, bid, ask, exec_px: bool) -> float:
    if side_up:
        c = ask if (exec_px and ask is not None) else yes
    else:
        c = (1 - bid) if (exec_px and bid is not None) else (1 - yes)
    return min(max(c, 0.01), 0.99)


def run(start_ts: int, end_ts: int, cfg: "PMEdgeConfig | None" = None,
        *, conn=None) -> dict:
    """Backtest the PM Edge strategy over windows opening in [start_ts, end_ts]."""
    cfg = (cfg or PMEdgeConfig()).validate()
    col = MODEL_COL[cfg.model]
    exec_px = cfg.price == "exec"
    fade = cfg.direction == "fade"

    own = conn is None
    if own:
        conn = db.connect(readonly=True)
    try:
        rows = conn.execute(
            f"""SELECT q.start_ts AS st, q.time - q.start_ts AS el,
                       q.yes AS yes, q.yes_bid AS bid, q.yes_ask AS ask,
                       q.{col} AS m, w.resolved_up AS up
                FROM pm_quote q JOIN pm_window w ON w.start_ts = q.start_ts
                WHERE w.resolved_up IS NOT NULL
                  AND q.start_ts BETWEEN ? AND ?
                  AND (q.time - q.start_ts) BETWEEN ? AND ?
                  AND q.{col} IS NOT NULL
                ORDER BY q.start_ts, q.time""",
            (start_ts, end_ts, cfg.entry_from, cfg.entry_to),
        ).fetchall()
    finally:
        if own:
            conn.close()

    trades = []
    seen = set()
    for r in rows:
        st = r["st"]
        if st in seen:
            continue                       # one bet per window: first qualifying tick
        edge = r["m"] - r["yes"]
        if abs(edge) < cfg.delta:
            continue
        seen.add(st)
        side_up = (edge > 0) ^ fade
        c = _entry_cost(side_up, r["yes"], r["bid"], r["ask"], exec_px)
        win = (side_up == bool(r["up"]))
        pnl = (cfg.stake * (1 - c) / c) * (1 - cfg.fee) if win else -cfg.stake
        trades.append({
            "start_ts": st, "settle_ts": st + 300, "entry_el": r["el"],
            "side": "UP" if side_up else "DOWN", "model_pup": round(r["m"], 4),
            "yes": r["yes"], "edge": round(edge, 4), "odds": round(c, 4),
            "resolved_up": int(r["up"]), "win": win, "pnl": round(pnl, 4),
        })

    return {"config": asdict(cfg), "trades": trades,
            "stats": _summarize(trades, cfg), "equity": _equity(trades)}


def _summarize(trades: list, cfg: "PMEdgeConfig") -> dict:
    n = len(trades)
    if n == 0:
        return {"bets": 0, "wins": 0, "hit_rate": 0.0, "avg_odds": 0.0,
                "breakeven": 0.0, "ev_per_bet": 0.0, "total_pnl": 0.0,
                "max_drawdown": 0.0, "up_bets": 0, "down_bets": 0, "roi_pct": 0.0}
    wins = sum(t["win"] for t in trades)
    total = sum(t["pnl"] for t in trades)
    odds = sum(t["odds"] for t in trades) / n
    cum = peak = mdd = 0.0
    for t in trades:
        cum += t["pnl"]
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    staked = n * cfg.stake
    return {
        "bets": n, "wins": wins, "hit_rate": round(100 * wins / n, 2),
        "avg_odds": round(odds, 4), "breakeven": round(100 * odds, 2),
        "ev_per_bet": round(total / n, 4), "total_pnl": round(total, 2),
        "roi_pct": round(100 * total / staked, 2),
        "max_drawdown": round(mdd, 2),
        "up_bets": sum(1 for t in trades if t["side"] == "UP"),
        "down_bets": sum(1 for t in trades if t["side"] == "DOWN"),
    }


def _equity(trades: list) -> list:
    curve, cum = [], 0.0
    for t in trades:
        cum += t["pnl"]
        curve.append({"time": t["settle_ts"], "value": round(cum, 4)})
    return curve
