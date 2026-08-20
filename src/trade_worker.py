import asyncio
import logging
from typing import Any

import ccxt

from src.exchange.bitget_exchange import BitgetExchange
from src.portfolio.portfolio import Portfolio
from src.strategy.base import Signal

log = logging.getLogger(__name__)

RETRY_DELAY = 5.0
RETRY_MAX_DELAY = 30.0

WATCH_MY_TRADES_LIMIT = 6


async def _watch_my_trades_loop(
    exchange: BitgetExchange, portfolio: Portfolio, symbol: str
):
    backoff = RETRY_DELAY

    while True:
        try:
            trades = await exchange.watch_my_trades(
                symbol=symbol, limit=WATCH_MY_TRADES_LIMIT
            )
            log.debug("trades:\n%s", trades)
            await portfolio.on_trade(trades=trades)
            log.info("%d fill(s) procesado(s)", len(trades))

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


async def _consume_signals_loop(
    signal_queue: asyncio.Queue[tuple[list[Signal], dict[str, Any]]],
    portfolio: Portfolio,
    timeframe: str,
):
    while True:
        try:
            signals, last_row = await signal_queue.get()
            log.debug("signal:\n%s", signals)
            log.debug("last_row:\n%s", last_row)
            await portfolio.on_candle(
                signals=signals, last_row=last_row, timeframe=timeframe
            )
            log.info("Vela procesada con %d señal(es)", len(signals))

        except asyncio.CancelledError:
            log.info("Detenido.")
            return

        except ccxt.NetworkError as exc:
            log.warning("Error de red en on_candle (%s). Vela descartada.", exc)
            continue

        except ccxt.ExchangeError as exc:
            log.warning("Error de Exchange en on_candle (%s). Vela descartada.", exc)
            continue


async def trade_worker(
    exchange: BitgetExchange,
    portfolio: Portfolio,
    timeframe: str,
    signal_queue: asyncio.Queue,
    symbol: str,
):
    async with asyncio.TaskGroup() as tg:
        tg.create_task(
            _watch_my_trades_loop(exchange=exchange, portfolio=portfolio, symbol=symbol)
        )
        tg.create_task(
            _consume_signals_loop(
                signal_queue=signal_queue, portfolio=portfolio, timeframe=timeframe
            )
        )
