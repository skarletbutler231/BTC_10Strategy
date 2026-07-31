"""Strategy package: import each strategy module and register its instance.

This is the ONLY place you touch to enable a new strategy in the dashboard.
All ten of the video's strategies are implemented, plus Fair Value Gap, Fib
Retracement, Candlesticks, Reversal, Harmonic Patterns, Momentum Indicators,
Moon Phase, Elliott Wave, Renko, Trend Lines, Support & Resistance, Gann
Angles and Oscillators (additions beyond the video). Filters shared between strategies
(trading window, trend filter, MA/source helpers) live in common.py.
"""

from ..registry import register
from .atr_devexh import AtrDevExh
from .bb_squeeze import BBSqueeze
from .candlesticks import Candlesticks
from .cci_williams import CCIWilliams
from .choch import Choch
from .combined import Combined
from .elliott_wave import ElliottWave
from .fair_value_gap import FairValueGap
from .fib_retracement import FibRetracement
from .gann import Gann
from .harmonic import Harmonic
from .jump_exhaustion import JumpExhaustion
from .momentum import Momentum
from .moon_phase import MoonPhase
from .multi_horizon import MultiHorizon
from .oscillators import Oscillators
from .regime_switch import RegimeSwitch
from .renko import Renko
from .reversal import Reversal
from .rsi_bb import RsiBb
from .stoch_wick import StochWick
from .support_resistance import SupportResistance
from .trend_lines import TrendLines
from .volume_exhaustion import VolumeExhaustion
from .zscore_ms import ZScoreMS

# Registered in the video's own order, which is also the dashboard dropdown order.
register(RsiBb())
register(StochWick())
register(AtrDevExh())
register(BBSqueeze())
register(ZScoreMS())
register(RegimeSwitch())
register(VolumeExhaustion())
register(JumpExhaustion())
register(CCIWilliams())
register(MultiHorizon())

# Beyond the video's ten: classic chart-analysis tools implemented on the same
# framework and held to the same evidence bar.
register(FairValueGap())
register(FibRetracement())
register(Candlesticks())
register(Reversal())
register(Harmonic())
register(Choch())
register(Momentum())
register(MoonPhase())
register(ElliottWave())
register(Renko())
register(TrendLines())
register(SupportResistance())
register(Gann())
register(Oscillators())

# Registered last: it reads the others' presets, so they must already exist.
register(Combined())

# --- The video's ten strategies — all implemented ----------------------------
#   1. RSI + BB            (rsi_bb.py)             <-- DONE
#   2. Stoch Wick          (stoch_wick.py)         <-- DONE
#   3. ATR DevExh          (atr_devexh.py)         <-- DONE
#   4. BB Squeeze          (bb_squeeze.py)         <-- DONE
#   5. ZScore MS           (zscore_ms.py)          <-- DONE
#   6. Regime Switch       (regime_switch.py)      <-- DONE
#   7. Volume Exhaustion   (volume_exhaustion.py)  <-- DONE
#   8. Jump Exhaustion     (jump_exhaustion.py)    <-- DONE
#   9. CCI Williams        (cci_williams.py)       <-- DONE
#  10. Multi Horizon       (multi_horizon.py)      <-- DONE
#
# --- Additions beyond the video ----------------------------------------------
#      Fair Value Gap     (fair_value_gap.py)     <-- DONE
#      Fib Retracement    (fib_retracement.py)    <-- DONE
#      Candlesticks       (candlesticks.py)       <-- DONE
#      Reversal           (reversal.py)           <-- DONE
#      Harmonic Patterns  (harmonic.py)           <-- DONE
#      CHoCH              (choch.py)              <-- DONE
#      Momentum Indicators(momentum.py)           <-- DONE
#      Moon Phase         (moon_phase.py)         <-- DONE (folklore; see its presets)
#      Elliott Wave       (elliott_wave.py)       <-- DONE
#      Renko              (renko.py)              <-- DONE
#      Trend Lines        (trend_lines.py)        <-- DONE
#      Support & Resistance (support_resistance.py) <-- DONE
#      Gann Angles        (gann.py)               <-- DONE
#      Oscillators        (oscillators.py)        <-- DONE
