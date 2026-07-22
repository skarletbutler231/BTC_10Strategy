"""Strategy package: import each strategy module and register its instance.

This is the ONLY place you touch to enable a new strategy in the dashboard.
All ten of the video's strategies are implemented. Filters shared between
strategies (trading window, trend filter, MA/source helpers) live in common.py.
"""

from ..registry import register
from .atr_devexh import AtrDevExh
from .bb_squeeze import BBSqueeze
from .cci_williams import CCIWilliams
from .combined import Combined
from .jump_exhaustion import JumpExhaustion
from .multi_horizon import MultiHorizon
from .regime_switch import RegimeSwitch
from .rsi_bb import RsiBb
from .stoch_wick import StochWick
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
