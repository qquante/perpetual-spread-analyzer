"""
Teste isolado: Paradex Exchange
Verifica se conseguimos receber dados do orderbook.
"""

import asyncio
import httpx
import websockets
import orjson
from rich.console import Console
from rich.table import Table
from rich.live import Live

console = Console()


class ParadexClient:
    def __init__(self):
        self.orderbooks = {}  # symbol -> {bids: [], asks: []}
        self._running = False
        self._update_count = 0
    
    async def get_symbols(self):
        """Get all perp symbols from Paradex."""
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get('https://api.prod.paradex.trade/v1/markets')
            return [m['symbol'] for m in r.json()['results'] if 'PERP' in m['symbol']]
    
    async def start(self, symbols: list = None):
        """Start streaming orderbooks."""
        self._running = True
        
        if not symbols:
            console.print("Fetching symbols...")
            symbols = await self.get_symbols()
            console.print(f"Found {len(symbols)} perp symbols")
        
        # Use only first 5 for testing
        symbols = symbols[:5]
        console.print(f"Testing with: {symbols}")
        
        # Initialize
        for s in symbols:
            self.orderbooks[s] = {'bid': None, 'ask': None}
        
        # Stream updates
        console.print("\nStreaming updates (Ctrl+C to stop)...")
        
        try:
            async with websockets.connect("wss://ws.api.prod.paradex.trade/v1") as ws:
                for i, symbol in enumerate(symbols):
                    channel = f"order_book.{symbol}.snapshot@15@100ms"
                    await ws.send(orjson.dumps({
                        "jsonrpc": "2.0",
                        "method": "subscribe",
                        "params": {"channel": channel},
                        "id": i + 1
                    }).decode())
                    console.print(f"  Subscribed to {symbol}")
                
                with Live(self._build_table(), refresh_per_second=2) as live:
                    async for msg in ws:
                        if not self._running:
                            break
                        data = orjson.loads(msg)
                        if "params" in data and data.get("method") == "subscription":
                            ch = data["params"].get("channel", "")
                            bd = data["params"].get("data", {})
                            if "order_book." in ch:
                                symbol = ch.split(".")[1]
                                inserts = bd.get("inserts", [])
                                bids = [[e["price"], e["size"]] for e in inserts if e.get("side") == "BUY"]
                                asks = [[e["price"], e["size"]] for e in inserts if e.get("side") == "SELL"]
                                if bids:
                                    self.orderbooks[symbol]['bid'] = (float(bids[0][0]), float(bids[0][1]))
                                if asks:
                                    self.orderbooks[symbol]['ask'] = (float(asks[0][0]), float(asks[0][1]))
                                self._update_count += 1
                                live.update(self._build_table())
        except Exception as e:
            console.print(f"Error: {e}")
    
    def _build_table(self):
        table = Table(title=f"PARADEX - Updates: {self._update_count}")
        table.add_column("Symbol")
        table.add_column("Bid Price")
        table.add_column("Bid Qty")
        table.add_column("Ask Price")
        table.add_column("Ask Qty")
        
        for symbol, data in self.orderbooks.items():
            bid = data.get('bid')
            ask = data.get('ask')
            table.add_row(
                symbol,
                f"${bid[0]:,.2f}" if bid else "-",
                f"{bid[1]:.4f}" if bid else "-",
                f"${ask[0]:,.2f}" if ask else "-",
                f"{ask[1]:.4f}" if ask else "-",
            )
        return table
    
    def stop(self):
        self._running = False


async def main():
    client = ParadexClient()
    try:
        await client.start()
    except KeyboardInterrupt:
        client.stop()
        console.print("\nStopped")


if __name__ == "__main__":
    asyncio.run(main())
