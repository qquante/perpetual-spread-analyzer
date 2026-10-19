"""
Paradex Exchange Client - Order execution and WebSocket streaming
"""

import asyncio
import time
import os
import orjson
import websockets
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Dict, List, Tuple, TYPE_CHECKING

from bot.config import DEBUG
from bot.orderbook import FastOrderbook

if TYPE_CHECKING:
    from bot.main import ArbitrageBot


class ParadexClient:
    """Paradex Exchange trading client using paradex-py SDK."""
    
    def __init__(self, bot: "ArbitrageBot"):
        self.bot = bot
        self._client = None
        self.market_specs: Dict[str, dict] = {}
    
    def _debug(self, msg: str):
        if DEBUG:
            from datetime import datetime
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"[{ts}] DBG: {msg}", flush=True)
    
    def init(self):
        """Initialize Paradex SDK client."""
        from clients.paradex_trading import ParadexClient as ParadexSDK
        
        try:
            self._client = ParadexSDK()
            self.bot._log("Paradex client initialized")
        except Exception as e:
            self.bot._error(f"Paradex init failed: {e}")
            self._client = None
    
    async def buy(self, symbol: str, qty: float, price: float) -> dict:
        """Place BUY order on Paradex."""
        if not self._client:
            raise Exception("Paradex client not initialized")
        
        # Get market specs
        specs = self.market_specs.get(symbol, {})
        tick_size = specs.get('tick_size', 0.0001)
        step_size = specs.get('step_size', 1)  # Default to 1 for safety
        min_qty = specs.get('min_qty', 1)
        
        # Round price to tick_size using Decimal for precision
        price_decimal = Decimal(str(price)).quantize(Decimal(str(tick_size)), rounding=ROUND_HALF_UP)
        price_rounded = float(price_decimal)
        
        # Round qty to step_size using Decimal for precision
        qty_decimal = Decimal(str(qty)).quantize(Decimal(str(step_size)), rounding=ROUND_DOWN)
        qty_rounded = float(qty_decimal)
        
        # Ensure min qty
        if qty_rounded < min_qty:
            qty_rounded = min_qty
        
        self._debug(f"PX BUY {symbol}:")
        self._debug(f"  INPUT: qty={qty:.8f} price={price:.6f}")
        self._debug(f"  SPECS: tick={tick_size} step={step_size} min={min_qty}")
        self._debug(f"  ROUNDED: qty={qty_rounded} price={price_rounded:.6f}")
        
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._client.place_limit_order(
                market=symbol,
                side="BUY",
                size=qty_rounded,
                price=price_rounded,
                instruction="IOC",
            )
        )
        self._debug(f"  RESPONSE: {result}")
        return result
    
    async def sell(self, symbol: str, qty: float, price: float) -> dict:
        """Place SELL order on Paradex."""
        if not self._client:
            raise Exception("Paradex client not initialized")
        
        # Get market specs
        specs = self.market_specs.get(symbol, {})
        tick_size = specs.get('tick_size', 0.0001)
        step_size = specs.get('step_size', 1)  # Default to 1 for safety
        min_qty = specs.get('min_qty', 1)
        
        # Round price to tick_size using Decimal for precision
        price_decimal = Decimal(str(price)).quantize(Decimal(str(tick_size)), rounding=ROUND_HALF_UP)
        price_rounded = float(price_decimal)
        
        # Round qty to step_size using Decimal for precision
        qty_decimal = Decimal(str(qty)).quantize(Decimal(str(step_size)), rounding=ROUND_DOWN)
        qty_rounded = float(qty_decimal)
        
        # Ensure min qty
        if qty_rounded < min_qty:
            qty_rounded = min_qty
        
        self._debug(f"PX SELL {symbol}:")
        self._debug(f"  INPUT: qty={qty:.8f} price={price:.6f}")
        self._debug(f"  SPECS: tick={tick_size} step={step_size} min={min_qty}")
        self._debug(f"  ROUNDED: qty={qty_rounded} price={price_rounded:.6f}")
        
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._client.place_limit_order(
                market=symbol,
                side="SELL",
                size=qty_rounded,
                price=price_rounded,
                instruction="IOC",
            )
        )
        self._debug(f"  RESPONSE: {result}")
        return result
    
    def get_fill_qty(self, result: dict) -> float:
        """Get actual filled quantity from order result."""
        try:
            size = float(result.get("size", 0))
            remaining = float(result.get("remaining_size", size))
            filled = size - remaining
            return filled if filled > 0 else 0
        except:
            return 0
    
    async def stream(self):
        """Stream Paradex orderbook with auto-reconnection."""
        symbols = [(b, p["px"]) for b, p in self.bot.pairs.items() if p.get("px")]
        
        while self.bot._running:
            try:
                async with websockets.connect("wss://ws.api.prod.paradex.trade/v1", ping_interval=20, ping_timeout=10) as ws:
                    for i, (base, sym) in enumerate(symbols):
                        # Use 50ms for faster updates
                        await ws.send(orjson.dumps({
                            "jsonrpc": "2.0",
                            "method": "subscribe",
                            "params": {"channel": f"order_book.{sym}.snapshot@15@50ms"},
                            "id": i + 1
                        }).decode())
                    
                    self.bot._log(f"PX WebSocket connected ({len(symbols)}) @ 50ms")
                    
                    async for msg in ws:
                        if not self.bot._running:
                            break
                        try:
                            data = orjson.loads(msg)
                            if "params" in data and data.get("method") == "subscription":
                                ch = data["params"].get("channel", "")
                                bd = data["params"].get("data", {})
                                if "order_book." in ch:
                                    sym = ch.split(".")[1]
                                    base = sym.split("-")[0]
                                    
                                    # Check if snapshot or delta
                                    update_type = bd.get("update_type", "d")
                                    
                                    # Process ALL update types
                                    bids = []
                                    asks = []
                                    
                                    # Inserts (new levels)
                                    for e in bd.get("inserts", []):
                                        price = float(e["price"])
                                        size = float(e["size"])
                                        if e.get("side") == "BUY":
                                            bids.append((price, size))
                                        else:
                                            asks.append((price, size))
                                    
                                    # Updates (changed levels)
                                    for e in bd.get("updates", []):
                                        price = float(e["price"])
                                        size = float(e["size"])
                                        if e.get("side") == "BUY":
                                            bids.append((price, size))
                                        else:
                                            asks.append((price, size))
                                    
                                    # Deletes (removed levels - size 0)
                                    for e in bd.get("deletes", []):
                                        price = float(e["price"])
                                        if e.get("side") == "BUY":
                                            bids.append((price, 0))
                                        else:
                                            asks.append((price, 0))
                                    
                                    if bids or asks:
                                        if update_type == "s":
                                            # SNAPSHOT: replace entire orderbook
                                            if base not in self.bot.px:
                                                self.bot.px[base] = FastOrderbook()
                                            self.bot.px[base].set_full(bids, asks)
                                            self.bot.updates += 1
                                            self.bot._check_and_execute(base)
                                        else:
                                            # DELTA: update incrementally
                                            self._on_update(base, bids, asks)
                        except Exception as e:
                            self._debug(f"PX msg: {e}")
            except Exception as e:
                self.bot._error(f"PX stream error: {e}")
                if self.bot._running:
                    self.bot._log("PX reconnecting in 3s...")
                    await asyncio.sleep(3)
    
    def _on_update(self, pair: str, bids: List[Tuple[float, float]], asks: List[Tuple[float, float]]):
        """Handle orderbook update from WebSocket."""
        if pair not in self.bot.px:
            self.bot.px[pair] = FastOrderbook()
        self.bot.px[pair].update(bids, asks)
        self.bot.updates += 1
        self.bot._check_and_execute(pair)
