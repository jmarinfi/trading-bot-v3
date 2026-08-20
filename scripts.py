import asyncio
import logging

from src.config import config
from src.exchange.bitget_exchange import BitgetExchange

logger = logging.getLogger(__name__)
logging.basicConfig(filename="test.log", encoding="utf-8", level=logging.INFO)


async def test_market_buy_order(exchange: BitgetExchange):
    try:
        response = await exchange.create_market_order(
            symbol="BTC/USDT", side="buy", amount=0.000031, price=63470.01
        )
        logger.info(f"---Response create_market_order---\n{response}\n")

        while True:
            await asyncio.sleep(5)
            trades = await exchange.fetch_trades("BTC/USDT", limit=5)
            if not trades:
                continue
            logger.info("---Response fetch_trades---")
            for trade in trades:
                logger.info(f"\n{trade}")
            await exchange.close()
            break
    except Exception as e:
        logger.info(f"Exception:\n{str(e)}")
        await exchange.close()


async def test_market_sell_order(exchange: BitgetExchange):
    try:
        balances = await exchange.fetch_balance()
        logger.info(f"---Response fetch_balance---\n{balances}")

        response = await exchange.create_market_order(
            symbol="BTC/USDT", side="sell", amount=0.000062
        )
        logger.info(f"---Response create_market_order---\n{response}\n")

        while True:
            await asyncio.sleep(5)
            trades = await exchange.fetch_trades("BTC/USDT", limit=5)
            if not trades:
                continue
            logger.info("---Response fetch_trades---")
            for trade in trades:
                logger.info(f"\n{trade}")
            await exchange.close()
            break
    except Exception as e:
        logger.info(f"Exception:\n{str(e)}")
        await exchange.close()


async def test_limit_buy_order_and_trigger_order(exchange: BitgetExchange):
    response = await exchange.create_limit_order(
        symbol="BTC/USDT", side="buy", amount=0.000031, price=61000.00
    )
    logger.debug(f"---Response create_market_order---\n{response}\n")

    try:
        response = await exchange.create_trigger_order(
            symbol="BTCUSDT",
            side="sell",
            trigger_price=60000.00,
            order_type="market",
            amount=0.000031,
        )
        logger.debug(f"---Response create_market_order---\n{response}\n")
    except Exception as e:
        print(str(e))
        await exchange.close()

    await exchange.close()


async def test_sell_trigger_order(exchange: BitgetExchange):
    try:
        response = await exchange.create_trigger_order(
            symbol="BTCUSDT",
            side="sell",
            trigger_price=60000.00,
            order_type="market",
            amount=0.000031,
        )
        logger.info(f"---Response create_trigger_order---\n{response}\n")
    except Exception as e:
        print(str(e))
    finally:
        await exchange.close()


async def test_buy_trigger_order(exchange: BitgetExchange):
    try:
        response = await exchange.create_trigger_order(
            symbol="BTCUSDT",
            side="buy",
            trigger_price=65000.00,
            order_type="market",
            amount=0.000031,
        )
        logger.info(f"---Response create_trigger_order---\n{response}\n")
    except Exception as e:
        print(str(e))
    finally:
        await exchange.close()


async def test_cancel_orders(exchange: BitgetExchange):
    # Las órdenes trigger no se eliminan con este método
    response = await exchange.cancel_all_orders("BTC/USDT")
    logger.debug(f"---Response cancel_orders---\n{response}\n")

    await exchange.close()


async def test_cancel_trigger_order(exchange: BitgetExchange):
    try:
        response = await exchange.cancel_trigger_order(order_id="1471839729868595200")
        logger.debug(f"---Response test_cancel_trigger_order---\n{response}\n")
    except Exception as e:
        print(str(e))
    finally:
        await exchange.close()


exchange = BitgetExchange(
    config.get("BITGET_API_KEY"),
    config.get("BITGET_API_SECRET"),
    config.get("BITGET_PASSPHRASE"),
)
asyncio.run(test_buy_trigger_order(exchange=exchange))
