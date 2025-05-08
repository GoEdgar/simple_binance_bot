import asyncio
import json
import hmac
import hashlib
import time
import urllib.parse
from abc import ABC, abstractmethod
from collections import defaultdict
from types import TracebackType
from typing import Any, Self
import aiohttp

import websockets
from websockets import ClientConnection

from utils import get_logger

BINANCE_WS_BASE = "wss://stream.testnet.binance.vision/ws"
BINANCE_REST_BASE = "https://testnet.binance.vision/api"


class Exchange(ABC):
    @abstractmethod
    def get_event(self, event_type: str):
        """Wait and return specific event from websocket"""

    @abstractmethod
    async def market_buy(self, instrument: str, amount: float) -> dict:
        """Create market order to buy"""

    @abstractmethod
    async def market_sell(self, instrument: str, amount: float) -> dict:
        """Create market order to sell"""


class Binance(Exchange):
    def __init__(self, instrument: str, api_key: str, api_secret: str) -> None:
        self._instrument = instrument
        self._api_key = api_key
        self._api_secret = api_secret
        self._market_data_websocket: ClientConnection | None = None
        self._user_stream_websocket: ClientConnection | None = None
        self._listen_messages_task: asyncio.Task | None = None
        self._event_storage: dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)
        self._user_stream_listen_key: str | None = None
        self._user_stream_task: asyncio.Task | None = None
        self._keep_alive_task: asyncio.Task | None = None
        self._logger = get_logger(__name__)

    async def _ws_connect(self):
        """Connect to WS streams"""
        self._market_data_websocket = await websockets.connect(BINANCE_WS_BASE)
        await self._start_user_data_stream()

    async def _ws_close(self):
        """Close all websockets and cancel related tasks"""
        if self._market_data_websocket:
            await self._market_data_websocket.close()

        if self._user_stream_websocket:
            await self._user_stream_websocket.close()

        if self._keep_alive_task:
            self._keep_alive_task.cancel()
            try:
                await self._keep_alive_task
            except asyncio.CancelledError:
                pass

    async def _subscribe_to_streams(self) -> None:
        """Subscribe to required channels"""
        stream_instrument = self._instrument.lower()
        subscribe_payload = {
            "method": "SUBSCRIBE",
            "params": [f"{stream_instrument}@bookTicker"],
            "id": 1,
        }
        await self._market_data_websocket.send(json.dumps(subscribe_payload))

    async def _start_user_data_stream(self) -> None:
        """Start user data stream to get private updates (trades, balances)"""
        listen_key = await self._get_listen_key()
        self._user_stream_listen_key = listen_key

        # Connect to user data stream
        self._user_stream_websocket = await websockets.connect(
            f"{BINANCE_WS_BASE}/{listen_key}"
        )
        self._user_stream_task = asyncio.ensure_future(
            self._listen_messages(self._user_stream_websocket)
        )

        # Start keepalive task for the listen key
        self._keep_alive_task = asyncio.ensure_future(self._keep_listen_key_alive())

    async def _get_listen_key(self) -> str:
        """Get listen key for user data stream"""
        endpoint = "/v3/userDataStream"

        headers = {"X-MBX-APIKEY": self._api_key}

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{BINANCE_REST_BASE}{endpoint}", headers=headers
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"Failed to get listen key: {error_text}")
                data = await response.json()
                return data["listenKey"]

    async def _keep_listen_key_alive(self) -> None:
        """Keep listen key alive by sending keepalive requests every 30 minutes"""
        while True:
            await asyncio.sleep(30 * 60)  # 30 minutes

            if not self._user_stream_listen_key:
                break

            endpoint = "/v3/userDataStream"
            headers = {"X-MBX-APIKEY": self._api_key}
            params = {"listenKey": self._user_stream_listen_key}

            async with aiohttp.ClientSession() as session:
                async with session.put(
                    f"{BINANCE_REST_BASE}{endpoint}", headers=headers, params=params
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        print(f"Failed to keep listen key alive: {error_text}")

    def _handle_best_price(self, data: dict):
        """Handle price update events (price updates)"""
        event_type = "price_update"
        self._event_storage[event_type].put_nowait(
            {
                "event_type": event_type,
                "instrument": data["s"],
                "bid_price": float(data["b"]),
                "ask_price": float(data["a"]),
                "timestamp": int(time.time() * 1000),
            }
        )

    def _handle_account_update(self, data: dict):
        """Handle account update events (balance updates)"""
        balances = []
        for balance in data["B"]:
            if (
                float(balance["f"]) > 0 or float(balance["l"]) > 0
            ):  # Only include non-zero balances
                balances.append(
                    {
                        "asset": balance["a"],
                        "free": float(balance["f"]),
                        "locked": float(balance["l"]),
                    }
                )

        event_type = "balance_update"
        self._event_storage[event_type].put_nowait(
            {
                "event_type": event_type,
                "balances": balances,
                "timestamp": data["E"],
            }
        )

    def _handle_order_update(self, data: dict):
        """Handle order update events"""
        event_type = "order_update"
        self._event_storage[event_type].put_nowait(
            {
                "event_type": event_type,
                "symbol": data["s"],
                "client_order_id": data["c"],
                "side": data["S"],
                "order_type": data["o"],
                "time_in_force": data["f"],
                "quantity": float(data["q"]),
                "price": float(data["p"]),
                "execution_type": data["x"],
                "order_status": data["X"],
                "order_id": data["i"],
                "order_time": data["T"],
                "trade_id": data.get("t", None),
                "filled_quantity": float(data.get("l", 0)),
                "last_price": float(data.get("L", 0)),
                "commission": float(data.get("n", 0)),
                "commission_asset": data.get("N", ""),
                "timestamp": data["E"],
            }
        )

    def _handle_message(self, data: dict) -> None:
        if "id" in data:
            # subscription metadata
            return
        if "e" not in data:
            # bookTicker goes without event name
            self._handle_best_price(data)
        elif data["e"] == "outboundAccountPosition":
            # Balance update
            self._handle_account_update(data)
        elif data["e"] == "executionReport":
            # Order/Trade update
            self._handle_order_update(data)

    async def _listen_messages(self, websocket: ClientConnection) -> None:
        while True:
            raw_data = await websocket.recv()
            data = json.loads(raw_data)
            self._handle_message(data)

    async def __aenter__(self) -> Self:
        """Connect and subscribe to required data"""
        await self._ws_connect()
        await self._subscribe_to_streams()
        self._listen_messages_task = asyncio.ensure_future(
            self._listen_messages(self._market_data_websocket)
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Shutdown procedure"""
        await self._cancel_all_orders()
        self._logger.debug("Cancelled all orders")

        if self._listen_messages_task:
            self._listen_messages_task.cancel()
            try:
                await self._listen_messages_task
            except asyncio.CancelledError:
                pass

        if self._user_stream_task:
            self._user_stream_task.cancel()
            try:
                await self._user_stream_task
            except asyncio.CancelledError:
                pass

        # Disconnecting on testnet freezes
        # await self._ws_close()

    def _generate_signature(self, query_string: str) -> str:
        """Generate HMAC signature for API request"""
        return hmac.new(
            self._api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def _send_signed_request(
        self, method: str, endpoint: str, params: dict[str, Any] = None
    ) -> dict:
        """Send signed request to Binance API"""
        if params is None:
            params = {}

        params["timestamp"] = int(time.time() * 1000)
        query_string = urllib.parse.urlencode(params)

        signature = self._generate_signature(query_string)

        params["signature"] = signature

        headers = {"X-MBX-APIKEY": self._api_key}

        url = f"{BINANCE_REST_BASE}{endpoint}"

        async with aiohttp.ClientSession() as session:
            methods = {
                "GET": session.get,
                "POST": session.post,
                "DELETE": session.delete,
            }
            if method not in methods:
                raise ValueError(f"Unsupported HTTP method: {method}")
            method_func = methods[method]

            async with method_func(url, params=params, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(
                        f"Request failed with status {response.status}: {error_text}"
                    )
                return await response.json()

    async def _cancel_all_orders(self) -> None:
        """Cancel all opened orders"""
        endpoint = "/v3/openOrders"
        params = {"symbol": self._instrument}

        try:
            await self._send_signed_request("DELETE", endpoint, params)
        except Exception:
            # "Unknown order sent" is being ignored if there aren't any opened orders
            pass

    async def get_event(self, event_type: str) -> dict:
        """Await specific event and return it"""
        return await self._event_storage[event_type].get()

    async def market_buy(self, instrument: str, amount: float) -> dict:
        """Create market order to buy"""
        endpoint = "/v3/order"
        params = {
            "symbol": instrument,
            "side": "BUY",
            "type": "MARKET",
            "quantity": amount,
        }

        response = await self._send_signed_request("POST", endpoint, params)
        return response

    async def market_sell(self, instrument: str, amount: float) -> dict:
        """Create market order to sell"""
        endpoint = "/v3/order"
        params = {
            "symbol": instrument,
            "side": "SELL",
            "type": "MARKET",
            "quantity": amount,
        }

        response = await self._send_signed_request("POST", endpoint, params)
        return response
