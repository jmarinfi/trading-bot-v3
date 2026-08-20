import asyncio
import logging
import signal
from contextlib import suppress

from src.candle_worker import candle_worker
from src.config import load_settings
from src.data.candle_store import CandleStore
from src.exchange.bitget_exchange import BitgetExchange
from src.logging_setup import setup_logging
from src.portfolio.portfolio import Portfolio
from src.strategy.lrs_strategy import LRsStrategy
from src.trade_worker import trade_worker

log = logging.getLogger(__name__)


async def main():
    setup_logging()
    settings = load_settings()

    exchange = None
    portfolio = None

    try:
        exchange = BitgetExchange(
            settings.bitget_api_key,
            settings.bitget_api_secret,
            settings.bitget_passphrase,
        )
        signal_queue = asyncio.Queue()
        candle_store = CandleStore(
            buffer_size=settings.buffer_candles_length, timeframe=settings.timeframe
        )
        strategy = LRsStrategy(symbol=settings.symbol)
        portfolio = Portfolio(
            symbol=settings.symbol,
            base_amount_position=settings.base_amount_position,
            n_bars_static_sl=settings.n_bars_static_sl,
            pct_static_sl=settings.pct_static_sl,
            pct_trailing_sl=settings.pct_trailing_sl,
            exchange=exchange,
        )

        main_task = asyncio.current_task()
        loop = asyncio.get_running_loop()

        def request_shutdown():
            log.info("[main] - Señal recibida, deteniendo workers...")
            main_task.cancel()

        loop.add_signal_handler(signal.SIGINT, request_shutdown)
        loop.add_signal_handler(signal.SIGTERM, request_shutdown)

        async with asyncio.TaskGroup() as tg:
            tg.create_task(
                candle_worker(
                    exchange=exchange,
                    strategy=strategy,
                    candle_store=candle_store,
                    signal_queue=signal_queue,
                    symbol=settings.symbol,
                    timeframe=settings.timeframe,
                    buffer_size=settings.buffer_candles_length,
                )
            )
            tg.create_task(
                trade_worker(
                    exchange=exchange,
                    portfolio=portfolio,
                    timeframe=settings.timeframe,
                    signal_queue=signal_queue,
                    symbol=settings.symbol,
                )
            )

    except asyncio.CancelledError:
        log.info("[main] - Detenido.")

    finally:
        if portfolio is not None:
            portfolio.store.close()
        if exchange is not None:
            with suppress(Exception):
                await exchange.close()


if __name__ == "__main__":
    asyncio.run(main())
