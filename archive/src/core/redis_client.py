"""
Redis client for orderbook storage and pub/sub messaging.
"""

import asyncio
import logging
from typing import Optional, Callable, Dict, Any
import redis.asyncio as redis
import orjson

from src.config import config

logger = logging.getLogger(__name__)


class RedisClient:
    """
    Async Redis client for:
    - Orderbook storage (Hash)
    - Best bid/ask cache (String)
    - Account updates pub/sub (Pub/Sub)
    - Trade history (List)
    """

    def __init__(self):
        self.pool: Optional[redis.ConnectionPool] = None
        self.client: Optional[redis.Redis] = None
        self.pubsub: Optional[redis.client.PubSub] = None
        self._pubsub_task: Optional[asyncio.Task] = None
        self._subscribers: Dict[str, Callable] = {}

    async def connect(self) -> None:
        """Initialize Redis connection pool."""
        self.pool = redis.ConnectionPool(
            host=config.redis.host,
            port=config.redis.port,
            db=config.redis.db,
            password=config.redis.password,
            decode_responses=True,
            max_connections=20
        )
        self.client = redis.Redis(connection_pool=self.pool)
        
        # Test connection
        await self.client.ping()
        logger.info(f"Connected to Redis at {config.redis.host}:{config.redis.port}")

    async def disconnect(self) -> None:
        """Close Redis connections."""
        if self._pubsub_task:
            self._pubsub_task.cancel()
            try:
                await self._pubsub_task
            except asyncio.CancelledError:
                pass
        
        if self.pubsub:
            await self.pubsub.close()
        
        if self.client:
            await self.client.close()
        
        if self.pool:
            await self.pool.disconnect()
        
        logger.info("Disconnected from Redis")

    # ========== Orderbook Storage ==========

    async def update_orderbook(
        self,
        exchange: str,
        symbol: str,
        side: str,  # "bids" or "asks"
        price: str,
        quantity: str
    ) -> None:
        """
        Update a single price level in the orderbook.
        If quantity is "0", remove the price level.
        """
        key = f"{exchange}:{symbol}:{side}"
        
        if float(quantity) == 0:
            await self.client.hdel(key, price)
        else:
            await self.client.hset(key, price, quantity)

    async def set_orderbook_side(
        self,
        exchange: str,
        symbol: str,
        side: str,
        levels: Dict[str, str]
    ) -> None:
        """
        Replace entire orderbook side with new data (for snapshots).
        """
        key = f"{exchange}:{symbol}:{side}"
        
        # Clear existing data
        await self.client.delete(key)
        
        # Set new data if not empty
        if levels:
            await self.client.hset(key, mapping=levels)

    async def get_orderbook_side(
        self,
        exchange: str,
        symbol: str,
        side: str
    ) -> Dict[str, str]:
        """Get all price levels for one side of the orderbook."""
        key = f"{exchange}:{symbol}:{side}"
        return await self.client.hgetall(key)

    async def set_best_price(
        self,
        exchange: str,
        symbol: str,
        side: str,  # "bid" or "ask"
        price: str
    ) -> None:
        """Update cached best bid or ask price."""
        key = f"{exchange}:{symbol}:best_{side}"
        await self.client.set(key, price)

    async def get_best_prices(
        self,
        exchange: str,
        symbol: str
    ) -> Dict[str, Optional[str]]:
        """Get cached best bid and ask prices."""
        bid_key = f"{exchange}:{symbol}:best_bid"
        ask_key = f"{exchange}:{symbol}:best_ask"
        
        bid, ask = await asyncio.gather(
            self.client.get(bid_key),
            self.client.get(ask_key)
        )
        
        return {"best_bid": bid, "best_ask": ask}

    # ========== Pub/Sub for Account Updates ==========

    async def publish(self, channel: str, message: Dict[str, Any]) -> None:
        """Publish a message to a channel."""
        await self.client.publish(channel, orjson.dumps(message).decode())

    async def subscribe(self, channel: str, callback: Callable) -> None:
        """Subscribe to a channel with a callback handler."""
        if not self.pubsub:
            self.pubsub = self.client.pubsub()
        
        await self.pubsub.subscribe(channel)
        self._subscribers[channel] = callback
        
        # Start listener if not running
        if not self._pubsub_task or self._pubsub_task.done():
            self._pubsub_task = asyncio.create_task(self._pubsub_listener())
        
        logger.info(f"Subscribed to channel: {channel}")

    async def _pubsub_listener(self) -> None:
        """Background task to process pub/sub messages."""
        try:
            async for message in self.pubsub.listen():
                if message["type"] == "message":
                    channel = message["channel"]
                    data = orjson.loads(message["data"])
                    
                    if channel in self._subscribers:
                        callback = self._subscribers[channel]
                        if asyncio.iscoroutinefunction(callback):
                            await callback(channel, data)
                        else:
                            callback(channel, data)
        except asyncio.CancelledError:
            logger.info("Pub/Sub listener cancelled")
        except Exception as e:
            logger.error(f"Pub/Sub listener error: {e}")

    # ========== Trade History ==========

    async def log_trade(self, trade: Dict[str, Any]) -> None:
        """Add a trade to history."""
        await self.client.lpush("trades:history", orjson.dumps(trade).decode())
        # Keep only last 1000 trades
        await self.client.ltrim("trades:history", 0, 999)

    async def get_recent_trades(self, count: int = 10) -> list:
        """Get recent trades from history."""
        trades = await self.client.lrange("trades:history", 0, count - 1)
        return [orjson.loads(t) for t in trades]

    # ========== Utility ==========

    async def clear_exchange_data(self, exchange: str, symbol: str) -> None:
        """Clear all data for an exchange/symbol pair."""
        pattern = f"{exchange}:{symbol}:*"
        keys = []
        async for key in self.client.scan_iter(match=pattern):
            keys.append(key)
        
        if keys:
            await self.client.delete(*keys)
            logger.info(f"Cleared {len(keys)} keys for {exchange}:{symbol}")


# Global Redis client instance
redis_client = RedisClient()
