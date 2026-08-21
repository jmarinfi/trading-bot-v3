import logging
from pathlib import Path
from typing import Any, Literal

from ccxt.base.types import Trade

from src.data.candle_store import TIMEFRAME_MS
from src.orders import OrderService
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
        orders: OrderService,
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
        self.orders = orders

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

    def record_open_fill(self, position: Position, trade: Trade) -> bool:
        """Registra un fill de apertura; ignora duplicados por id.
        Devuelve True si el fill era nuevo y False si ya estaba registrado.
        """
        if any(fill["id"] == trade["id"] for fill in position.open_fills):
            return False
        position.open_fills.append(trade)
        return True

    def mark_closed(self, position: Position) -> None:
        """Marca la posición como cerrada."""
        position.is_open = False

    def record_close_fill(self, position: Position, trade: Trade) -> bool:
        """Registra un fill de cierre; ignora duplicados por id.
        Devuelve True si este fill cerró la posición.
        """
        if any(fill["id"] == trade["id"] for fill in position.close_fills):
            return False
        position.close_fills.append(trade)
        sum_open = self.filled_amount(position)
        sum_close = sum(float(fill["amount"]) for fill in position.close_fills)
        if abs(sum_open - sum_close) < calculate_dust_value(trade["price"]):
            self.mark_closed(position)
            return True
        return False

    async def on_trade(self, trades: list[Trade]):
        """Se ejecuta a la llegada de cada trade y ajusta las posiciones."""

        if not trades:
            return

        opened_positions = self.store.get_open()

        for trade in trades:
            order_trade = trade["order"]

            position = self.find_by_open_order(
                positions=opened_positions, order_id=order_trade
            )
            if position is not None and not self.record_open_fill(
                position=position, trade=trade
            ):
                log.info("fill de apertura duplicado ignorado (trade %s)", trade["id"])

            position = self.find_by_exit_order(
                positions=opened_positions, order_id=order_trade
            )
            if position is not None and self.record_close_fill(
                position=position, trade=trade
            ):
                await self.orders.cancel_sl(position=position)

        for position in opened_positions:
            self.store.save(position)

    async def on_candle(
        self, signals: list[Signal], last_row: dict[str, Any], timeframe: str
    ):
        """Se ejecuta al cierrre de cada candle."""

        await self.orders.cancel_all(symbol=self.symbol)

        opened_positions = self.store.get_open()

        for position in opened_positions:
            # Cerrar posiciones cuya orden de apertura no se ha ejecutado.
            if not position.open_fills:
                await self.orders.cancel_sl(position=position)
                self.mark_closed(position=position)
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
                await self.orders.cancel_sl(position=position)
                position.sl_order = await self.orders.place_sl(
                    position=position,
                    trigger_price=sl_trailing_price,
                    amount=self.filled_amount(position=position),
                )

            # Abrir orden limit para la salida TP
            side_word = "long" if position.side == "long" else "short"
            exit_price = last_row.get(f"exit_{side_word}_price", None)
            exit_signal = next(
                (
                    s
                    for s in signals
                    if s.symbol == position.symbol and s.type == f"exit_{side_word}"
                ),
                None,
            )
            if exit_price is not None and exit_price != 0 and exit_signal is None:
                position.tp_order = await self.orders.place_tp_limit(
                    position=position,
                    amount=self.filled_amount(position=position),
                    price=exit_price,
                )

        if not signals:
            return

        for signal in signals:
            signal_arr = signal.symbol.split("/")
            base = signal_arr[0]
            quote = signal_arr[1]

            if signal.type in ("enter_long", "enter_short"):
                side = "long" if signal.type == "enter_long" else "short"
                position = self.find_open(
                    positions=opened_positions, symbol=signal.symbol, side=side
                )
                coin = quote if side == "long" else base
                needed = (
                    self.base_amount_position * float(signal.data["close"])
                    if side == "long"
                    else self.base_amount_position
                )
                if position is None and await self.orders.has_balance(
                    coin=coin, quantity=needed
                ):
                    entry_order, sl_order = await self.orders.open_position(
                        symbol=signal.symbol,
                        side=side,
                        price=float(signal.data["close"]),
                        amount=self.base_amount_position,
                        pct_static_sl=self.pct_static_sl,
                    )
                    position = Position(
                        symbol=signal.symbol,
                        side=side,
                        open_signal=signal,
                        open_order=entry_order,
                        sl_order=sl_order,
                        is_open=True,
                    )
                    opened_positions.append(position)
                    self.store.save(position=position)

            if signal.type in ("exit_long", "exit_short"):
                side = "long" if signal.type == "exit_long" else "short"
                position = self.find_open(
                    positions=opened_positions, symbol=signal.symbol, side=side
                )
                if position is not None:
                    position.close_order = await self.orders.market_close(
                        position=position,
                        amount=self.filled_amount(position=position),
                        price=signal.data["close"],
                    )

        for position in opened_positions:
            self.store.save(position)
