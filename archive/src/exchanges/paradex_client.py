"""
Paradex Exchange client using official paradex-py SDK.
Handles orderbook streaming and order execution.
"""

import asyncio
import logging
from typing import Optional, Callable, Dict, Any, List
from decimal import Decimal

from src.config import config
from src.core.orderbook import Orderbook

logger = logging.getLogger(__name__)

# Conditional import for paradex-py
try:
    from paradex_py import Paradex
    from paradex_py.environment import Environment
    from paradex_py.common.order import OrderSide, OrderType
    PARADEX_SDK_AVAILABLE = True
except ImportError:
    PARADEX_SDK_AVAILABLE = False
    logger.warning("paradex-py SDK not installed. Install with: pip install paradex-py")


class ParadexClient:
    """
    Paradex Exchange client using official SDK for:
    - WebSocket orderbook streaming
    - REST API order execution
    - Account updates
    
    Note: Paradex has ZERO FEES for retail traders!
    """

    def __init__(self):
        self.l1_address = config.paradex.l1_address
        self.l1_private_key = config.paradex.l1_private_key
        self.symbol = config.paradex.symbol
        self.environment = config.paradex.environment
        
        self.paradex: Optional[Paradex] = None
        self.orderbook = Orderbook(exchange="paradex", symbol=self.symbol)
        
        self._running = False
        self._ws_task: Optional[asyncio.Task] = None
        self._on_orderbook_update: Optional[Callable] = None
        self._on_account_update: Optional[Callable] = None

    async def connect(self) -> None:
        """Initialize Paradex SDK client."""
        if not PARADEX_SDK_AVAILABLE:
            raise ImportError("paradex-py SDK is required. Install with: pip install paradex-py")
        
        if not self.l1_address or not self.l1_private_key:
            logger.warning("Paradex credentials not configured, running in read-only mode")
            return
        
        try:
            env = Environment.MAINNET if self.environment == "mainnet" else Environment.TESTNET
            
            self.paradex = Paradex(
                env=env,
                l1_address=self.l1_address,
                l1_private_key=self.l1_private_key
            )
            
            logger.info(f"Paradex client initialized (L2 address: {hex(self.paradex.account.l2_address)})")
        except Exception as e:
            logger.error(f"Failed to initialize Paradex client: {e}")
            raise

    async def disconnect(self) -> None:
        """Close all connections."""
        self._running = False
        
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        
        if self.paradex and hasattr(self.paradex, 'ws_client'):
            try:
                await self.paradex.ws_client.disconnect()
            except Exception:
                pass
        
        logger.info("Paradex client disconnected")

    # ========== REST API ==========

    async def get_orderbook_snapshot(self, market: str = None) -> Dict:
        """
        Fetch orderbook snapshot from REST API.
        
        Returns:
            {"bids": [[price, qty], ...], "asks": [[price, qty], ...]}
        """
        market = market or self.symbol
        
        try:
            # Use SDK's REST client
            response = await self.paradex.api_client.fetch_order_book(market=market)
            
            bids = [[str(b['price']), str(b['size'])] for b in response.get('bids', [])]
            asks = [[str(a['price']), str(a['size'])] for a in response.get('asks', [])]
            
            logger.info(f"Fetched Paradex orderbook for {market}: {len(bids)} bids, {len(asks)} asks")
            
            return {
                "bids": bids,
                "asks": asks,
                "sequence": response.get('sequence', 0)
            }
        except Exception as e:
            logger.error(f"Failed to fetch Paradex orderbook: {e}")
            return {"bids": [], "asks": []}

    async def place_order(
        self,
        market: str,
        side: str,  # "BUY" or "SELL"
        size: float,
        price: float = None,
        order_type: str = "MARKET",  # "MARKET", "LIMIT", "IOC"
        reduce_only: bool = False
    ) -> Dict:
        """
        Execute an order on Paradex.
        
        For aggressive execution (agredir o book), use:
        - order_type="MARKET" for immediate fill at best available
        - order_type="IOC" for Immediate or Cancel
        
        Args:
            market: Trading pair (e.g., "SOL-USD-PERP")
            side: "BUY" or "SELL"
            size: Order size
            price: Limit price (required for LIMIT orders)
            order_type: "MARKET", "LIMIT", "IOC"
            reduce_only: True to only reduce positions
            
        Returns:
            Order response from exchange
        """
        if not self.paradex:
            raise RuntimeError("Paradex client not initialized")
        
        if config.trading.paper_trading:
            logger.info(f"[PAPER] Paradex order: {side} {size} {market} @ {price or 'MARKET'}")
            return {"status": "paper", "market": market, "side": side, "size": size}
        
        try:
            # Map to SDK types
            order_side = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL
            
            if order_type.upper() == "MARKET":
                sdk_order_type = OrderType.MARKET
            elif order_type.upper() == "IOC":
                sdk_order_type = OrderType.LIMIT  # IOC is a time-in-force modifier
                # Note: Will need to check SDK for IOC support
            else:
                sdk_order_type = OrderType.LIMIT
            
            # Create order via SDK
            order_params = {
                "market": market,
                "side": order_side,
                "type": sdk_order_type,
                "size": str(size),
            }
            
            if price is not None:
                order_params["price"] = str(price)
            
            if reduce_only:
                order_params["reduce_only"] = True
            
            # Execute order
            result = await self.paradex.api_client.submit_order(**order_params)
            
            logger.info(f"Paradex order executed: {result}")
            return result
            
        except Exception as e:
            logger.error(f"Paradex order failed: {e}")
            raise

    async def cancel_order(self, order_id: str) -> Dict:
        """Cancel an open order."""
        if not self.paradex:
            raise RuntimeError("Paradex client not initialized")
        
        try:
            result = await self.paradex.api_client.cancel_order(order_id=order_id)
            logger.info(f"Paradex order cancelled: {order_id}")
            return result
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            raise

    async def get_positions(self) -> List[Dict]:
        """Get current open positions."""
        if not self.paradex:
            return []
        
        try:
            result = await self.paradex.api_client.fetch_positions()
            return result.get('results', [])
        except Exception as e:
            logger.error(f"Failed to fetch positions: {e}")
            return []

    async def get_balances(self) -> Dict:
        """Get account balances."""
        if not self.paradex:
            return {}
        
        try:
            result = await self.paradex.api_client.fetch_account()
            return result
        except Exception as e:
            logger.error(f"Failed to fetch balances: {e}")
            return {}

    # ========== WebSocket Streaming ==========

    async def start_orderbook_stream(
        self,
        market: str = None,
        on_update: Callable = None
    ) -> None:
        """
        Start streaming orderbook updates via WebSocket.
        
        Uses Paradex SDK's WebSocket client for:
        1. Initial orderbook snapshot
        2. Incremental updates
        """
        market = market or self.symbol
        self._on_orderbook_update = on_update
        self._running = True
        
        self._ws_task = asyncio.create_task(
            self._ws_loop(market)
        )

    async def _ws_loop(self, market: str) -> None:
        """WebSocket connection loop with reconnection."""
        while self._running:
            try:
                await self._connect_and_stream(market)
            except Exception as e:
                logger.error(f"Paradex WebSocket error: {e}")
                if self._running:
                    await asyncio.sleep(5)

    async def _connect_and_stream(self, market: str) -> None:
        """Connect and stream orderbook via SDK."""
        if not self.paradex:
            # For read-only mode, use simple WebSocket
            await self._stream_public_orderbook(market)
            return
        
        # Use SDK's WebSocket client
        await self.paradex.ws_client.connect()
        
        # Subscribe to orderbook channel
        async def on_orderbook_message(channel: str, message: Dict):
            await self._handle_orderbook_update(message)
        
        await self.paradex.ws_client.subscribe(
            channel="order_book",
            params={"market": market},
            callback=on_orderbook_message
        )
        
        logger.info(f"Subscribed to Paradex orderbook: {market}")
        
        # Keep alive
        while self._running:
            await asyncio.sleep(1)

    async def _stream_public_orderbook(self, market: str) -> None:
        """
        Stream public orderbook without authentication.
        Uses simple websocket connection.
        """
        import websockets
        import orjson
        
        # Correct production URL
        ws_url = "wss://ws.api.prod.paradex.trade/v1"
        
        async with websockets.connect(ws_url) as ws:
            logger.info(f"Connected to Paradex public WebSocket")
            
            # Subscribe to orderbook with correct channel format
            # Format: order_book.{market_symbol}.snapshot@15@{refresh_rate}
            channel = f"order_book.{market}.snapshot@15@100ms"
            subscribe_msg = {
                "jsonrpc": "2.0",
                "method": "subscribe",
                "params": {
                    "channel": channel
                },
                "id": 1
            }
            await ws.send(orjson.dumps(subscribe_msg).decode())
            logger.info(f"Subscribed to Paradex orderbook: {channel}")
            
            async for message in ws:
                if not self._running:
                    break
                try:
                    data = orjson.loads(message)
                    
                    # Handle subscription confirmation
                    if "result" in data:
                        logger.info(f"Paradex subscription confirmed: {data.get('result')}")
                        continue
                    
                    # Handle errors
                    if "error" in data:
                        logger.error(f"Paradex WebSocket error: {data.get('error')}")
                        continue
                    
                    # Handle orderbook updates
                    if "params" in data and data.get("method") == "subscription":
                        params = data["params"]
                        await self._handle_orderbook_snapshot(params.get("data", {}))
                        
                except Exception as e:
                    logger.error(f"Error processing Paradex message: {e}")

    async def _handle_orderbook_snapshot(self, data: Dict) -> None:
        """Process orderbook snapshot from WebSocket (Paradex sends snapshots, not deltas)."""
        update_type = data.get("update_type", "s")
        seq_no = data.get("seq_no", 0)
        
        # Process inserts (for snapshots and updates)
        inserts = data.get("inserts", [])
        updates = data.get("updates", [])
        deletes = data.get("deletes", [])
        
        # Combine all changes
        all_entries = inserts + updates
        bids = [[str(e["price"]), str(e["size"])] for e in all_entries if e.get("side") == "BUY"]
        asks = [[str(e["price"]), str(e["size"])] for e in all_entries if e.get("side") == "SELL"]
        
        # Handle deletes (set size to 0)
        for d in deletes:
            if d.get("side") == "BUY":
                bids.append([str(d["price"]), "0"])
            else:
                asks.append([str(d["price"]), "0"])
        
        if update_type == "s":
            # Full snapshot
            self.orderbook.apply_snapshot(bids, asks, seq_no)
        else:
            # Delta update
            self.orderbook.apply_delta(bids, asks, seq_no, seq_no)
        
        # Notify callback
        if self._on_orderbook_update:
            await self._on_orderbook_update(self.orderbook)

    async def subscribe_account_updates(self, on_update: Callable) -> None:
        """Subscribe to private account updates (fills, positions)."""
        self._on_account_update = on_update
        
        if not self.paradex:
            logger.warning("Cannot subscribe to account updates without authentication")
            return
        
        async def on_account_message(channel: str, message: Dict):
            if self._on_account_update:
                await self._on_account_update("paradex", message)
        
        await self.paradex.ws_client.subscribe(
            channel="account",
            callback=on_account_message
        )
        
        logger.info("Subscribed to Paradex account updates")


# Global client instance
paradex_client = ParadexClient()
