"""
Backpack Exchange Client - Full Orderbook Depth
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
    bids: List[OrderbookLevel] = field(default_factory=list)  # Sorted desc by price
    asks: List[OrderbookLevel] = field(default_factory=list)  # Sorted asc by price
    
    def best_bid(self) -> Tuple[float, float]:
        return (self.bids[0].price, self.bids[0].qty) if self.bids else (0, 0)
    
    def best_ask(self) -> Tuple[float, float]:
        return (self.asks[0].price, self.asks[0].qty) if self.asks else (0, 0)
    
    def total_bid_liquidity(self, max_levels: int = 10) -> float:
        """Total bid quantity across top N levels."""
        return sum(b.qty for b in self.bids[:max_levels])
    
    def total_ask_liquidity(self, max_levels: int = 10) -> float:
        """Total ask quantity across top N levels."""
        return sum(a.qty for a in self.asks[:max_levels])
    
    def depth_stats(self) -> dict:
        """Get depth statistics."""
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


class BackpackClient:
    """Backpack Exchange client with full orderbook depth."""
    
    FEE = 0.0002  # 0.02% taker fee
    
    def __init__(self):
        self.orderbooks: Dict[str, Orderbook] = {}
        self._running = False
        self._update_count = 0
    
    async def get_symbols(self) -> List[str]:
        """Get all perpetual symbols."""
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get('https://api.backpack.exchange/api/v1/markets')
            return [m['symbol'] for m in r.json() if 'PERP' in m['symbol']]
    
    async def fetch_snapshot(self, symbol: str) -> None:
        """Fetch full orderbook snapshot via REST API."""
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get('https://api.backpack.exchange/api/v1/depth',
                                params={'symbol': symbol})
            d = r.json()
            
            bids = [OrderbookLevel(float(p), float(q)) for p, q in d.get('bids', [])]
            asks = [OrderbookLevel(float(p), float(q)) for p, q in d.get('asks', [])]
            
            self.orderbooks[symbol] = Orderbook(
                symbol=symbol,
                bids=sorted(bids, key=lambda x: x.price, reverse=True),
                asks=sorted(asks, key=lambda x: x.price),
            )
    
    def apply_delta(self, symbol: str, bid_updates: list, ask_updates: list) -> None:
        """Apply incremental updates to orderbook."""
        if symbol not in self.orderbooks:
            return
        
        book = self.orderbooks[symbol]
        
        # Process bid updates
        for price_str, qty_str in bid_updates:
            price, qty = float(price_str), float(qty_str)
            # Remove existing level at this price
            book.bids = [b for b in book.bids if b.price != price]
            # Add new level if qty > 0
            if qty > 0:
                book.bids.append(OrderbookLevel(price, qty))
        
        # Process ask updates
        for price_str, qty_str in ask_updates:
            price, qty = float(price_str), float(qty_str)
            book.asks = [a for a in book.asks if a.price != price]
            if qty > 0:
                book.asks.append(OrderbookLevel(price, qty))
        
        # Re-sort
        book.bids = sorted(book.bids, key=lambda x: x.price, reverse=True)
        book.asks = sorted(book.asks, key=lambda x: x.price)
    
    async def start(self, symbols: List[str] = None, max_symbols: int = 10):
        """Start streaming orderbooks."""
        self._running = True
        
        if not symbols:
            console.print("[bold]BACKPACK CLIENT - Full Depth[/]")
            console.print("Fetching symbols...")
            all_symbols = await self.get_symbols()
            symbols = all_symbols[:max_symbols]
            console.print(f"Using {len(symbols)} symbols: {symbols[:5]}...")
        
        # Fetch initial snapshots
        console.print("\nFetching orderbook snapshots...")
        for symbol in symbols:
            try:
                await self.fetch_snapshot(symbol)
                stats = self.orderbooks[symbol].depth_stats()
                console.print(f"  ✓ {symbol}: {stats.get('bid_levels', 0)} bids, {stats.get('ask_levels', 0)} asks")
            except Exception as e:
                console.print(f"  ✗ {symbol}: {e}")
        
        # Stream updates
        console.print("\nStreaming updates...")
        
        try:
            async with websockets.connect("wss://ws.backpack.exchange") as ws:
                for symbol in symbols:
                    await ws.send(orjson.dumps({
                        "method": "SUBSCRIBE",
                        "params": [f"depth.{symbol}"]
                    }).decode())
                
                with Live(self._build_table(), refresh_per_second=2) as live:
                    async for msg in ws:
                        if not self._running:
                            break
                        try:
                            data = orjson.loads(msg)
                            if 'stream' in data and 'depth' in data['stream']:
                                symbol = data['stream'].replace('depth.', '')
                                payload = data.get('data', {})
                                self.apply_delta(
                                    symbol,
                                    payload.get('b', []),
                                    payload.get('a', [])
                                )
                                self._update_count += 1
                                live.update(self._build_table())
                        except:
                            pass
        except Exception as e:
            console.print(f"Error: {e}")
    
    def _build_table(self) -> Table:
        """Build display table with depth info."""
        table = Table(title=f"BACKPACK - Updates: {self._update_count} | Full Depth")
        table.add_column("Symbol", width=14)
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
    client = BackpackClient()
    try:
        await client.start(max_symbols=10)
    except KeyboardInterrupt:
        client.stop()
        console.print("\nStopped")


if __name__ == "__main__":
    asyncio.run(main())
