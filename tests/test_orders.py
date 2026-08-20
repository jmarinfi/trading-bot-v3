import asyncio

import ccxt
import pytest

from src.orders import OrderService
from src.portfolio.position import Position
from src.strategy.base import Signal


class FakeExchange:
    """Registra las llamadas y devuelve respuestas mínimas con forma de ccxt."""

    def __init__(self, balances: dict | None = None, fail_cancel: bool = False):
        self.calls: list[tuple] = []
        self.balances = {"free": balances or {}}
        self.fail_cancel = fail_cancel

    async def cancel_all_orders(self, symbol: str):
        self.calls.append(("cancel_all_orders", symbol))
        return {"cancelled": 1}

    async def cancel_trigger_order(self, order_id: str):
        if self.fail_cancel:
            raise ccxt.ExchangeError("cancel fail")
        self.calls.append(("cancel_trigger_order", order_id))
        return {"cancelled": order_id}

    async def fetch_balance(self):
        self.calls.append(("fetch_balance",))
        return self.balances

    async def create_trigger_order(self, **kwargs):
        self.calls.append(("create_trigger_order", kwargs))
        return {"data": {"orderId": "sl-1"}}

    async def create_limit_order(self, **kwargs):
        self.calls.append(("create_limit_order", kwargs))
        return {"id": "limit-1"}

    async def create_market_order(self, **kwargs):
        self.calls.append(("create_market_order", kwargs))
        return {"id": "market-1"}


def make_position(side: str = "long", sl_order: dict | None = None) -> Position:
    return Position(
        symbol="BTC/USDT",
        side=side,
        open_signal=Signal(type="enter_long", symbol="BTC/USDT", data={"close": 100.0}),
        open_order={"id": "order-1"},
        sl_order=sl_order,
    )


def call(exchange: FakeExchange, kind: str) -> tuple | None:
    return next((c for c in exchange.calls if c[0] == kind), None)


def test_place_sl_maps_side_long():
    exchange = FakeExchange()
    service = OrderService(exchange)

    sl_order = asyncio.run(
        service.place_sl(position=make_position("long"), trigger_price=95.0, amount=0.5)
    )

    assert sl_order["id"] == "sl-1"
    trigger = call(exchange, "create_trigger_order")[1]
    assert trigger["side"] == "sell"
    assert trigger["trigger_price"] == 95.0
    assert trigger["amount"] == 0.5


def test_place_sl_maps_side_short():
    exchange = FakeExchange()
    service = OrderService(exchange)

    asyncio.run(
        service.place_sl(position=make_position("short"), trigger_price=105.0, amount=0.5)
    )

    assert call(exchange, "create_trigger_order")[1]["side"] == "buy"


def test_place_tp_limit_maps_side():
    exchange = FakeExchange()
    service = OrderService(exchange)

    response = asyncio.run(
        service.place_tp_limit(position=make_position("long"), amount=0.5, price=110.0)
    )

    assert response == {"id": "limit-1"}
    limit = call(exchange, "create_limit_order")[1]
    assert limit["side"] == "sell"
    assert limit["amount"] == 0.5
    assert limit["price"] == 110.0


def test_market_close_maps_sides():
    exchange = FakeExchange()
    service = OrderService(exchange)

    asyncio.run(service.market_close(position=make_position("long"), amount=0.5, price=99.0))
    asyncio.run(service.market_close(position=make_position("short"), amount=0.5, price=101.0))

    market_calls = [c for c in exchange.calls if c[0] == "create_market_order"]
    assert market_calls[0][1]["side"] == "sell"
    assert market_calls[1][1]["side"] == "buy"


def test_open_position_long_places_entry_and_static_sl_below():
    exchange = FakeExchange()
    service = OrderService(exchange)

    entry, sl = asyncio.run(
        service.open_position(
            symbol="BTC/USDT", side="long", price=100.0, amount=0.001, pct_static_sl=0.02
        )
    )

    assert entry == {"id": "limit-1"}
    assert sl["id"] == "sl-1"
    assert call(exchange, "create_limit_order")[1]["side"] == "buy"
    trigger = call(exchange, "create_trigger_order")[1]
    assert trigger["side"] == "sell"
    assert trigger["trigger_price"] == pytest.approx(98.0)


def test_open_position_short_places_entry_and_static_sl_above():
    exchange = FakeExchange()
    service = OrderService(exchange)

    asyncio.run(
        service.open_position(
            symbol="BTC/USDT", side="short", price=100.0, amount=0.001, pct_static_sl=0.02
        )
    )

    assert call(exchange, "create_limit_order")[1]["side"] == "sell"
    trigger = call(exchange, "create_trigger_order")[1]
    assert trigger["side"] == "buy"
    assert trigger["trigger_price"] == pytest.approx(102.0)


def test_cancel_sl_noop_when_no_sl_order():
    exchange = FakeExchange()
    service = OrderService(exchange)

    asyncio.run(service.cancel_sl(position=make_position(sl_order=None)))

    assert exchange.calls == []


def test_cancel_sl_cancels_existing_order():
    exchange = FakeExchange()
    service = OrderService(exchange)

    asyncio.run(service.cancel_sl(position=make_position(sl_order={"id": "sl-1"})))

    assert call(exchange, "cancel_trigger_order")[1] == "sl-1"


def test_cancel_sl_swallows_exchange_error():
    exchange = FakeExchange(fail_cancel=True)
    service = OrderService(exchange)

    asyncio.run(service.cancel_sl(position=make_position(sl_order={"id": "sl-1"})))


def test_has_balance():
    exchange = FakeExchange(balances={"USDT": 100.0})
    service = OrderService(exchange)

    assert asyncio.run(service.has_balance(coin="USDT", quantity=50.0)) is True
    assert asyncio.run(service.has_balance(coin="USDT", quantity=150.0)) is False
    assert asyncio.run(service.has_balance(coin="BTC", quantity=0.001)) is False


def test_cancel_all_passes_symbol():
    exchange = FakeExchange()
    service = OrderService(exchange)

    asyncio.run(service.cancel_all(symbol="BTC/USDT"))

    assert call(exchange, "cancel_all_orders")[1] == "BTC/USDT"
