from typing import Literal

import ccxt.pro as ccxtpro


class BitgetExchange:
    def __init__(self, api_key: str, secret: str, password: str):
        self._exchange = ccxtpro.bitget(
            {"apiKey": api_key, "secret": secret, "password": password}
        )

    async def watch_ohlcv(self, symbol: str, timeframe: str):
        return await self._exchange.watch_ohlcv(symbol, timeframe)

    async def watch_my_trades(self, symbol: str, limit: int):
        return await self._exchange.watch_my_trades(symbol=symbol, limit=limit)

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int):
        return await self._exchange.fetch_ohlcv(symbol, timeframe, limit=limit)

    async def fetch_balance(self):
        return await self._exchange.fetch_balance()

    async def fetch_trades(self, symbol: str, limit: int):
        return await self._exchange.fetch_my_trades(symbol=symbol, limit=limit)

    async def fetch_opened_orders(self, symbol: str):
        return await self._exchange.fetch_open_orders(symbol=symbol)

    async def cancel_all_orders(self, symbol: str | None = None):
        return await self._exchange.cancel_all_orders(symbol=symbol)

    async def create_market_order(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        amount: float,
        price: float | None = None,
    ):
        return await self._exchange.create_order(
            symbol=symbol, type="market", side=side, amount=amount, price=price
        )

    async def create_limit_order(
        self, symbol: str, side: Literal["buy", "sell"], amount: float, price: float
    ):
        return await self._exchange.create_order(
            symbol=symbol, type="limit", side=side, amount=amount, price=price
        )

    async def create_trigger_order(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        trigger_price: float,
        order_type: Literal["limit", "market"],
        amount: float,
        execute_price: float | None = None,
    ):
        symbol = symbol.split("/")
        base = symbol[0]
        quote = symbol[1]
        return await self._exchange.privateSpotPostV2SpotTradePlacePlanOrder(
            {
                "symbol": f"{base}{quote}",
                "side": side,
                "triggerPrice": trigger_price,
                "orderType": order_type,
                "executePrice": execute_price,
                "planType": "amount",
                "size": amount,
                "triggerType": "fill_price",
            }
        )

    async def cancel_trigger_order(self, order_id: str):
        return await self._exchange.privateSpotPostV2SpotTradeCancelPlanOrder(
            {"orderId": order_id}
        )

    async def close(self):
        await self._exchange.close()
