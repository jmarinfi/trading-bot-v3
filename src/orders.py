import logging
from typing import Literal

import ccxt
from ccxt.base.types import Order

from src.exchange.bitget_exchange import BitgetExchange
from src.portfolio.position import Position

log = logging.getLogger(__name__)


class OrderService:
    """I/O de órdenes con el exchange, con nombres de dominio."""

    def __init__(self, exchange: BitgetExchange):
        self.exchange = exchange

    async def cancel_all(self, symbol: str) -> None:
        response = await self.exchange.cancel_all_orders(symbol=symbol)
        log.debug("cancel_all_orders:\n%s", response)

    async def cancel_sl(self, position: Position) -> None:
        """Cancela la orden SL de la posición."""
        if position.sl_order is None:
            return
        try:
            response = await self.exchange.cancel_trigger_order(
                order_id=position.sl_order["id"]
            )
            log.debug("cancel_trigger_order:\n%s", response)
        except ccxt.ExchangeError as exc:
            log.warning("cancel_trigger_order falló (%s)", exc)

    async def has_balance(self, coin: str, quantity: float) -> bool:
        """Comprueba saldo libre suficiente de la moneda dada."""
        balances = await self.exchange.fetch_balance()
        free = balances["free"].get(coin, None)
        log.info("free %s = %s", coin, free)
        return free is not None and free >= quantity

    async def place_sl(
        self, position: Position, trigger_price: float, amount: float
    ) -> Order:
        """Coloca la orden SL (trigger) de la posición."""
        response = await self.exchange.create_trigger_order(
            symbol=position.symbol,
            side="sell" if position.side == "long" else "buy",
            trigger_price=trigger_price,
            order_type="market",
            amount=amount,
        )
        log.debug("create_trigger_order SL:\n%s", response)
        sl_order = Order(id=response["data"]["orderId"])
        log.info(
            "SL colocado (%s %s trigger=%s qty=%s)",
            position.symbol,
            position.side,
            trigger_price,
            amount,
        )
        return sl_order

    async def place_tp_limit(
        self, position: Position, amount: float, price: float
    ) -> Order:
        """Coloca la orden límite de salida TP de la posición."""
        response = await self.exchange.create_limit_order(
            symbol=position.symbol,
            side="sell" if position.side == "long" else "buy",
            amount=amount,
            price=price,
        )
        log.debug("create_limit_order TP:\n%s", response)
        log.info(
            "TP límite colocado (%s %s price=%s qty=%s)",
            position.symbol,
            position.side,
            price,
            amount,
        )
        return response

    async def open_position(
        self,
        symbol: str,
        side: Literal["long", "short"],
        price: float,
        amount: float,
        pct_static_sl: float,
    ) -> tuple[Order, Order]:
        """Abre una posición: entrada límite + SL estático. Devuelve (entrada, sl)."""
        entry_order = await self.exchange.create_limit_order(
            symbol=symbol,
            side="buy" if side == "long" else "sell",
            amount=amount,
            price=price,
        )
        log.debug("create_limit_order entrada %s:\n%s", side, entry_order)
        log.info(
            "entrada %s colocada (%s qty=%s price=%s)", side, symbol, amount, price
        )

        sl_static_price = (
            price - price * pct_static_sl
            if side == "long"
            else price + price * pct_static_sl
        )
        response = await self.exchange.create_trigger_order(
            symbol=symbol,
            side="sell" if side == "long" else "buy",
            trigger_price=sl_static_price,
            order_type="market",
            amount=amount,
        )
        log.debug("create_trigger_order sl_%s:\n%s", side, response)
        sl_order = Order(id=response["data"]["orderId"])
        log.info("SL estático colocado (%s, order_id=%s)", symbol, sl_order["id"])
        return entry_order, sl_order

    async def market_close(
        self, position: Position, amount: float, price: float
    ) -> Order:
        """Cierra la posición a mercado."""
        response = await self.exchange.create_market_order(
            symbol=position.symbol,
            side="sell" if position.side == "long" else "buy",
            amount=amount,
            price=price,
        )
        log.debug("create_market_order close_order:\n%s", response)
        log.info(
            "cierre a mercado (%s %s qty=%s)", position.symbol, position.side, amount
        )
        return response
