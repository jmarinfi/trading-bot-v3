import asyncio
import logging

import ccxt

from src.data.candle_store import CandleStore
from src.exchange.bitget_exchange import BitgetExchange
from src.strategy.base import BaseStrategy

log = logging.getLogger(__name__)

RETRY_DELAY = 5.0
RETRY_MAX_DELAY = 30.0


async def candle_worker(
    exchange: BitgetExchange,
    candle_store: CandleStore,
    strategy: BaseStrategy,
    signal_queue: asyncio.Queue,
    symbol: str,
    timeframe: str,
    buffer_size: int,
):
    backoff = RETRY_DELAY

    while True:
        try:
            candles = await exchange.watch_ohlcv(symbol=symbol, timeframe=timeframe)
            log.debug("candle:\n%s", candles)

            if candle_store.add_candles(candles=candles):
                log.warning("Hueco detectado, reconstruyendo buffer...")
                candles = await exchange.fetch_ohlcv(
                    symbol=symbol, timeframe=timeframe, limit=buffer_size
                )
                candle_store.add_candles(candles=candles)

            if candle_store.is_new:
                signals, last_row = strategy.run(candle_store.to_dataframe())
                await signal_queue.put((signals, last_row))

        except asyncio.CancelledError:
            log.info("Detenido.")
            return

        except ccxt.NetworkError as exc:
            log.warning("Error de red (%s). Reintentando en %ss...", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RETRY_MAX_DELAY)
            continue

        except ccxt.ExchangeError as exc:
            log.warning(
                "Error de Exchange (%s). Reintentando en %ss...", exc, RETRY_DELAY
            )
            await asyncio.sleep(RETRY_DELAY)
            continue

        backoff = RETRY_DELAY
