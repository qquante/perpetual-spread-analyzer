"""
Paradex Exchange Client - Full Orderbook Depth
Captures all orderbook levels, not just the top one.
"""

import asyncio
import httpx
import websockets
import orjson
from typing import Dict, List, Tuple
from dataclasses import dataclass, field
from rich.console import Console
from rich.table import Table
from rich.live import Live

console = Console()


@dataclass
class OrderbookLevel:
    price: float
    qty: float


@dataclass
class Orderbook:
    symbol: str
    bids: List[OrderbookLevel] = field(default_factory=list)
    asks: List[OrderbookLevel] = field(default_factory=list)
    
    def best_bid(self) -> Tuple[float, float]:
        return (self.bids[0].price, self.bids[0].qty) if self.bids else (0, 0)
    
    def best_ask(self) -> Tuple[float, float]:
        return (self.asks[0].price, self.asks[0].qty) if self.asks else (0, 0)
    
    def total_bid_liquidity(self, max_levels: int = 10) -> float:
        return sum(b.qty for b in self.bids[:max_levels])
    
    def total_ask_liquidity(self, max_levels: int = 10) -> float:
        return sum(a.qty for a in self.asks[:max_levels])
    
    def depth_stats(self) -> dict:
        if not self.bids or not self.asks:
            return {}
        return {
            'best_bid': self.bids[0].price,
            'best_ask': self.asks[0].price,
            'spread': (self.asks[0].price - self.bids[0].price) / self.bids[0].price * 100,
            'bid_levels': len(self.bids),
            'ask_levels': len(self.asks),
            'bid_liquidity_10': self.total_bid_liquidity(10),
            'ask_liquidity_10': self.total_ask_liquidity(10),
        }


class ParadexClient:
    """Paradex Exchange client with full orderbook depth."""
    
    FEE = 0.0000  # 0% taker fee
    
    def __init__(self):
        self.orderbooks: Dict[str, Orderbook] = {}
        self._running = False
        self._update_count = 0
    
    async def get_symbols(self) -> List[str]:
        """Get all perpetual symbols."""
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get('https://api.prod.paradex.trade/v1/markets')
            return [m['symbol'] for m in r.json()['results'] if 'PERP' in m['symbol']]
    
    def apply_snapshot(self, symbol: str, inserts: list) -> None:
        """Apply orderbook snapshot from websocket."""
        bids = []
        asks = []
        
        for entry in inserts:
            price = float(entry['price'])
            qty = float(entry['size'])
            side = entry.get('side')
            
            if side == 'BUY':
                bids.append(OrderbookLevel(price, qty))
            elif side == 'SELL':
                asks.append(OrderbookLevel(price, qty))
        
        self.orderbooks[symbol] = Orderbook(
            symbol=symbol,
            bids=sorted(bids, key=lambda x: x.price, reverse=True),
            asks=sorted(asks, key=lambda x: x.price),
        )
    
    async def start(self, symbols: List[str] = None, max_symbols: int = 10):
        """Start streaming orderbooks."""
        self._running = True
        
        if not symbols:
            console.print("[bold]PARADEX CLIENT - Full Depth[/]")
            console.print("Fetching symbols...")
            all_symbols = await self.get_symbols()
            symbols = all_symbols[:max_symbols]
            console.print(f"Using {len(symbols)} symbols: {symbols[:5]}...")
        
        # Initialize orderbooks
        for symbol in symbols:
            self.orderbooks[symbol] = Orderbook(symbol=symbol)
        
        console.print("\nStreaming orderbooks with full depth...")
        
        try:
            async with websockets.connect("wss://ws.api.prod.paradex.trade/v1") as ws:
                for i, symbol in enumerate(symbols):
                    # Request 15 levels at 100ms updates (max supported)
                    channel = f"order_book.{symbol}.snapshot@15@100ms"
                    await ws.send(orjson.dumps({
                        "jsonrpc": "2.0",
                        "method": "subscribe",
                        "params": {"channel": channel},
                        "id": i + 1
                    }).decode())
                    console.print(f"  ✓ Subscribed: {symbol}")
                
                console.print("\n[bold]Receiving data...[/]\n")
                
                with Live(self._build_table(), refresh_per_second=2) as live:
                    async for msg in ws:
                        if not self._running:
                            break
                        try:
                            data = orjson.loads(msg)
                            if "params" in data and data.get("method") == "subscription":
                                ch = data["params"].get("channel", "")
                                bd = data["params"].get("data", {})
                                
                                if "order_book." in ch:
                                    symbol = ch.split(".")[1]
                                    inserts = bd.get("inserts", [])
                                    
                                    if inserts:
                                        self.apply_snapshot(symbol, inserts)
                                        self._update_count += 1
                                        live.update(self._build_table())
                        except Exception as e:
                            pass
        except Exception as e:
            console.print(f"Error: {e}")
    
    def _build_table(self) -> Table:
        """Build display table with depth info."""
        table = Table(title=f"PARADEX - Updates: {self._update_count} | Full Depth")
        table.add_column("Symbol", width=16)
        table.add_column("Best Bid", justify="right", width=12)
        table.add_column("Best Ask", justify="right", width=12)
        table.add_column("Spread", justify="right", width=8)
        table.add_column("Bid Lvls", justify="right", width=8)
        table.add_column("Ask Lvls", justify="right", width=8)
        table.add_column("Bid Liq (10)", justify="right", width=12)
        table.add_column("Ask Liq (10)", justify="right", width=12)
        
        for symbol, book in self.orderbooks.items():
            stats = book.depth_stats()
            if not stats:
                continue
            
            table.add_row(
                symbol,
                f"${stats['best_bid']:,.2f}",
                f"${stats['best_ask']:,.2f}",
                f"{stats['spread']:.4f}%",
                str(stats['bid_levels']),
                str(stats['ask_levels']),
                f"{stats['bid_liquidity_10']:.4f}",
                f"{stats['ask_liquidity_10']:.4f}",
            )
        
        return table
    
    def stop(self):
        self._running = False


async def main():
    client = ParadexClient()
    try:
        await client.start(max_symbols=10)
    except KeyboardInterrupt:
        client.stop()
        console.print("\nStopped")


if __name__ == "__main__":
    asyncio.run(main())
