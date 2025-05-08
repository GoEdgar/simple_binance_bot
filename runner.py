import asyncio

from dotenv import dotenv_values

from exchange import Binance, Exchange
from robot import Robot


def load_config() -> dict:
    return dotenv_values(".env")


def init_exchange(config: dict) -> Exchange:
    return Binance(config["INSTRUMENT"], config["API_KEY"], config["API_SECRET"])


def init_robot(config: dict, exchange: Exchange) -> Robot:
    return Robot(
        exchange,
        config["INSTRUMENT"],
        float(config["ORDER_AMOUNT"]),
        float(config["TAKE_PROFIT_PERCENT"]),
        float(config["STOP_LOSS_PERCENT"]),
        float(config["TARGET_TIMEOUT_SECONDS"]),
        float(config["TRADE_CYCLE_INTERVAL_SECONDS"]),
    )


async def main():
    config = load_config()
    exchange = init_exchange(config)
    robot = init_robot(config, exchange)

    async with exchange:
        await robot.run()


if __name__ == "__main__":
    asyncio.run(main())
