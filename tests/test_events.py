from dataclasses import FrozenInstanceError

import pytest

from src.events import CandleClosed, Event, FillsReceived
from src.strategy.base import Signal


def make_signal(type_: str = "enter_long") -> Signal:
    return Signal(type=type_, symbol="BTC/USDT", data={"close": 100.0})


def test_candle_closed_construction():
    signal = make_signal()
    event = CandleClosed(signals=[signal], last_row={"close": 100.0}, timeframe="15m")

    assert event.signals == [signal]
    assert event.last_row == {"close": 100.0}
    assert event.timeframe == "15m"


def test_fills_received_construction():
    trade = {"id": "1", "order": "o-1", "price": 100.0, "amount": 0.5}
    event = FillsReceived(trades=[trade])

    assert event.trades == [trade]


def test_events_are_frozen():
    event = CandleClosed(signals=[], last_row={}, timeframe="15m")
    fills = FillsReceived(trades=[])

    with pytest.raises(FrozenInstanceError):
        event.timeframe = "1h"
    with pytest.raises(FrozenInstanceError):
        fills.trades = []


def test_event_union_covers_both():
    candle = CandleClosed(signals=[], last_row={}, timeframe="15m")
    fills = FillsReceived(trades=[])

    assert isinstance(candle, Event)
    assert isinstance(fills, Event)
