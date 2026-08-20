import logging

import talib

from src.strategy.base import BaseStrategy

log = logging.getLogger(__name__)


class LRsStrategy(BaseStrategy):
    def __init__(self, symbol):
        super().__init__(symbol)
        self.enter_fast_period_lr = 47
        self.enter_slow_period_lr = 157
        self.enter_overbought = 80
        self.enter_oversold = 30
        self.enter_period_rsi = 14

    def populate_indicators(self):
        self.data["rsi"] = talib.RSI(
            self.data["close"].values, timeperiod=self.enter_period_rsi
        )
        self.data["fast_lr_slope"] = talib.LINEARREG_SLOPE(
            self.data["close"].values, timeperiod=self.enter_fast_period_lr
        )
        self.data["slow_lr_slope"] = talib.LINEARREG_SLOPE(
            self.data["close"].values, timeperiod=self.enter_slow_period_lr
        )

    def populate_entry(self):

        # Enter long
        self.data.loc[
            (
                (self.data["fast_lr_slope"] > 0)
                & (self.data["fast_lr_slope"] > self.data["slow_lr_slope"])
                & (self.data["rsi"] < self.enter_overbought)
                & (self.data["volume"] > 0)
            ),
            "enter_long",
        ] = 1

        # Enter short
        self.data.loc[
            (
                (self.data["fast_lr_slope"] < 0)
                & (self.data["fast_lr_slope"] < self.data["slow_lr_slope"])
                & (self.data["rsi"] > self.enter_oversold)
                & (self.data["volume"] > 0)
            ),
            "enter_short",
        ] = 1

    def populate_exit(self):

        # Exit long
        self.data.loc[
            (
                (self.data["fast_lr_slope"] <= 0)
                | (self.data["fast_lr_slope"] <= self.data["slow_lr_slope"])
            ),
            "exit_long",
        ] = 1

        # Exit short
        self.data.loc[
            (
                (self.data["fast_lr_slope"] >= 0)
                | (self.data["fast_lr_slope"] >= self.data["slow_lr_slope"])
            ),
            "exit_short",
        ] = 1
