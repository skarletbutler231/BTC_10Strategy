"""Backtest engine.

Single-position, sequential simulation:

  * A signal fires at the CLOSE of bar i. To avoid look-ahead, the trade is
    entered at the OPEN of bar i+1.
  * Exit is whichever comes first: take-profit (tp_atr_mult x ATR), stop-loss
    (sl_atr_mult x ATR), or a time stop after max_hold_bars bars.
  * Only one position is open at a time; signals that fire while in a trade are
    ignored (recorded as skipped) so the equity curve is realistic.
  * If both TP and SL fall inside the same bar's range, we conservatively assume
    the STOP was hit first.

Exit / cost parameters live here (shared by every strategy) and are merged into
each strategy's schema as the "Exit / Backtest" group.
"""

from __future__ import annotations

from typing import List

from .strategies.base import Param, ParamGroup, Signal

EXIT_PARAM_GROUP = ParamGroup(
    title="Exit / Backtest",
    params=[
        Param("tp_atr_mult", "Take-profit (xATR)", 1.5, "float", 0.1, 20, 0.1,
              "Profit target as a multiple of ATR at entry."),
        Param("sl_atr_mult", "Stop-loss (xATR)", 1.5, "float", 0.1, 20, 0.1,
              "Stop distance as a multiple of ATR at entry."),
        Param("max_hold_bars", "Max hold (bars)", 12, "int", 1, 500, 1,
              "Time stop: exit at this bar's close if neither TP nor SL hit."),
        Param("fee_bps", "Round-trip fee (bps)", 0, "float", 0, 100, 0.5,
              "Total entry+exit cost in basis points, subtracted from each trade."),
    ],
)


def run_backtest(candles: List[dict], signals: List[Signal], params: dict) -> dict:
    """Return {'trades': [...], 'stats': {...}, 'equity': [...]}."""
    tp_mult = float(params.get("tp_atr_mult", 1.5))
    sl_mult = float(params.get("sl_atr_mult", 1.5))
    max_hold = int(params.get("max_hold_bars", 12))
    fee_bps = float(params.get("fee_bps", 0.0))

    sig_by_index = {s.index: s for s in signals}
    n = len(candles)
    trades: List[dict] = []
    skipped = 0

    i = 0
    while i < n:
        sig = sig_by_index.get(i)
        if sig is None or sig.atr <= 0:
            i += 1
            continue

        entry_idx = i + 1  # enter next bar's open (no look-ahead)
        if entry_idx >= n:
            break
        entry = candles[entry_idx]["open"]
        atr = sig.atr

        if sig.side == "long":
            tp = entry + tp_mult * atr
            sl = entry - sl_mult * atr
        else:
            tp = entry - tp_mult * atr
            sl = entry + sl_mult * atr

        exit_idx = None
        exit_price = None
        outcome = None
        last = min(entry_idx + max_hold, n - 1)
        for j in range(entry_idx, last + 1):
            hi = candles[j]["high"]
            lo = candles[j]["low"]
            if sig.side == "long":
                hit_sl = lo <= sl
                hit_tp = hi >= tp
            else:
                hit_sl = hi >= sl
                hit_tp = lo <= tp
            if hit_sl and hit_tp:  # ambiguous bar -> assume stop first
                exit_idx, exit_price, outcome = j, sl, "sl"
                break
            if hit_sl:
                exit_idx, exit_price, outcome = j, sl, "sl"
                break
            if hit_tp:
                exit_idx, exit_price, outcome = j, tp, "tp"
                break
        if exit_idx is None:  # time stop
            exit_idx = last
            exit_price = candles[last]["close"]
            outcome = "time"

        if sig.side == "long":
            gross_pct = (exit_price - entry) / entry * 100.0
        else:
            gross_pct = (entry - exit_price) / entry * 100.0
        pnl_pct = gross_pct - fee_bps / 100.0  # bps -> pct
        win = pnl_pct > 0

        trades.append({
            "side": sig.side,
            "signal_index": i,
            "signal_time": sig.time,
            "entry_index": entry_idx,
            "entry_time": candles[entry_idx]["time"],
            "entry": entry,
            "exit_index": exit_idx,
            "exit_time": candles[exit_idx]["time"],
            "exit": exit_price,
            "outcome": outcome,           # 'tp' | 'sl' | 'time'
            "pnl_pct": round(pnl_pct, 4),
            "hold_bars": exit_idx - entry_idx,
            "win": win,
            "reason": sig.reason,
        })

        # count any signals skipped because we were in this trade
        for k in range(i + 1, exit_idx + 1):
            if k in sig_by_index:
                skipped += 1
        i = exit_idx + 1  # resume scanning after the position closes

    stats = _summarize(trades, len(signals), skipped)
    equity = _equity_curve(trades)
    return {"trades": trades, "stats": stats, "equity": equity}


def _summarize(trades: List[dict], total_signals: int, skipped: int) -> dict:
    n = len(trades)
    if n == 0:
        return {
            "signals": total_signals, "signals_skipped": skipped,
            "trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "total_return_pct": 0.0, "avg_return_pct": 0.0,
            "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
            "profit_factor": 0.0, "expectancy_pct": 0.0,
            "max_drawdown_pct": 0.0, "avg_hold_bars": 0.0,
            "tp_exits": 0, "sl_exits": 0, "time_exits": 0,
        }
    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    gross_win = sum(t["pnl_pct"] for t in wins)
    gross_loss = sum(t["pnl_pct"] for t in losses)  # <= 0
    total = sum(t["pnl_pct"] for t in trades)
    pf = (gross_win / abs(gross_loss)) if gross_loss != 0 else float("inf")
    return {
        "signals": total_signals,
        "signals_skipped": skipped,
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(100.0 * len(wins) / n, 2),
        "total_return_pct": round(total, 3),
        "avg_return_pct": round(total / n, 4),
        "avg_win_pct": round(gross_win / len(wins), 4) if wins else 0.0,
        "avg_loss_pct": round(gross_loss / len(losses), 4) if losses else 0.0,
        "profit_factor": round(pf, 3) if pf != float("inf") else None,
        "expectancy_pct": round(total / n, 4),
        "max_drawdown_pct": round(_max_drawdown(trades), 3),
        "avg_hold_bars": round(sum(t["hold_bars"] for t in trades) / n, 2),
        "tp_exits": sum(1 for t in trades if t["outcome"] == "tp"),
        "sl_exits": sum(1 for t in trades if t["outcome"] == "sl"),
        "time_exits": sum(1 for t in trades if t["outcome"] == "time"),
    }


def _equity_curve(trades: List[dict]) -> list:
    """Cumulative %-return after each closed trade, keyed by exit time."""
    curve = []
    cum = 0.0
    for t in trades:
        cum += t["pnl_pct"]
        curve.append({"time": t["exit_time"], "value": round(cum, 4)})
    return curve


def _max_drawdown(trades: List[dict]) -> float:
    cum = 0.0
    peak = 0.0
    mdd = 0.0
    for t in trades:
        cum += t["pnl_pct"]
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    return mdd  # <= 0
