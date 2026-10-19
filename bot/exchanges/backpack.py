"""
Backpack Exchange Client - Order execution and WebSocket streaming
"""

import asyncio
import time
import os
import base64
import httpx
import websockets
import orjson
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Tuple, TYPE_CHECKING
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bot.config import DEBUG
from bot.orderbook import FastOrderbook

if TYPE_CHECKING:
    from bot.main import ArbitrageBot


class BackpackClient:
    """Backpack Exchange trading client with ED25519 authentication."""
    
    def __init__(self, bot: "ArbitrageBot"):
        self.bot = bot
        self.api_key = os.getenv("BACKPACK_API_KEY", "")
        self.api_secret = os.getenv("BACKPACK_API_SECRET", "")
        self.market_specs: Dict[str, dict] = {}
    
    def _debug(self, msg: str):
        if DEBUG:
            from datetime import datetime
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"[{ts}] DBG: {msg}", flush=True)
    
    async def buy(self, symbol: str, qty: float, price: float) -> dict:
        """Place BUY order on Backpack."""
        return await self._order(symbol, "Bid", qty, price)
    
    async def sell(self, symbol: str, qty: float, price: float) -> dict:
        """Place SELL order on Backpack."""
        return await self._order(symbol, "Ask", qty, price)
    
    async def _order(self, symbol: str, side: str, qty: float, price: float) -> dict:
        """Place order on Backpack with IOC."""
        
        # Get market specs for this symbol
        specs = self.market_specs.get(symbol, {})
        tick_size = specs.get('tick_size', 0.01)
        step_size = specs.get('step_size', 1)
        min_qty = specs.get('min_qty', 0.001)
        
        # Round price to tick_size using Decimal for precision
        price_decimal = Decimal(str(price)).quantize(Decimal(str(tick_size)), rounding=ROUND_HALF_UP)
        price_rounded = float(price_decimal)
        
        # Round qty to step_size using Decimal for precision
        qty_decimal = Decimal(str(qty)).quantize(Decimal(str(step_size)), rounding=ROUND_HALF_UP)
        qty_rounded = float(qty_decimal)
        
        # Validate
        if qty_rounded < min_qty:
            raise Exception(f"BP qty too low: {qty_rounded} < {min_qty}")
        
        # Calculate decimals from tick_size
        if tick_size >= 1:
            price_decimals = 0
        else:
            price_decimals = len(str(tick_size).rstrip('0').split('.')[-1])
        
        if step_size >= 1:
            qty_decimals = 0
        else:
            qty_decimals = len(str(step_size).rstrip('0').split('.')[-1])
        
        # Format with correct decimals
        price_str = f"{price_rounded:.{price_decimals}f}"
        qty_str = f"{qty_rounded:.{qty_decimals}f}"
        
        self._debug(f"BP {side} {symbol}:")
        self._debug(f"  INPUT: qty={qty:.8f} price={price:.6f}")
        self._debug(f"  SPECS: tick={tick_size} step={step_size} min={min_qty}")
        self._debug(f"  ROUNDED: qty={qty_str} price={price_str}")
        
        timestamp = int(time.time() * 1000)
        window = 5000
        
        order_params = {
            "symbol": symbol,
            "side": side,
            "orderType": "Limit",
            "quantity": qty_str,
            "price": price_str,
            "timeInForce": "IOC",
        }
        self._debug(f"  PARAMS: {order_params}")
        
        # Build signing string
        parts = ["instruction=orderExecute"]
        for k in sorted(order_params.keys()):
            parts.append(f"{k}={order_params[k]}")
        parts.append(f"timestamp={timestamp}")
        parts.append(f"window={window}")
        signing_string = "&".join(parts)
        
        # Sign
        private_key_bytes = base64.b64decode(self.api_secret)
        private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
        signature = private_key.sign(signing_string.encode())
        signature_b64 = base64.b64encode(signature).decode()
        
        headers = {
            "X-API-Key": self.api_key,
            "X-Signature": signature_b64,
            "X-Timestamp": str(timestamp),
            "X-Window": str(window),
            "Content-Type": "application/json; charset=utf-8",
        }
        
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.post(
                "https://api.backpack.exchange/api/v1/order",
                headers=headers,
                json=order_params,
            )
            self._debug(f"  RESPONSE: status={r.status_code}")
            self._debug(f"  BODY: {r.text[:500]}")
            if r.status_code != 200:
                raise Exception(f"BP {side} failed: {r.status_code} {r.text[:200]}")
            return r.json()
    
    def get_fill_qty(self, result: dict) -> float:
        """Get actual filled quantity from order result."""
        try:
            return float(result.get("executedQuantity", 0))
        except:
            return 0
    
    async def stream(self):
        """Stream Backpack orderbook with auto-reconnection."""
        symbols = [(b, p["bp"]) for b, p in self.bot.pairs.items() if p.get("bp")]
        
        while self.bot._running:
            try:
                # PARALLEL snapshot loading
                async with httpx.AsyncClient(timeout=30) as c:
                    async def load_snapshot(base: str, sym: str):
                        try:
                            r = await c.get('https://api.backpack.exchange/api/v1/depth', 
                                            params={'symbol': sym})
                            d = r.json()
                            bids = [(float(p), float(q)) for p, q in d.get('bids', [])[:20]]
                            asks = [(float(p), float(q)) for p, q in d.get('asks', [])[:20]]
                            if base not in self.bot.bp:
                                self.bot.bp[base] = FastOrderbook()
                            self.bot.bp[base].set_full(bids, asks)
                            return True
                        except Exception as e:
                            self._debug(f"BP snapshot {base}: {e}")
                            return False
                    
                    # Load ALL snapshots in parallel
                    results = await asyncio.gather(*[load_snapshot(b, s) for b, s in symbols])
                    loaded = sum(results)
                    self.bot._log(f"BP snapshots: {loaded}/{len(symbols)} parallel")
                
                # Stream updates
                async with websockets.connect("wss://ws.backpack.exchange", ping_interval=20, ping_timeout=10) as ws:
                    for base, sym in symbols:
                        await ws.send(orjson.dumps({
                            "method": "SUBSCRIBE",
                            "params": [f"depth.{sym}"]
                        }).decode())
                    
                    self.bot._log("BP WebSocket connected")
                    
                    async for msg in ws:
                        if not self.bot._running:
                            break
                        try:
                            data = orjson.loads(msg)
                            if 'stream' in data and 'depth' in data['stream']:
                                sym = data['stream'].replace('depth.', '')
                                base = sym.split('_')[0]
                                p = data.get('data', {})
                                bids = [(float(a), float(b)) for a, b in p.get('b', [])]
                                asks = [(float(a), float(b)) for a, b in p.get('a', [])]
                                self._on_update(base, bids, asks)
                        except Exception as e:
                            self._debug(f"BP msg: {e}")
            except Exception as e:
                self.bot._error(f"BP stream error: {e}")
                if self.bot._running:
                    self.bot._log("BP reconnecting in 3s...")
                    await asyncio.sleep(3)
    
    def _on_update(self, pair: str, bids: List[Tuple[float, float]], asks: List[Tuple[float, float]]):
        """Handle orderbook update from WebSocket."""
        if pair not in self.bot.bp:
            self.bot.bp[pair] = FastOrderbook()
        self.bot.bp[pair].update(bids, asks)
        self.bot.updates += 1
        self.bot._check_and_execute(pair)
