from dataclasses import dataclass, field
from typing import Literal

from ccxt.base.types import Order, Trade

from src.strategy.base import Signal


@dataclass
class Position:
    symbol: str
    side: Literal["long", "short"]
    open_signal: Signal
    open_order: Order
    open_fills: list[Trade] = field(default_factory=list)
    is_open: bool = True
    id: int | None = None
    close_signal: Signal | None = None
    close_order: Order | None = None
    tp_order: Order | None = None
    sl_order: Order | None = None
    close_fills: list[Trade] = field(default_factory=list)
