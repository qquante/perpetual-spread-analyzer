"""
Backpack Exchange WebSocket and REST client.
Handles orderbook streaming and order execution.
"""

import asyncio
import logging
import time
import hashlib
import base64
from typing import Optional, Callable, Dict, Any, List
from decimal import Decimal

import websockets
import httpx
import orjson
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from src.config import config
from src.core.orderbook import Orderbook

logger = logging.getLogger(__name__)


class BackpackClient:
    """
    Backpack Exchange client for:
    - WebSocket orderbook streaming
    - REST API order execution
    - Account updates via private streams
    """

    def __init__(self):
        self.ws_url = config.backpack.ws_url
        self.rest_url = config.backpack.rest_url
        self.api_key = config.backpack.api_key
        self.api_secret = config.backpack.api_secret
        self.symbol = config.backpack.symbol
        
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.http_client: Optional[httpx.AsyncClient] = None
        self.orderbook = Orderbook(exchange="backpack", symbol=self.symbol)
        
        self._running = False
        self._ws_task: Optional[asyncio.Task] = None
        self._on_orderbook_update: Optional[Callable] = None
        self._on_account_update: Optional[Callable] = None
        
        # ED25519 key for signing
        self._private_key: Optional[Ed25519PrivateKey] = None
        if self.api_secret:
            self._init_signing_key()

    def _init_signing_key(self) -> None:
        """Initialize ED25519 private key for API signing."""
        try:
            # Decode base64 secret
            key_bytes = base64.b64decode(self.api_secret)
            self._private_key = Ed25519PrivateKey.from_private_bytes(key_bytes[:32])
            logger.info("Backpack signing key initialized")
        except Exception as e:
            logger.error(f"Failed to initialize signing key: {e}")

    def _sign_request(self, instruction: str, params: Dict[str, Any] = None) -> Dict[str, str]:
        """
        Sign a request using ED25519.
        
        Args:
            instruction: API instruction name
            params: Request parameters
            
        Returns:
            Headers dict with signature
        """
        timestamp = int(time.time() * 1000)
        window = 5000
        
        # Build signing string
        sign_parts = [f"instruction={instruction}"]
        
        if params:
            # Sort params alphabetically
            for key in sorted(params.keys()):
                value = params[key]
                if value is not None:
                    sign_parts.append(f"{key}={value}")
        
        sign_parts.append(f"timestamp={timestamp}")
        sign_parts.append(f"window={window}")
        
        sign_string = "&".join(sign_parts)
        
        # Sign with ED25519
        signature = self._private_key.sign(sign_string.encode())
        signature_b64 = base64.b64encode(signature).decode()
        
        return {
            "X-API-Key": self.api_key,
            "X-Signature": signature_b64,
            "X-Timestamp": str(timestamp),
            "X-Window": str(window)
        }

    async def connect(self) -> None:
        """Initialize HTTP client and WebSocket connection."""
        self.http_client = httpx.AsyncClient(
            base_url=self.rest_url,
            timeout=30.0,
            headers={"Content-Type": "application/json"}
        )
        logger.info("Backpack HTTP client initialized")

    async def disconnect(self) -> None:
        """Close all connections."""
        self._running = False
        
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        
        if self.ws:
            await self.ws.close()
        
        if self.http_client:
            await self.http_client.aclose()
        
        logger.info("Backpack client disconnected")

    # ========== REST API ==========

    async def get_orderbook_snapshot(self, symbol: str = None) -> Dict:
        """
        Fetch orderbook snapshot from REST API.
        
        Returns:
            {"bids": [[price, qty], ...], "asks": [[price, qty], ...]}
        """
        symbol = symbol or self.symbol
        
        response = await self.http_client.get(
            "/api/v1/depth",
            params={"symbol": symbol}
        )
        response.raise_for_status()
        
        data = response.json()
        logger.info(f"Fetched orderbook snapshot for {symbol}: {len(data.get('bids', []))} bids, {len(data.get('asks', []))} asks")
        
        return data

    async def place_order(
        self,
        symbol: str,
        side: str,  # "Bid" or "Ask"
        quantity: float,
        price: float = None,
        order_type: str = "Market",  # "Market", "Limit", "IOC"
        reduce_only: bool = False
    ) -> Dict:
        """
        Execute an order on Backpack.
        
        For aggressive execution (agredir o book), use:
        - order_type="Market" for immediate fill at best available
        - order_type="Limit" with postOnly=False and good price for IOC-like
        
        Args:
            symbol: Trading pair (e.g., "SOL_USDC_PERP")
            side: "Bid" (buy) or "Ask" (sell)
            quantity: Order quantity
            price: Limit price (required for Limit orders)
            order_type: "Market", "Limit"
            reduce_only: True to only reduce positions
            
        Returns:
            Order response from exchange
        """
        params = {
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "quantity": str(quantity),
        }
        
        if price is not None:
            params["price"] = str(price)
        
        if reduce_only:
            params["reduceOnly"] = True
        
        # For IOC (Immediate or Cancel) - aggressive taker
        if order_type == "IOC":
            params["orderType"] = "Limit"
            params["timeInForce"] = "IOC"
        
        # Sign request
        headers = self._sign_request("orderExecute", params)
        
        if config.trading.paper_trading:
            logger.info(f"[PAPER] Backpack order: {side} {quantity} {symbol} @ {price or 'MARKET'}")
            return {"status": "paper", "params": params}
        
        response = await self.http_client.post(
            "/api/v1/order",
            json=params,
            headers=headers
        )
        response.raise_for_status()
        
        result = response.json()
        logger.info(f"Backpack order executed: {result}")
        
        return result

    async def cancel_order(self, symbol: str, order_id: str) -> Dict:
        """Cancel an open order."""
        params = {
            "symbol": symbol,
            "orderId": order_id
        }
        
        headers = self._sign_request("orderCancel", params)
        
        response = await self.http_client.delete(
            "/api/v1/order",
            params=params,
            headers=headers
        )
        response.raise_for_status()
        
        return response.json()

    async def get_positions(self) -> List[Dict]:
        """Get current open positions."""
        headers = self._sign_request("getPositions")
        
        response = await self.http_client.get(
            "/api/v1/positions",
            headers=headers
        )
        response.raise_for_status()
        
        return response.json()

    async def get_balances(self) -> Dict:
        """Get account balances."""
        headers = self._sign_request("getBalances")
        
        response = await self.http_client.get(
            "/api/v1/capital",
            headers=headers
        )
        response.raise_for_status()
        
        return response.json()

    # ========== WebSocket Streaming ==========

    async def start_orderbook_stream(
        self,
        symbol: str = None,
        on_update: Callable = None
    ) -> None:
        """
        Start streaming orderbook updates via WebSocket.
        
        1. Connect to WebSocket
        2. Fetch REST snapshot to initialize
        3. Subscribe to depth stream for deltas
        4. Apply deltas to local orderbook
        """
        symbol = symbol or self.symbol
        self._on_orderbook_update = on_update
        self._running = True
        
        self._ws_task = asyncio.create_task(
            self._ws_loop(symbol)
        )

    async def _ws_loop(self, symbol: str) -> None:
        """WebSocket connection loop with reconnection."""
        while self._running:
            try:
                await self._connect_and_stream(symbol)
            except websockets.ConnectionClosed as e:
                logger.warning(f"Backpack WebSocket closed: {e}")
                if self._running:
                    await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Backpack WebSocket error: {e}")
                if self._running:
                    await asyncio.sleep(5)

    async def _connect_and_stream(self, symbol: str) -> None:
        """Connect and handle WebSocket messages."""
        async with websockets.connect(self.ws_url) as ws:
            self.ws = ws
            logger.info(f"Connected to Backpack WebSocket: {self.ws_url}")
            
            # 1. Get initial snapshot from REST
            snapshot = await self.get_orderbook_snapshot(symbol)
            self.orderbook.apply_snapshot(
                bids=snapshot.get("bids", []),
                asks=snapshot.get("asks", []),
                update_id=snapshot.get("lastUpdateId", 0)
            )
            
            # 2. Subscribe to depth stream
            subscribe_msg = {
                "method": "SUBSCRIBE",
                "params": [f"depth.{symbol}"]
            }
            await ws.send(orjson.dumps(subscribe_msg).decode())
            logger.info(f"Subscribed to depth.{symbol}")
            
            # 3. Process incoming messages
            async for message in ws:
                try:
                    data = orjson.loads(message)
                    await self._handle_ws_message(data)
                except Exception as e:
                    logger.error(f"Error processing message: {e}")

    async def _handle_ws_message(self, data: Dict) -> None:
        """Handle incoming WebSocket message."""
        if "stream" not in data:
            return
        
        stream = data["stream"]
        payload = data.get("data", {})
        
        if stream.startswith("depth."):
            # Orderbook update
            event_type = payload.get("e")
            
            if event_type == "depth":
                bids = payload.get("b", [])
                asks = payload.get("a", [])
                first_id = payload.get("U", 0)
                last_id = payload.get("u", 0)
                
                # Apply delta
                success = self.orderbook.apply_delta(bids, asks, first_id, last_id)
                
                if not success:
                    # Sequence gap - need to resync
                    logger.warning("Sequence gap detected, resyncing...")
                    snapshot = await self.get_orderbook_snapshot(self.symbol)
                    self.orderbook.apply_snapshot(
                        bids=snapshot.get("bids", []),
                        asks=snapshot.get("asks", []),
                        update_id=snapshot.get("lastUpdateId", 0)
                    )
                
                # Notify callback
                if self._on_orderbook_update:
                    await self._on_orderbook_update(self.orderbook)

    async def subscribe_account_updates(self, on_update: Callable) -> None:
        """
        Subscribe to private account updates (fills, positions).
        Requires authentication via signature.
        """
        self._on_account_update = on_update
        
        if not self.ws:
            logger.error("WebSocket not connected, cannot subscribe to account updates")
            return
        
        # Generate signature for subscription
        timestamp = int(time.time() * 1000)
        window = 5000
        sign_string = f"instruction=subscribe&timestamp={timestamp}&window={window}"
        
        signature = self._private_key.sign(sign_string.encode())
        signature_b64 = base64.b64encode(signature).decode()
        verifying_key = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )
        verifying_key_b64 = base64.b64encode(verifying_key).decode()
        
        subscribe_msg = {
            "method": "SUBSCRIBE",
            "params": ["account.orderUpdate"],
            "signature": [verifying_key_b64, signature_b64, str(timestamp), str(window)]
        }
        
        await self.ws.send(orjson.dumps(subscribe_msg).decode())
        logger.info("Subscribed to account.orderUpdate")


# Global client instance
backpack_client = BackpackClient()
