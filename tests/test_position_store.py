from pathlib import Path

import numpy as np
import pytest

from src.portfolio.portfolio import Portfolio
from src.portfolio.position import Position
from src.portfolio.position_store import PositionStore
from src.strategy.base import Signal


def make_position(**overrides) -> Position:
    kwargs = dict(
        symbol="BTC/USDT",
        side="long",
        open_signal=Signal(
            type="entry",
            symbol="BTC/USDT",
            data={"close": np.float64(65000.5), "rsi": np.int64(28)},
        ),
        open_order={
            "id": "o1",
            "side": "buy",
            "price": 65000.0,
            "amount": 0.001,
            "info": {"orderId": "123", "state": "filled"},
        },
        open_fills=[
            {
                "id": "t1",
                "order": "o1",
                "price": 65000.1,
                "amount": 0.001,
                "fee": {"currency": "USDT", "cost": 0.065, "rate": None},
            }
        ],
    )
    kwargs.update(overrides)
    return Position(**kwargs)


@pytest.fixture
def store(tmp_path):
    store = PositionStore(tmp_path / "positions.db")
    yield store
    store.close()


def test_save_and_get_roundtrip(store):
    position = make_position()

    store.save(position)
    restored = store.get(position.id)

    assert restored.id == position.id
    assert restored.symbol == "BTC/USDT"
    assert restored.side == "long"
    assert restored.is_open is True
    assert restored.open_signal.type == "entry"
    assert restored.open_signal.data["close"] == 65000.5
    assert restored.open_signal.data["rsi"] == 28
    assert restored.open_order["id"] == "o1"
    assert restored.open_order["info"]["state"] == "filled"
    assert restored.open_fills[0]["fee"]["cost"] == 0.065
    assert restored.close_signal is None
    assert restored.close_order is None
    assert restored.tp_order is None
    assert restored.close_fills == []


def test_get_missing_id_returns_none(store):
    assert store.get(999) is None


def test_save_twice_updates_single_row(store):
    position = make_position()
    first_id = store.save(position)

    position.is_open = False
    position.close_order = {"id": "o2", "side": "sell"}
    second_id = store.save(position)

    assert second_id == first_id
    assert len(store.get_all()) == 1
    restored = store.get(first_id)
    assert restored.is_open is False
    assert restored.close_order["id"] == "o2"


def test_get_open_returns_only_open_positions(store):
    open_position = make_position()
    closed_position = make_position(is_open=False)
    store.save(open_position)
    store.save(closed_position)

    assert [p.id for p in store.get_open()] == [open_position.id]


def test_two_writers_same_database(tmp_path):
    db_path = tmp_path / "positions.db"
    store_a = PositionStore(db_path)
    store_b = PositionStore(db_path)

    position_a = make_position()
    position_b = make_position(
        open_signal=Signal(
            type="entry", symbol="BTC/USDT", data={"close": 64000.0}
        )
    )
    store_a.save(position_a)
    store_b.save(position_b)

    assert len(store_a.get_all()) == 2
    assert len(store_b.get_all()) == 2
    assert store_a.get(position_b.id).open_signal.data["close"] == 64000.0
    store_a.close()
    store_b.close()


def test_portfolio_uses_db_path(tmp_path):
    db_path = str(tmp_path / "positions.db")
    portfolio = Portfolio(
        symbol="BTC/USDT",
        base_amount_position=0.001,
        n_bars_static_sl=10,
        pct_static_sl=0.02,
        pct_trailing_sl=0.01,
        exchange=object(),
        db_path=db_path,
    )
    position = make_position()

    portfolio.store.save(position)

    assert Path(db_path).exists()
    assert portfolio.store.get(position.id) is not None
    portfolio.store.close()
