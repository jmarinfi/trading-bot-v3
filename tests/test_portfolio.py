import asyncio

import pytest

from pathlib import Path

from src.portfolio.portfolio import Portfolio
from src.portfolio.position import Position
from src.strategy.base import Signal

SYMBOL = "BTC/USDT"


class FakeOrderService:
    """Registra las llamadas de órdenes sin tocar la red."""

    def __init__(self, balances: dict | None = None):
        self.balances = balances or {}
        self.entries: list[tuple] = []
        self.closed: list[tuple] = []
        self.cancelled: list = []
        self.cancelled_all: list[str] = []

    async def cancel_all(self, symbol: str) -> None:
        self.cancelled_all.append(symbol)

    async def cancel_sl(self, position: Position) -> None:
        self.cancelled.append(position)

    async def has_balance(self, coin: str, quantity: float) -> bool:
        free = self.balances.get(coin)
        return free is not None and free >= quantity

    async def place_sl(self, position, trigger_price, amount):
        return {"id": "sl-2"}

    async def place_tp_limit(self, position, amount, price):
        return {"id": "tp-1"}

    async def open_position(self, symbol, side, price, amount, pct_static_sl):
        self.entries.append((symbol, side, price, amount, pct_static_sl))
        return {"id": "entry-1"}, {"id": "sl-1"}

    async def market_close(self, position, amount, price):
        self.closed.append((position.symbol, position.side, amount, price))
        return {"id": "close-1"}


def make_portfolio(tmp_path: Path, orders=None) -> Portfolio:
    return Portfolio(
        symbol=SYMBOL,
        base_amount_position=0.001,
        n_bars_static_sl=10,
        pct_static_sl=0.02,
        pct_trailing_sl=0.01,
        orders=orders,
        db_path=tmp_path / "test.db",
    )


def make_position(
    symbol: str = SYMBOL,
    side: str = "long",
    is_open: bool = True,
    open_fills: list | None = None,
) -> Position:
    return Position(
        symbol=symbol,
        side=side,
        open_signal=Signal(type="enter_long", symbol=symbol, data={"close": 100.0}),
        open_order={"id": "order-1"},
        open_fills=open_fills or [],
        is_open=is_open,
    )


def test_find_open_returns_matching_open_position(tmp_path):
    portfolio = make_portfolio(tmp_path)
    target = make_position()
    other = make_position(symbol="ETH/USDT", side="short")

    assert portfolio.find_open([target, other], SYMBOL, "long") is target


def test_find_open_returns_none_when_no_match(tmp_path):
    portfolio = make_portfolio(tmp_path)
    closed = make_position(is_open=False)
    short = make_position(side="short")
    other_symbol = make_position(symbol="ETH/USDT")

    assert portfolio.find_open([closed, short, other_symbol], SYMBOL, "long") is None


def test_find_open_empty_list(tmp_path):
    portfolio = make_portfolio(tmp_path)

    assert portfolio.find_open([], SYMBOL, "long") is None


def test_filled_amount_sums_open_fills(tmp_path):
    portfolio = make_portfolio(tmp_path)
    fills = [{"id": "t1", "amount": 0.5}, {"id": "t2", "amount": 0.25}]
    position = make_position(open_fills=fills)

    assert portfolio.filled_amount(position) == 0.75


def test_filled_amount_zero_when_no_fills(tmp_path):
    portfolio = make_portfolio(tmp_path)

    assert portfolio.filled_amount(make_position()) == 0


def test_find_by_open_order_matches(tmp_path):
    portfolio = make_portfolio(tmp_path)
    position = make_position()

    assert portfolio.find_by_open_order([position], "order-1") is position
    assert portfolio.find_by_open_order([position], "no-existe") is None


@pytest.mark.parametrize("attr", ["close_order", "sl_order", "tp_order"])
def test_find_by_exit_order_matches_each_exit_order(tmp_path, attr):
    portfolio = make_portfolio(tmp_path)
    position = make_position()
    setattr(position, attr, {"id": "exit-1"})

    assert portfolio.find_by_exit_order([position], "exit-1") is position


def test_find_by_exit_order_ignores_positions_without_exit_orders(tmp_path):
    portfolio = make_portfolio(tmp_path)
    position = make_position()

    assert portfolio.find_by_exit_order([position], "order-1") is None


def test_record_open_fill_appends_new_trade(tmp_path):
    portfolio = make_portfolio(tmp_path)
    position = make_position()
    trade = {"id": "t1", "order": "order-1", "price": 100.0, "amount": 0.5}

    assert portfolio.record_open_fill(position, trade) is True
    assert position.open_fills == [trade]


def test_record_open_fill_ignores_duplicate_by_id(tmp_path):
    portfolio = make_portfolio(tmp_path)
    position = make_position(open_fills=[{"id": "t1", "amount": 0.5}])
    duplicate = {"id": "t1", "amount": 0.5}

    assert portfolio.record_open_fill(position, duplicate) is False
    assert position.open_fills == [{"id": "t1", "amount": 0.5}]


def test_record_open_fill_allows_distinct_ids(tmp_path):
    portfolio = make_portfolio(tmp_path)
    first = {"id": "t1", "order": "order-1", "price": 100.0, "amount": 0.5}
    second = {"id": "t2", "order": "order-1", "price": 101.0, "amount": 0.25}
    position = make_position(open_fills=[first])

    assert portfolio.record_open_fill(position, second) is True
    assert position.open_fills == [first, second]


def test_mark_closed_flips_is_open(tmp_path):
    portfolio = make_portfolio(tmp_path)
    position = make_position()

    portfolio.mark_closed(position)

    assert position.is_open is False


def test_record_close_fill_partial_does_not_close(tmp_path):
    portfolio = make_portfolio(tmp_path)
    position = make_position(open_fills=[{"id": "t0", "amount": 0.5}])
    trade = {"id": "c1", "order": "sl-1", "price": 100.0, "amount": 0.2}

    assert portfolio.record_close_fill(position, trade) is False
    assert position.is_open is True


def test_record_close_fill_closes_when_amounts_match(tmp_path):
    portfolio = make_portfolio(tmp_path)
    position = make_position(open_fills=[{"id": "t0", "amount": 0.5}])
    trade = {"id": "c1", "order": "sl-1", "price": 100.0, "amount": 0.5}

    assert portfolio.record_close_fill(position, trade) is True
    assert position.is_open is False


def test_record_close_fill_accumulates_until_closed(tmp_path):
    portfolio = make_portfolio(tmp_path)
    position = make_position(open_fills=[{"id": "t0", "amount": 0.5}])

    first = {"id": "c1", "order": "sl-1", "price": 100.0, "amount": 0.3}
    second = {"id": "c2", "order": "sl-1", "price": 100.0, "amount": 0.2}

    assert portfolio.record_close_fill(position, first) is False
    assert portfolio.record_close_fill(position, second) is True
    assert position.is_open is False


def test_record_close_fill_ignores_duplicate_and_no_double_close(tmp_path):
    portfolio = make_portfolio(tmp_path)
    fill = {"id": "c1", "order": "sl-1", "price": 100.0, "amount": 0.5}
    position = make_position(open_fills=[{"id": "t0", "amount": 0.5}])
    assert portfolio.record_close_fill(position, fill) is True
    assert position.is_open is False

    duplicate = {"id": "c1", "order": "sl-1", "price": 100.0, "amount": 0.5}

    assert portfolio.record_close_fill(position, duplicate) is False
    assert position.close_fills == [fill]


def test_on_candle_entry_blocked_by_insufficient_balance(tmp_path):
    orders = FakeOrderService(balances={"USDT": 0.05})
    portfolio = make_portfolio(tmp_path, orders=orders)
    signal = Signal(type="enter_long", symbol=SYMBOL, data={"close": 100.0})

    asyncio.run(
        portfolio.on_candle(
            signals=[signal], last_row={"close": 100.0}, timeframe="15m"
        )
    )

    assert orders.entries == []
    assert portfolio.store.get_open() == []


def test_on_candle_entry_creates_position_when_balance_ok(tmp_path):
    orders = FakeOrderService(balances={"USDT": 100.0})
    portfolio = make_portfolio(tmp_path, orders=orders)
    signal = Signal(type="enter_long", symbol=SYMBOL, data={"close": 100.0})

    asyncio.run(
        portfolio.on_candle(
            signals=[signal], last_row={"close": 100.0}, timeframe="15m"
        )
    )

    assert len(orders.entries) == 1
    assert orders.entries[0][1] == "long"
    positions = portfolio.store.get_open()
    assert len(positions) == 1
    assert positions[0].side == "long"
    assert positions[0].open_order["id"] == "entry-1"
    assert positions[0].sl_order["id"] == "sl-1"


def test_on_candle_entry_short_checks_base_balance(tmp_path):
    orders = FakeOrderService(balances={"BTC": 0.001})
    portfolio = make_portfolio(tmp_path, orders=orders)
    signal = Signal(type="enter_short", symbol=SYMBOL, data={"close": 100.0})

    asyncio.run(
        portfolio.on_candle(
            signals=[signal], last_row={"close": 100.0}, timeframe="15m"
        )
    )

    assert len(orders.entries) == 1
    assert orders.entries[0][1] == "short"


def test_on_candle_exit_places_market_close(tmp_path):
    orders = FakeOrderService()
    portfolio = make_portfolio(tmp_path, orders=orders)
    ts = 1_700_000_000_000
    position = Position(
        symbol=SYMBOL,
        side="long",
        open_signal=Signal(
            type="enter_long", symbol=SYMBOL, data={"close": 100.0, "open_ts": ts}
        ),
        open_order={"id": "entry-1"},
        open_fills=[{"id": "t0", "amount": 0.5}],
        sl_order={"id": "sl-1"},
        is_open=True,
    )
    portfolio.store.save(position)

    exit_signal = Signal(
        type="exit_long", symbol=SYMBOL, data={"close": 99.0, "open_ts": ts}
    )
    asyncio.run(
        portfolio.on_candle(
            signals=[exit_signal],
            last_row={"close": 99.0, "open_ts": ts},
            timeframe="15m",
        )
    )

    assert len(orders.closed) == 1
    assert orders.closed[0][1] == "long"
    assert orders.closed[0][2] == 0.5
    saved = portfolio.store.get_open()
    assert saved[0].close_order["id"] == "close-1"
