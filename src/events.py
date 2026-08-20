from dataclasses import dataclass
from typing import Any

from ccxt.base.types import Trade

from src.strategy.base import Signal


@dataclass(frozen=True)
class CandleClosed:
    """Una vela cerrada, con las señales que generó la estrategia."""

    signals: list[Signal]
    last_row: dict[str, Any]
    timeframe: str


@dataclass(frozen=True)
class FillsReceived:
    """Fills nuevos llegados por el websocket de trades propios."""

    trades: list[Trade]


Event = CandleClosed | FillsReceived
