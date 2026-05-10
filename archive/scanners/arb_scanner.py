"""
Scanner de Arbitragem: Backpack x Paradex
Usa os mesmos padrões testados isoladamente.
"""

import asyncio
import httpx
import websockets
import orjson
from rich.console import Console
from rich.table import Table
from rich.live import Live
from datetime import datetime

console = Console()

# Fees
BP_FEE = 0.0002
PX_FEE = 0.0000


class ArbScanner:
    def __init__(self):
        self.pairs = {}  # base -> {bp_bid, bp_ask, px_bid, px_ask}
        self._running = False
        self._update_count = 0
        self._start_time = None
    
    async def discover_pairs(self):
        """Find common pairs between BP and PX."""
        async with httpx.AsyncClient(timeout=30) as client:
            r1 = await client.get('https://api.backpack.exchange/api/v1/markets')
            r2 = await client.get('https://api.prod.paradex.trade/v1/markets')
            
            bp_syms = {m['symbol'].split('_')[0]: m['symbol'] for m in r1.json() if 'PERP' in m['symbol']}
            px_syms = {m['symbol'].split('-')[0]: m['symbol'] for m in r2.json()['results'] if 'PERP' in m['symbol']}
        
        # Find common
        common = set(bp_syms.keys()) & set(px_syms.keys())
        blocklist = {"ORDER", "TRX"}
        
        for base in common:
            if base not in blocklist:
                self.pairs[base] = {
                    'bp_symbol': bp_syms[base],
                    'px_symbol': px_syms[base],
                    'bp_bid': None,
                    'bp_ask': None,
                    'px_bid': None,
                    'px_ask': None,
                }
        
        return len(self.pairs)
    
    def _calc_spread(self, base):
        """Calculate spread for a pair."""
        p = self.pairs.get(base)
        if not p:
            return None
        
        bp_bid = p.get('bp_bid')
        bp_ask = p.get('bp_ask')
        px_bid = p.get('px_bid')
        px_ask = p.get('px_ask')
        
        if not all([bp_bid, bp_ask, px_bid, px_ask]):
            return None
        
        # Check both directions
        # BP>PX: buy on BP ask, sell on PX bid
        bp_to_px = (px_bid[0] - bp_ask[0]) / bp_ask[0] * 100 if bp_ask[0] > 0 else 0
        # PX>BP: buy on PX ask, sell on BP bid  
        px_to_bp = (bp_bid[0] - px_ask[0]) / px_ask[0] * 100 if px_ask[0] > 0 else 0
        
        if bp_to_px > px_to_bp:
            spread = bp_to_px
            direction = "BP>PX"
            buy_price, buy_qty = bp_ask
            sell_price, sell_qty = px_bid
            buy_fee, sell_fee = BP_FEE, PX_FEE
        else:
            spread = px_to_bp
            direction = "PX>BP"
            buy_price, buy_qty = px_ask
            sell_price, sell_qty = bp_bid
            buy_fee, sell_fee = PX_FEE, BP_FEE
        
        exec_qty = min(buy_qty, sell_qty)
        exec_value = exec_qty * buy_price
        gross = exec_qty * (sell_price - buy_price)
        net = gross - (exec_value * buy_fee) - (exec_qty * sell_price * sell_fee)
        
        return {
            'spread': spread,
            'direction': direction,
            'exec_qty': exec_qty,
            'exec_value': exec_value,
            'net_profit': net,
            'buy_price': buy_price,
            'sell_price': sell_price,
        }
    
    def _build_table(self):
        """Build display table."""
        table = Table(title=f"ARBITRAGE SCANNER - Updates: {self._update_count}")
        table.add_column("PAIR", width=8)
        table.add_column("SPREAD", justify="right", width=10)
        table.add_column("QTY", justify="right", width=12)
        table.add_column("VALUE", justify="right", width=10)
        table.add_column("PROFIT", justify="right", width=10)
        table.add_column("ROUTE", justify="center", width=8)
        
        # Calculate all spreads
        results = []
        for base in self.pairs:
            calc = self._calc_spread(base)
            if calc:
                results.append((base, calc))
        
        # Sort by spread
        results.sort(key=lambda x: x[1]['spread'], reverse=True)
        
        for base, calc in results[:25]:
            spread = calc['spread']
            
            if spread >= 0.15:
                spread_str = f"[bold green]*{spread:+.3f}%[/]"
            elif spread >= 0.05:
                spread_str = f"[green]{spread:+.4f}%[/]"
            elif spread >= 0:
                spread_str = f"[yellow]{spread:+.4f}%[/]"
            else:
                spread_str = f"[red]{spread:+.4f}%[/]"
            
            qty = calc['exec_qty']
            qty_str = f"{qty:.4f}" if qty < 10 else f"{qty:.2f}" if qty < 1000 else f"{qty:,.0f}"
            
            value = calc['exec_value']
            value_str = f"${value:,.0f}" if value >= 100 else f"${value:.2f}"
            
            net = calc['net_profit']
            profit_str = f"[green]${net:.2f}[/]" if net > 0 else f"[red]${net:.2f}[/]"
            
            table.add_row(
                base,
                spread_str,
                qty_str,
                value_str,
                profit_str,
                f"[cyan]{calc['direction']}[/]"
            )
        
        return table
    
    async def start(self):
        """Start scanner."""
        self._running = True
        self._start_time = datetime.now()
        
        console.print("[bold]ARBITRAGE SCANNER: Backpack x Paradex[/]")
        
        count = await self.discover_pairs()
        console.print(f"Found {count} common pairs")
        console.print()
        
        # Start both streams
        await asyncio.gather(
            self._stream_backpack(),
            self._stream_paradex(),
        )
    
    async def _stream_backpack(self):
        """Stream Backpack data."""
        symbols = [p['bp_symbol'] for p in self.pairs.values()]
        
        # Fetch snapshots first
        console.print("Fetching Backpack snapshots...")
        async with httpx.AsyncClient(timeout=30) as client:
            for base, pair in self.pairs.items():
                try:
                    r = await client.get('https://api.backpack.exchange/api/v1/depth',
                                        params={'symbol': pair['bp_symbol']})
                    d = r.json()
                    bids = d.get('bids', [])
                    asks = d.get('asks', [])
                    if bids and asks:
                        pair['bp_bid'] = (float(bids[0][0]), float(bids[0][1]))
                        pair['bp_ask'] = (float(asks[0][0]), float(asks[0][1]))
                except:
                    pass
        console.print(f"  ✓ Got {sum(1 for p in self.pairs.values() if p['bp_bid'])} snapshots")
        
        # Stream updates
        try:
            async with websockets.connect("wss://ws.backpack.exchange") as ws:
                for symbol in symbols:
                    await ws.send(orjson.dumps({
                        "method": "SUBSCRIBE",
                        "params": [f"depth.{symbol}"]
                    }).decode())
                
                console.print("  ✓ Subscribed to Backpack stream")
                
                async for msg in ws:
                    if not self._running:
                        break
                    try:
                        data = orjson.loads(msg)
                        if 'stream' in data and 'depth' in data['stream']:
                            symbol = data['stream'].replace('depth.', '')
                            base = symbol.split('_')[0]
                            if base in self.pairs:
                                payload = data.get('data', {})
                                bids = payload.get('b', [])
                                asks = payload.get('a', [])
                                if bids:
                                    self.pairs[base]['bp_bid'] = (float(bids[0][0]), float(bids[0][1]))
                                if asks:
                                    self.pairs[base]['bp_ask'] = (float(asks[0][0]), float(asks[0][1]))
                                self._update_count += 1
                    except:
                        pass
        except Exception as e:
            console.print(f"Backpack error: {e}")
    
    async def _stream_paradex(self):
        """Stream Paradex data."""
        symbols = [(base, p['px_symbol']) for base, p in self.pairs.items()]
        
        try:
            async with websockets.connect("wss://ws.api.prod.paradex.trade/v1") as ws:
                for i, (base, symbol) in enumerate(symbols):
                    await ws.send(orjson.dumps({
                        "jsonrpc": "2.0",
                        "method": "subscribe",
                        "params": {"channel": f"order_book.{symbol}.snapshot@15@100ms"},
                        "id": i + 1
                    }).decode())
                
                console.print("  ✓ Subscribed to Paradex stream")
                console.print()
                
                with Live(self._build_table(), refresh_per_second=4, console=console) as live:
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
                                    base = symbol.split("-")[0]
                                    if base in self.pairs:
                                        inserts = bd.get("inserts", [])
                                        bids = [e for e in inserts if e.get("side") == "BUY"]
                                        asks = [e for e in inserts if e.get("side") == "SELL"]
                                        if bids:
                                            self.pairs[base]['px_bid'] = (float(bids[0]["price"]), float(bids[0]["size"]))
                                        if asks:
                                            self.pairs[base]['px_ask'] = (float(asks[0]["price"]), float(asks[0]["size"]))
                                        self._update_count += 1
                                        live.update(self._build_table())
                        except:
                            pass
        except Exception as e:
            console.print(f"Paradex error: {e}")
    
    def stop(self):
        self._running = False


async def main():
    scanner = ArbScanner()
    try:
        await scanner.start()
    except KeyboardInterrupt:
        scanner.stop()
        console.print("\nStopped")


if __name__ == "__main__":
    asyncio.run(main())
