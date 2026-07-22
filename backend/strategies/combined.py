"""Combined (Agreement) — a meta-strategy that requires several strategies to
confirm each other before an entry counts.

Idea
----
Each sub-strategy is run independently over the same candles with the preset you
pick for it. Their signals are then tallied **per candle**: if at least
`min_agree` of the enabled strategies fire on the SAME bar in the SAME direction,
this strategy emits that signal. Set `min_agree` to 1 and it behaves as a simple
union (any strategy's signal passes through); raise it and entries get rarer but
carry more confirmation.

`strict_same_direction` decides what happens when a bar has votes on BOTH sides.
When on (the default), that bar is discarded as a genuine disagreement. When off,
the heavier side wins and the opposing votes are ignored.

Everything downstream is unchanged: the emitted signals feed the usual TP/SL
engine, or the Polymarket next-candle mode, exactly like a single strategy's.

Notes
-----
* Sub-strategy exit params (tp/sl/fee) are ignored — exits come from this
  strategy's own Exit/Backtest group, since there is only one position.
* ATR for a combined signal is the mean ATR of the agreeing signals, so TP/SL
  sizing stays sane regardless of which strategies contributed.
"""

from __future__ import annotations

from typing import List

from .. import registry
from .base import Param, ParamGroup, Signal, Strategy

# Sub-strategies in the video's order. Anything registered but not listed here is
# simply not offered as a voter (and this strategy never votes for itself).
SUB_IDS = ["rsi_bb", "stoch_wick", "atr_devexh", "bb_squeeze", "zscore_ms",
           "regime_switch", "volume_exhaustion", "jump_exhaustion",
           "cci_williams", "multi_horizon"]

DEFAULT_PRESET = "— defaults —"


def _available() -> list:
    """(id, strategy) for each sub-strategy that is actually registered."""
    out = []
    for sid in SUB_IDS:
        try:
            out.append((sid, registry.get(sid)))
        except KeyError:
            continue
    return out


class Combined(Strategy):
    id = "combined"
    name = "Combined (Agreement)"
    description = ("Run several strategies together and only take an entry when "
                   "at least N of them fire on the same candle in the same "
                   "direction.")

    def param_groups(self) -> List[ParamGroup]:
        subs = _available()
        agreement = [
            Param("min_agree", "Min Strategies In Agreement", 1, "int", 1,
                  max(len(subs), 1), 1,
                  "How many enabled strategies must fire on the SAME candle in "
                  "the same direction for the entry to count. 1 = any single "
                  "strategy is enough."),
            Param("strict_same_direction", "Strict same-direction only", True, "bool",
                  help="When a candle has both long and short votes: ON discards "
                       "the bar as a disagreement; OFF lets the heavier side win."),
        ]
        picks = []
        for sid, S in subs:
            options = [DEFAULT_PRESET] + list(S.presets().keys())
            picks.append(Param(f"use_{sid}", S.name, True, "bool",
                               help=f"Include {S.name} as a voter."))
            picks.append(Param(f"preset_{sid}", f"{S.name} preset", DEFAULT_PRESET,
                               "enum", options=options,
                               help=f"Which {S.name} preset casts its vote."))
        return [ParamGroup("Agreement", agreement),
                ParamGroup("Strategies", picks)]

    def presets(self) -> dict:
        subs = [sid for sid, _ in _available()]
        only = lambda keep: {f"use_{s}": (s in keep) for s in subs}
        return {
            # Any single signal passes through — the widest net.
            "Any signal (1 of N)": {"min_agree": 1, "strict_same_direction": True,
                                    **only(subs)},
            # Two independent confirmations.
            "Confirmed (2 agree)": {"min_agree": 2, "strict_same_direction": True,
                                    **only(subs)},
            # High conviction.
            "High conviction (3 agree)": {"min_agree": 3, "strict_same_direction": True,
                                          **only(subs)},
            # The three mean-reversion strategies validated in this repo, 2 of 3.
            "Validated trio (2 of 3)": {"min_agree": 2, "strict_same_direction": True,
                                        **only({"rsi_bb", "stoch_wick", "atr_devexh"})},
        }

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:
        p = self.resolve_params(params)
        min_agree = max(1, int(p["min_agree"]))
        strict = bool(p["strict_same_direction"])

        # bar index -> side -> list of (strategy name, Signal)
        votes: dict = {}
        used: List[str] = []
        for sid, S in _available():
            if not bool(p.get(f"use_{sid}", False)):
                continue
            chosen = p.get(f"preset_{sid}", DEFAULT_PRESET)
            raw = S.presets().get(chosen, {}) if chosen != DEFAULT_PRESET else {}
            try:
                sub_params = S.resolve_params(raw)
                sigs = S.generate_signals(candles, sub_params)
            except Exception:  # noqa: BLE001 - one bad voter must not kill the run
                continue
            used.append(S.name)
            for s in sigs:
                votes.setdefault(s.index, {"long": [], "short": []})
                votes[s.index][s.side].append((S.name, s))

        if not used:
            return []

        out: List[Signal] = []
        for idx in sorted(votes):
            longs = votes[idx]["long"]
            shorts = votes[idx]["short"]
            if strict and longs and shorts:
                continue                      # genuine disagreement -> skip the bar
            side = "long" if len(longs) >= len(shorts) else "short"
            winners = longs if side == "long" else shorts
            if len(winners) < min_agree:
                continue

            c = candles[idx]
            atrs = [s.atr for _, s in winners if s.atr and s.atr > 0]
            atr = sum(atrs) / len(atrs) if atrs else 0.0
            if atr <= 0:
                continue
            names = [n for n, _ in winners]
            opposed = len(shorts) if side == "long" else len(longs)
            reason = (f"{len(winners)}/{len(used)} agree {side.upper()}: "
                      + ", ".join(names)
                      + (f" (vs {opposed} opposing)" if opposed else ""))
            out.append(Signal(
                index=idx, time=c["time"], side=side, price=c["close"],
                reason=reason, atr=atr,
                meta={"agree": len(winners), "opposed": opposed,
                      "voters": names, "enabled": len(used)},
            ))
        return out
