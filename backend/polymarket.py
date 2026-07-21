"""Polymarket 5-minute UP/DOWN backtest mode (binary directional resolution).

Unlike the TP/SL engine, this models a binary prediction market:

  * Each signal is an INDEPENDENT bet (no one-position-at-a-time; every 5-min
    market is separate), placed at the open of the next candle.
  * It resolves purely on that candle's DIRECTION — WIN if it closes in the
    predicted direction (close>open for an UP/long bet, close<open for DOWN),
    regardless of the intrabar path. TP/SL are irrelevant here.
  * Payout is binary: you buy a $1 share of your side at price ``entry_price``
    (the odds). WIN pays $1 (profit = 1 - price); LOSS forfeits the stake.

Reported per a fixed $1 stake per bet, so cumulative P/L is comparable to the
TP/SL engine's percentage curve. The key metrics are the directional **hit rate**
and, given the odds, the **EV per bet** (positive iff hit rate > breakeven price).
"""

from __future__ import annotations

from typing import List

from .strategies.base import Signal


def run_binary_backtest(candles: List[dict], signals: List[Signal],
                        entry_price: float = 0.5, fee_bps: float = 0.0) -> dict:
    """Resolve each signal as a next-candle UP/DOWN bet. Returns trades/stats/equity."""
    c = min(max(float(entry_price), 0.01), 0.99)   # cost per $1 share; breakeven = c
    fee = max(fee_bps, 0.0) / 10000.0              # taken from winnings
    win_ret = (1.0 - c) / c * (1.0 - fee)          # per $1 stake, if correct
    n = len(candles)

    trades: List[dict] = []
    wins = losses = flats = up_bets = down_bets = 0
    gross_win = gross_loss = 0.0
    cum = peak = mdd = 0.0
    equity: List[dict] = []

    for s in signals:
        j = s.index + 1                # resolves on the NEXT candle
        if j >= n:
            continue                   # no candle to resolve against
        res = candles[j]
        o, cl = res["open"], res["close"]
        actual = "up" if cl > o else "down" if cl < o else "flat"
        bet_up = s.side == "long"
        up_bets += 1 if bet_up else 0
        down_bets += 0 if bet_up else 1

        win = (bet_up and actual == "up") or (not bet_up and actual == "down")
        if win:
            ret = win_ret
            wins += 1
            gross_win += ret
        else:
            ret = -1.0                 # forfeit the whole stake
            if actual == "flat":
                flats += 1
            else:
                losses += 1
            gross_loss += 1.0

        pnl_pct = ret * 100.0
        cum += pnl_pct
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
        equity.append({"time": res["time"], "value": round(cum, 4)})
        trades.append({
            "side": s.side,                       # long == UP bet, short == DOWN bet
            "signal_index": s.index, "signal_time": s.time,
            "entry_index": j, "entry_time": res["time"], "entry": o,
            "exit_index": j, "exit_time": res["time"], "exit": cl,
            "outcome": actual,                    # 'up' | 'down' | 'flat'
            "pnl_pct": round(pnl_pct, 4), "hold_bars": 1,
            "win": win, "reason": s.reason,
        })

    bets = len(trades)
    hit = 100.0 * wins / bets if bets else 0.0
    total = round(cum, 3)
    ev = round(total / bets, 4) if bets else 0.0
    pf = round(gross_win / gross_loss, 3) if gross_loss > 0 else None

    stats = {
        "signals": len(signals), "signals_skipped": 0,
        "trades": bets, "bets": bets,
        "wins": wins, "losses": losses, "flats": flats,
        "up_bets": up_bets, "down_bets": down_bets,
        "win_rate": round(hit, 2), "hit_rate": round(hit, 2),
        "entry_price": c, "breakeven": round(c * 100.0, 1),
        "total_return_pct": total,
        "avg_return_pct": ev, "ev_per_bet_pct": ev,
        "profit_factor": pf,
        "max_drawdown_pct": round(mdd, 3),
        "avg_hold_bars": 1.0,
        # kept for renderer compatibility (not meaningful in binary mode)
        "tp_exits": 0, "sl_exits": 0, "time_exits": 0,
    }
    return {"trades": trades, "stats": stats, "equity": equity}


def binary_markers(trades: List[dict]) -> list:
    """One arrow per bet at its resolution candle, coloured by win/loss."""
    out = []
    for t in trades:
        up = t["side"] == "long"
        out.append({
            "time": t["entry_time"],
            "position": "belowBar" if up else "aboveBar",
            "color": "#26a69a" if t["win"] else "#ef5350",
            "shape": "arrowUp" if up else "arrowDown",
            "text": "",
        })
    out.sort(key=lambda m: m["time"])
    return out
