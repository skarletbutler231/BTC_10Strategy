"""Strategy package: import each strategy module and register its instance.

This is the ONLY place you touch to enable a new strategy in the dashboard.
The video demonstrated 10 strategies; #4 (BB Squeeze) and #8 (Jump Exhaustion)
are implemented. The rest are listed as TODO stubs so the roadmap is visible.
"""

from ..registry import register
from .bb_squeeze import BBSqueeze
from .jump_exhaustion import JumpExhaustion

register(BBSqueeze())
register(JumpExhaustion())

# --- Roadmap: the other strategies from the video ----------------------------
# Implement each as a Strategy subclass, then add a register(...) line here.
#   1. RSI + BB            (rsi_bb.py)
#   2. Stoch Wick          (stoch_wick.py)
#   3. ATR DevExh          (atr_devexh.py)
#   4. BB Squeeze          (bb_squeeze.py)      <-- DONE
#   5. ZScore MS           (zscore_ms.py)
#   6. Regime Switch       (regime_switch.py)
#   7. Volume Exhaustion   (volume_exhaustion.py)
#   8. Jump Exhaustion     (jump_exhaustion.py) <-- DONE
#   9. CCI Williams        (cci_williams.py)
#  10. Multi Horizon       (multi_horizon.py)
