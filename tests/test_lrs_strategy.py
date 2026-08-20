import pandas as pd

from src.strategy.lrs_strategy import LRsStrategy

SYMBOL = "BTC/USDT"


def make_dataframe(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame(
        {
            "open_ts": [1_700_000_000_000 + i * 900_000 for i in range(n)],
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [10.0] * n,
        }
    )


def alternating_phase(start: float, up: float, down: float, bars: int) -> list[float]:
    """Serie geométrica alternando subida/bajada por vela, desde `start`."""
    price = start
    closes = []
    for i in range(bars):
        price *= up if i % 2 == 0 else down
        closes.append(price)
    return closes


def signal_types(signals: list) -> set[str]:
    return {signal.type for signal in signals}


def test_uptrend_enters_long():
    closes = alternating_phase(100.0, up=1.012, down=0.995, bars=300)
    signals, _ = LRsStrategy(symbol=SYMBOL).run(make_dataframe(closes))

    types = signal_types(signals)
    assert "enter_long" in types
    assert "enter_short" not in types
    assert "exit_long" not in types
    assert "exit_short" in types


def test_downtrend_after_rise_enters_short():
    closes = alternating_phase(100.0, up=1.003, down=0.9985, bars=240)
    closes += alternating_phase(closes[-1], up=0.985, down=1.008, bars=60)
    signals, _ = LRsStrategy(symbol=SYMBOL).run(make_dataframe(closes))

    types = signal_types(signals)
    assert "enter_short" in types
    assert "enter_long" not in types
    assert "exit_long" in types
    assert "exit_short" not in types


def test_flat_market_no_enter_signals():
    signals, _ = LRsStrategy(symbol=SYMBOL).run(make_dataframe([100.0] * 300))

    types = signal_types(signals)
    assert "enter_long" not in types
    assert "enter_short" not in types


def test_short_history_below_slow_window():
    # Con 50 velas, slow_lr_slope (157) es NaN: sin entradas, pero la
    # pendiente rápida ya es válida y positiva → solo exit_short
    closes = alternating_phase(100.0, up=1.012, down=0.995, bars=50)
    signals, _ = LRsStrategy(symbol=SYMBOL).run(make_dataframe(closes))

    assert signal_types(signals) == {"exit_short"}


def test_empty_dataframe_returns_empty():
    empty = pd.DataFrame(columns=["open_ts", "open", "high", "low", "close", "volume"])
    assert LRsStrategy(symbol=SYMBOL).run(empty) == ([], {})
