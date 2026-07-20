"""Strategy registry.

To add a strategy: create a Strategy subclass in backend/strategies/, then
register() its instance in strategies/__init__.py. The dashboard picks it up
automatically (name in the dropdown, params rendered from its schema).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List

if TYPE_CHECKING:  # avoid a circular import (strategies package imports register)
    from .strategies.base import Strategy

_REGISTRY: "Dict[str, Strategy]" = {}


def register(strategy: Strategy) -> Strategy:
    if strategy.id in _REGISTRY:
        raise ValueError(f"duplicate strategy id: {strategy.id}")
    _REGISTRY[strategy.id] = strategy
    return strategy


def get(strategy_id: str) -> Strategy:
    if strategy_id not in _REGISTRY:
        raise KeyError(strategy_id)
    return _REGISTRY[strategy_id]


def all_strategies() -> List[Strategy]:
    return list(_REGISTRY.values())
