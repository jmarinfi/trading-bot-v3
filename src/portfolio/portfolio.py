import logging
from pathlib import Path
from typing import Any, Literal

import ccxt
from ccxt.base.types import Order, Trade

from src.data.candle_store import TIMEFRAME_MS
from src.exchange.bitget_exchange import BitgetExchange
from src.portfolio.position import Position
from src.portfolio.position_store import PositionStore
from src.strategy.base import Signal

log = logging.getLogger(__name__)


def calculate_dust_value(price: float) -> float:
    return 1 / price * 0.1


class Portfolio:
    def __init__(
        self,
        symbol: str,
        base_amount_position: float,
        n_bars_static_sl: int,
        pct_static_sl: float,
        pct_trailing_sl: float,
        exchange: BitgetExchange,
        db_path: Path = Path("data/positions.db"),
    ):
        self.symbol = symbol
        arr_symbol = symbol.split("/")
        self.quote = arr_symbol[1]
        self.base = arr_symbol[0]
        self.base_amount_position = base_amount_position
        self.n_bars_static_sl = n_bars_static_sl
        self.pct_static_sl = pct_static_sl
        self.pct_trailing_sl = pct_trailing_sl
        self.exchange = exchange

        db_path = Path(db_path)
        self.store = PositionStore(db_path=db_path)

    def find_open(
        self, positions: list[Position], symbol: str, side: Literal["long", "short"]
    ) -> Position | None:
        """Posición abierta para el símbolo y lado dados (o None)."""
        return next(
            (
                p
                for p in positions
                if p.symbol == symbol and p.side == side and p.is_open
            ),
            None,
        )

    def filled_amount(self, position: Position) -> float:
        """Cantidad total llenada de apertura de la posición."""
        return sum(float(fill["amount"]) for fill in position.open_fills)

    def find_by_open_order(
        self, positions: list[Position], order_id: str
    ) -> Position | None:
        """Posición cuya orden de apertura tiene ese id (o None)."""
        return next((p for p in positions if p.open_order["id"] == order_id), None)

    def find_by_exit_order(
        self, positions: list[Position], order_id: str
    ) -> Position | None:
        """Posición con una orden de salida (close/SL/TP) con ese id (o None)."""
        return next(
            (
                p
                for p in positions
                if (p.close_order and p.close_order["id"] == order_id)
                or (p.sl_order and p.sl_order["id"] == order_id)
                or (p.tp_order and p.tp_order["id"] == order_id)
            ),
            None,
        )

    async def _cancel_sl_order(self, position: Position) -> None:
        if position.sl_order is None:
            return
        try:
            response = await self.exchange.cancel_trigger_order(
                order_id=position.sl_order["id"]
            )
            log.debug("cancel_trigger_order:\n%s", response)
        except ccxt.ExchangeError as exc:
            log.warning("cancel_trigger_order falló (%s)", exc)

    async def _there_is_coin(self, coin: str, quantity: float) -> bool:
        balances = await self.exchange.fetch_balance()
        free = balances["free"].get(coin, None)
        log.info("free %s = %s", coin, free)
        return free is not None and free >= quantity

    async def on_trade(self, trades: list[Trade]):
        """Se ejecuta a la llegada de cada trade y ajusta las posiciones."""

        if not trades:
            return

        opened_positions = self.store.get_open()

        for trade in trades:
            order_trade = trade["order"]
            price_trade = trade["price"]

            position = self.find_by_open_order(
                positions=opened_positions, order_id=order_trade
            )
            if position is not None:
                position.open_fills.append(trade)

            position = self.find_by_exit_order(
                positions=opened_positions, order_id=order_trade
            )
            if position is not None:
                position.close_fills.append(trade)
                sum_open_fills = sum([float(f["amount"]) for f in position.open_fills])
                sum_close_fills = sum(
                    [float(f["amount"]) for f in position.close_fills]
                )
                if abs(sum_open_fills - sum_close_fills) < calculate_dust_value(
                    price=price_trade
                ):
                    await self._cancel_sl_order(position=position)
                    position.is_open = False

        for position in opened_positions:
            self.store.save(position)

    async def on_candle(
        self, signals: list[Signal], last_row: dict[str, Any], timeframe: str
    ):
        """Se ejecuta al cierrre de cada candle."""

        cancelled_orders = await self.exchange.cancel_all_orders(symbol=self.symbol)
        log.debug("cancel_all_orders:\n%s", cancelled_orders)

        opened_positions = self.store.get_open()

        for position in opened_positions:
            # Cerrar posiciones cuya orden de apertura no se ha ejecutado
            open_fills = position.open_fills
            if not open_fills:
                await self._cancel_sl_order(position=position)
                position.is_open = False
                continue

            # Ajustar SL
            current_price = float(last_row["close"])
            sl_trailing_price = (
                current_price - current_price * self.pct_trailing_sl
                if position.side == "long"
                else current_price + current_price * self.pct_trailing_sl
            )
            candles_elapsed = (
                int(last_row["open_ts"]) - int(position.open_signal.data["open_ts"])
            ) / TIMEFRAME_MS[timeframe]
            if candles_elapsed >= self.n_bars_static_sl:
                await self._cancel_sl_order(position=position)
                response = await self.exchange.create_trigger_order(
                    symbol=position.symbol,
                    side="sell" if position.side == "long" else "buy",
                    trigger_price=sl_trailing_price,
                    order_type="market",
                    amount=self.filled_amount(position=position),
                )
                log.debug("create_trigger_order SL:\n%s", response)
                position.sl_order = Order(id=response["data"]["orderId"])

            # Abrir orden limit para la salida TP
            exit_price = last_row.get("exit_long_price", None)
            exit_signal = next(
                (
                    s
                    for s in signals
                    if s.symbol == position.symbol and s.type == "exit_long"
                ),
                None,
            )
            if (
                exit_price is not None
                and exit_price != 0
                and exit_signal is None
                and position.side == "long"
            ):
                response = await self.exchange.create_limit_order(
                    symbol=position.symbol,
                    side="sell",
                    amount=self.filled_amount(position=position),
                    price=exit_price,
                )
                position.tp_order = response
                log.debug("create_limit_order TP:\n%s", response)
            exit_price = last_row.get("exit_short_price", None)
            exit_signal = next(
                (
                    s
                    for s in signals
                    if s.symbol == position.symbol and s.type == "exit_short"
                ),
                None,
            )
            if (
                exit_price is not None
                and exit_price != 0
                and exit_signal is None
                and position.side == "short"
            ):
                response = await self.exchange.create_limit_order(
                    symbol=position.symbol,
                    side="buy",
                    amount=self.filled_amount(position=position),
                    price=exit_price,
                )
                position.tp_order = response
                log.debug("create_limit_order TP:\n%s", response)

        if not signals:
            return

        for signal in signals:
            signal_arr = signal.symbol.split("/")
            base = signal_arr[0]
            quote = signal_arr[1]
            if signal.type == "enter_long":
                position = self.find_open(
                    positions=opened_positions, symbol=signal.symbol, side="long"
                )
                if position is None and self._there_is_coin(
                    quote, self.base_amount_position * float(signal.data["close"])
                ):
                    open_price = float(signal.data["close"])
                    entry_order = await self.exchange.create_limit_order(
                        symbol=signal.symbol,
                        side="buy",
                        amount=self.base_amount_position,
                        price=open_price,
                    )
                    log.debug("create_limit_order enter_long:\n%s", entry_order)
                    sl_static_price = open_price - open_price * self.pct_static_sl
                    response = await self.exchange.create_trigger_order(
                        symbol=signal.symbol,
                        side="sell",
                        trigger_price=sl_static_price,
                        order_type="market",
                        amount=self.base_amount_position,
                    )
                    log.debug("create_trigger_order sl_long:\n%s", response)
                    sl_order = Order(id=response["data"]["orderId"])
                    position = Position(
                        symbol=signal.symbol,
                        side="long",
                        open_signal=signal,
                        open_order=entry_order,
                        sl_order=sl_order,
                        is_open=True,
                    )
                    opened_positions.append(position)
                    self.store.save(position=position)

            if signal.type == "enter_short":
                position = self.find_open(
                    positions=opened_positions, symbol=signal.symbol, side="short"
                )
                if position is None and self._there_is_coin(
                    base, self.base_amount_position
                ):
                    open_price = float(signal.data["close"])
                    entry_order = await self.exchange.create_limit_order(
                        symbol=signal.symbol,
                        side="sell",
                        amount=self.base_amount_position,
                        price=signal.data["close"],
                    )
                    log.debug("create_limit_order enter_short:\n%s", entry_order)
                    sl_static_price = open_price + open_price * self.pct_static_sl
                    response = await self.exchange.create_trigger_order(
                        symbol=signal.symbol,
                        side="buy",
                        trigger_price=sl_static_price,
                        order_type="market",
                        amount=self.base_amount_position,
                    )
                    log.debug("create_trigger_order sl_short:\n%s", response)
                    sl_order = Order(id=response["data"]["orderId"])
                    position = Position(
                        symbol=signal.symbol,
                        side="short",
                        open_signal=signal,
                        open_order=entry_order,
                        sl_order=sl_order,
                        is_open=True,
                    )
                    opened_positions.append(position)
                    self.store.save(position=position)

            if signal.type == "exit_long":
                position = self.find_open(
                    positions=opened_positions, symbol=signal.symbol, side="long"
                )
                if position is not None:
                    amount = self.filled_amount(position=position)
                    response = await self.exchange.create_market_order(
                        symbol=signal.symbol,
                        side="sell",
                        amount=amount,
                        price=signal.data["close"],
                    )
                    position.close_order = response
                    log.debug("create_market_order close_order:\n%s", response)

            if signal.type == "exit_short":
                position = self.find_open(
                    positions=opened_positions, symbol=signal.symbol, side="short"
                )
                if position is not None:
                    amount = self.filled_amount(position=position)
                    response = await self.exchange.create_market_order(
                        symbol=signal.symbol,
                        side="buy",
                        amount=amount,
                        price=signal.data["close"],
                    )
                    position.close_order = response
                    log.debug("create_market_order close_order:\n%s", response)

        for position in opened_positions:
            self.store.save(position)
