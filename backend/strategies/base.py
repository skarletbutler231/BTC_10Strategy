"""Strategy framework: parameter schema, signals, and the base class.

A strategy is a self-contained unit that turns candles + params into a list of
Signals. The dashboard renders each strategy's parameter form automatically from
`param_groups()`, so adding a new strategy never requires touching the frontend.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, List, Literal, Optional

Side = Literal["long", "short"]


@dataclass
class Param:
    key: str
    label: str
    default: Any                                      # number, bool, or enum string
    kind: Literal["int", "float", "bool", "enum"] = "float"
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    help: str = ""
    options: Optional[List[str]] = None               # allowed values for kind="enum"

    def coerce(self, v):
        if self.kind == "bool":
            if isinstance(v, bool):
                return v
            if isinstance(v, str):
                return v.strip().lower() in ("1", "true", "yes", "on")
            return bool(v)
        if self.kind == "enum":
            s = str(v)
            return s if (not self.options or s in self.options) else self.default
        v = float(v)
        return int(round(v)) if self.kind == "int" else v


@dataclass
class ParamGroup:
    title: str
    params: List[Param] = field(default_factory=list)


@dataclass
class Signal:
    index: int          # bar index in the candle list
    time: int           # unix seconds of the signal bar
    side: Side          # 'long' or 'short'
    price: float        # close of the signal bar
    reason: str = ""    # human-readable explanation
    atr: float = 0.0    # ATR at the signal bar (drives TP/SL sizing)
    meta: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


class Strategy:
    """Base class. Subclasses set id/name/description and implement the two
    methods below."""

    id: str = "base"
    name: str = "Base"
    description: str = ""

    def param_groups(self) -> List[ParamGroup]:  # pragma: no cover - abstract
        raise NotImplementedError

    def presets(self) -> dict:
        """Optional named parameter presets: {preset_name: {param_key: value}}.
        The 'Default' preset is derived automatically from param defaults."""
        return {}

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:  # pragma: no cover
        raise NotImplementedError

    # ---- shared helpers -------------------------------------------------

    def default_params(self) -> dict:
        out = {}
        for g in self.param_groups():
            for p in g.params:
                out[p.key] = p.default
        return out

    def resolve_params(self, params: Optional[dict]) -> dict:
        """Merge user-supplied params over defaults and coerce types."""
        merged = self.default_params()
        if params:
            merged.update(params)
        specs = {p.key: p for g in self.param_groups() for p in g.params}
        for k, spec in specs.items():
            try:
                merged[k] = spec.coerce(merged[k])
            except (TypeError, ValueError):
                merged[k] = spec.default
        return merged

    def schema(self) -> dict:
        presets = {"Default": self.default_params()}
        presets.update(self.presets())
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "param_groups": [
                {"title": g.title, "params": [asdict(p) for p in g.params]}
                for g in self.param_groups()
            ],
            "presets": presets,
        }
