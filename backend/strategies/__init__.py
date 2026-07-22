"""Strategy package: import each strategy module and register its instance.

This is the ONLY place you touch to enable a new strategy in the dashboard.
Seven of the video's ten strategies are implemented: #4 BB Squeeze, #5 Zscore MS,
#6 Regime Switch, #7 Volume Exhaustion, #8 Jump Exhaustion, #9 CCI Williams and
#10 Multi Horizon. Filters shared between strategies (trading window, trend
filter, MA/source helpers) live in common.py.
"""

from ..registry import register
from .bb_squeeze import BBSqueeze
from .cci_williams import CCIWilliams
from .jump_exhaustion import JumpExhaustion
from .multi_horizon import MultiHorizon
from .regime_switch import RegimeSwitch
from .volume_exhaustion import VolumeExhaustion
from .zscore_ms import ZScoreMS

register(BBSqueeze())
register(ZScoreMS())
register(RegimeSwitch())
register(JumpExhaustion())
register(CCIWilliams())
register(VolumeExhaustion())
register(MultiHorizon())

# --- Roadmap: the video's ten strategies -------------------------------------
# Implement each as a Strategy subclass, then add a register(...) line here.
#   1. RSI + BB            (rsi_bb.py)
#   2. Stoch Wick          (stoch_wick.py)
#   3. ATR DevExh          (atr_devexh.py)
#   4. BB Squeeze          (bb_squeeze.py)         <-- DONE
#   5. ZScore MS           (zscore_ms.py)          <-- DONE
#   6. Regime Switch       (regime_switch.py)      <-- DONE
#   7. Volume Exhaustion   (volume_exhaustion.py)  <-- DONE
#   8. Jump Exhaustion     (jump_exhaustion.py)    <-- DONE
#   9. CCI Williams        (cci_williams.py)       <-- DONE
#  10. Multi Horizon       (multi_horizon.py)      <-- DONE
