import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Signal:
    type: Literal["enter_long", "enter_short", "exit_long", "exit_short"]
    symbol: str
    data: dict[str, Any]


class BaseStrategy(ABC):
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.data: pd.DataFrame
        self.signals: list[Signal] = []
        self.name_enter_long_col = "enter_long"
        self.name_enter_short_col = "enter_short"
        self.name_exit_long_col = "exit_long"
        self.name_exit_short_col = "exit_short"

    @abstractmethod
    def populate_indicators(self) -> None: ...

    @abstractmethod
    def populate_entry(self) -> None: ...

    @abstractmethod
    def populate_exit(self) -> None: ...

    def run(self, data: pd.DataFrame) -> tuple[list[Signal], dict[str, Any]]:
        log.debug("data:\n%s", data)
        self.data = data
        if self.data.empty:
            log.info("Dataframe vacío")
            return [], {}

        self.populate_indicators()
        self.populate_entry()
        self.populate_exit()
        log.debug("data populated:\n%s", self.data)

        self.signals = []
        last_row = {col: self.data[col].iloc[-1] for col in self.data.columns}
        log.debug("last_row:\n%s", last_row)

        if last_row.get(self.name_enter_long_col, 0) == 1:
            self.signals.append(Signal("enter_long", self.symbol, last_row))
        if last_row.get(self.name_enter_short_col, 0) == 1:
            self.signals.append(Signal("enter_short", self.symbol, last_row))
        if last_row.get(self.name_exit_long_col, 0) == 1:
            self.signals.append(Signal("exit_long", self.symbol, last_row))
        if last_row.get(self.name_exit_short_col, 0) == 1:
            self.signals.append(Signal("exit_short", self.symbol, last_row))
        log.debug("signals:\n%s", self.signals)

        return self.signals, last_row
