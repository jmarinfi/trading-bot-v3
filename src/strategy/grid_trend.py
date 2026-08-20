import talib

from src.strategy.base import BaseStrategy


class GridTrendStrategy(BaseStrategy):
    def __init__(self, symbol):
        super().__init__(symbol)
        self.tp_pct = 0.005
        self.sl_pct = 0.004

    def populate_indicators(self):
        self.data["rsi"] = talib.RSI(self.data["close"].values)
        self.data["fast_lr_slope"] = talib.LINEARREG_SLOPE(self.data["close"].values)
        self.data["slow_lr_slope"] = talib.LINEARREG_SLOPE(
            self.data["close"].values, timeperiod=140
        )

        self.data["exit_long_price"] = (
            self.data["close"] + self.data["close"] * self.tp_pct
        )
        self.data["exit_short_price"] = (
            self.data["close"] - self.data["close"] * self.tp_pct
        )

    def populate_entry(self):

        # Entry long
        self.data.loc[
            (
                (self.data["fast_lr_slope"] > 0)
                & (self.data["fast_lr_slope"] > self.data["slow_lr_slope"])
                & (self.data["rsi"] < 80)
                & (self.data["volume"] > 0)
            ),
            "enter_long",
        ] = 1

        # Entry short
        self.data.loc[
            (
                (self.data["fast_lr_slope"] < 0)
                & (self.data["fast_lr_slope"] < self.data["slow_lr_slope"])
                & (self.data["rsi"] > 20)
                & (self.data["volume"] > 0)
            ),
            "enter_short",
        ] = 1

    def populate_exit(self):

        # Exit long
        self.data.loc[
            (
                (self.data["close"] > self.data["exit_long_price"].shift(1))
                | (
                    self.data["close"]
                    < self.data["close"].shift(1)
                    - self.data["close"].shift(1) * self.sl_pct
                )
            ),
            "exit_long",
        ] = 1

        # Exit short
        self.data.loc[
            (
                (self.data["close"] < self.data["exit_short_price"].shift(1))
                | (
                    self.data["close"]
                    > self.data["close"].shift(1)
                    + self.data["close"].shift(1) * self.sl_pct
                )
            ),
            "exit_short",
        ] = 1
