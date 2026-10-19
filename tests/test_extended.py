"""
Teste isolado: Extended Exchange
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


class ExtendedClient:
    def __init__(self):
        self.orderbooks = {}
        self._running = False
        self._update_count = 0
    
    async def get_symbols(self):
        """Get all perp symbols from Extended."""
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get('https://api.starknet.extended.exchange/api/v1/info/markets')
            return [m['name'] for m in r.json()['data'] if m.get('status') == 'ACTIVE' and '-USD' in m['name']]
    
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
        
        # Stream each symbol (Extended needs one WS per symbol)
        console.print("\nStreaming updates (Ctrl+C to stop)...")
        
        tasks = [self._stream_single(symbol) for symbol in symbols]
        
        # Start display in background
        display_task = asyncio.create_task(self._display_loop())
        
        try:
            await asyncio.gather(*tasks, display_task)
        except asyncio.CancelledError:
            pass
    
    async def _stream_single(self, symbol: str):
        """Stream single Extended market."""
        ws_url = f"wss://api.starknet.extended.exchange/stream.extended.exchange/v1/orderbooks/{symbol}"
        
        while self._running:
            try:
                async with websockets.connect(ws_url) as ws:
                    console.print(f"  ✓ Connected to {symbol}")
                    
                    async for msg in ws:
                        if not self._running:
                            break
                        try:
                            data = orjson.loads(msg)
                            book = data.get("data", {})
                            bids = book.get("b", [])
                            asks = book.get("a", [])
                            if bids:
                                self.orderbooks[symbol]['bid'] = (float(bids[0]["p"]), float(bids[0]["q"]))
                            if asks:
                                self.orderbooks[symbol]['ask'] = (float(asks[0]["p"]), float(asks[0]["q"]))
                            self._update_count += 1
                        except Exception as e:
                            pass
            except Exception as e:
                console.print(f"  ✗ {symbol}: {e}")
                if self._running:
                    await asyncio.sleep(2)
    
    async def _display_loop(self):
        """Display table continuously."""
        with Live(self._build_table(), refresh_per_second=2) as live:
            while self._running:
                await asyncio.sleep(0.5)
                live.update(self._build_table())
    
    def _build_table(self):
        table = Table(title=f"EXTENDED - Updates: {self._update_count}")
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
    client = ExtendedClient()
    try:
        await client.start()
    except KeyboardInterrupt:
        client.stop()
        console.print("\nStopped")


if __name__ == "__main__":
    asyncio.run(main())
