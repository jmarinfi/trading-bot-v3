import pytest

from pathlib import Path

from src.portfolio.portfolio import Portfolio
from src.portfolio.position import Position
from src.strategy.base import Signal

SYMBOL = "BTC/USDT"


def make_portfolio(tmp_path: Path) -> Portfolio:
    return Portfolio(
        symbol=SYMBOL,
        base_amount_position=0.001,
        n_bars_static_sl=10,
        pct_static_sl=0.02,
        pct_trailing_sl=0.01,
        exchange=None,
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
