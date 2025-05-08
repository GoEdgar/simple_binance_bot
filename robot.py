import asyncio
import json

import aiofiles

from exchange import Exchange
from utils import get_logger


class Robot:
    def __init__(
        self,
        exchange: Exchange,
        instrument: str,
        order_amount: float,
        take_profit_percent: float,
        stop_loss_percent: float,
        target_timeout_seconds: float,
        trade_cycle_interval_seconds: float,
    ) -> None:
        self._exchange = exchange
        self._instrument = instrument
        self._order_amount = order_amount
        self._take_profit_percent = take_profit_percent
        self._stop_loss_percent = -stop_loss_percent
        self._target_timeout_seconds = target_timeout_seconds
        self._trade_cycle_interval_seconds = trade_cycle_interval_seconds
        self._report_worker_task: asyncio.Task | None = None
        self._logger = get_logger(__name__)

    async def _report_worker(self) -> None:
        """Listen to user balances and execution report updates, write them as json to file."""
        while True:
            try:
                events, _ = await asyncio.wait(
                    (
                        asyncio.ensure_future(
                            self._exchange.get_event("balance_update")
                        ),
                        asyncio.ensure_future(self._exchange.get_event("order_update")),
                    ),
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in events:
                    event_data = task.result()
                    async with aiofiles.open("user_events.log", "a") as f:
                        json_str = json.dumps(event_data)
                        await f.write(json_str + "\n")
            except Exception as exc:
                self._logger.error(
                    f"Robot report worker error: {exc.__class__.__name__}, args: {exc.args}"
                )

    async def run(self) -> None:
        """Start entrypoint of robot"""
        self._logger.info(f"Bot started for instrument {self._instrument}")
        self._report_worker_task = asyncio.ensure_future(self._report_worker())

        try:
            while True:
                try:
                    await self.cycle()
                    await asyncio.sleep(self._trade_cycle_interval_seconds)
                except Exception as exc:
                    self._logger.error(
                        f"Robot cycle error: {exc.__class__.__name__}, args: {exc.args}"
                    )
                    await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass
        finally:
            if self._report_worker_task and not self._report_worker_task.done():
                self._report_worker_task.cancel()
                try:
                    await self._report_worker_task
                except asyncio.CancelledError:
                    pass
            self._logger.info(f"Bot stopped for instrument {self._instrument}")

    async def open_position(self) -> tuple[float, float]:
        """Open position by market order"""
        order = await self._exchange.market_buy(self._instrument, self._order_amount)
        quote = float(order["cummulativeQuoteQty"])
        price = quote / self._order_amount
        self._logger.debug(f"Position opened at price {price}, amount {quote}")
        return price, quote

    async def close_position(self) -> tuple[float, float]:
        """Close position by market order"""
        order = await self._exchange.market_sell(self._instrument, self._order_amount)
        quote = float(order["cummulativeQuoteQty"])
        price = quote / self._order_amount
        self._logger.debug(f"Position closed at price {price}, amount {quote}")
        return price, quote

    async def cycle(self) -> None:
        """Main robot cycle algorythm"""
        self._logger.debug("===== New Cycle =====")

        # 1. Open position
        enter_price, enter_quote = await self.open_position()
        try:
            # 2. Wait within timeout any target price movements
            async with asyncio.timeout(self._target_timeout_seconds):
                while True:
                    event = await self._exchange.get_event("price_update")
                    current_price = event["bid_price"]
                    price_diff_percent = (1 - (enter_price / current_price)) * 100

                    # 3. Stop to wait movements when target price hit
                    if price_diff_percent >= self._take_profit_percent:
                        reason = "take_profit"
                        break
                    if price_diff_percent < self._stop_loss_percent:
                        reason = "stop_loss"
                        break
        except asyncio.TimeoutError:
            # 4. If target didn't hit, we finish waiting
            reason = "timeout"

        # 5. Close position, calculate PNL
        exit_price, exit_quote = await self.close_position()
        cycle_pnl = exit_quote - enter_quote
        profit_percent = (exit_quote / enter_quote - 1) * 100

        self._logger.info(
            f"Cycle completed ({reason}): PnL={cycle_pnl:.2f} ({profit_percent:.2f}%)"
        )
