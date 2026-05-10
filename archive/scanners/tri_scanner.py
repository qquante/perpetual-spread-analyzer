"""
TRI-EXCHANGE ARBITRAGE SCANNER
Backpack x Paradex x Extended
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
BP_FEE = 0.0002   # 0.02%
PX_FEE = 0.0000   # 0%
EX_FEE = 0.00025  # 0.025%


class TriArbScanner:
    def __init__(self):
        self.pairs = {}  # base -> {bp_bid, bp_ask, px_bid, px_ask, ex_bid, ex_ask}
        self._running = False
        self._update_count = 0
        self._start_time = None
    
    async def discover_pairs(self):
        """Find pairs across all 3 exchanges."""
        async with httpx.AsyncClient(timeout=30) as client:
            r1 = await client.get('https://api.backpack.exchange/api/v1/markets')
            r2 = await client.get('https://api.prod.paradex.trade/v1/markets')
            r3 = await client.get('https://api.starknet.extended.exchange/api/v1/info/markets')
            
            bp_syms = {m['symbol'].split('_')[0]: m['symbol'] for m in r1.json() if 'PERP' in m['symbol']}
            px_syms = {m['symbol'].split('-')[0]: m['symbol'] for m in r2.json()['results'] if 'PERP' in m['symbol']}
            ex_syms = {m['name'].split('-')[0]: m['name'] for m in r3.json()['data'] if m.get('status') == 'ACTIVE' and '-USD' in m['name']}
        
        # Find all bases that exist in at least 2 exchanges
        all_bases = set(bp_syms.keys()) | set(px_syms.keys()) | set(ex_syms.keys())
        blocklist = {"ORDER", "TRX"}
        
        for base in all_bases:
            if base in blocklist:
                continue
            
            count = sum([base in bp_syms, base in px_syms, base in ex_syms])
            if count >= 2:
                self.pairs[base] = {
                    'bp_symbol': bp_syms.get(base),
                    'px_symbol': px_syms.get(base),
                    'ex_symbol': ex_syms.get(base),
                    'bp_bid': None, 'bp_ask': None,
                    'px_bid': None, 'px_ask': None,
                    'ex_bid': None, 'ex_ask': None,
                }
        
        bp_count = sum(1 for p in self.pairs.values() if p['bp_symbol'])
        px_count = sum(1 for p in self.pairs.values() if p['px_symbol'])
        ex_count = sum(1 for p in self.pairs.values() if p['ex_symbol'])
        
        return len(self.pairs), bp_count, px_count, ex_count
    
    def _calc_best_opportunity(self, base):
        """Calculate best arbitrage opportunity across all exchanges."""
        p = self.pairs.get(base)
        if not p:
            return None
        
        # Collect prices by exchange
        prices = {}
        if p['bp_bid'] and p['bp_ask']:
            prices['BP'] = {'bid': p['bp_bid'], 'ask': p['bp_ask'], 'fee': BP_FEE}
        if p['px_bid'] and p['px_ask']:
            prices['PX'] = {'bid': p['px_bid'], 'ask': p['px_ask'], 'fee': PX_FEE}
        if p['ex_bid'] and p['ex_ask']:
            prices['EX'] = {'bid': p['ex_bid'], 'ask': p['ex_ask'], 'fee': EX_FEE}
        
        if len(prices) < 2:
            return None
        
        # Check all exchange pairs for best opportunity
        best = None
        exchanges = list(prices.keys())
        
        for buy_ex in exchanges:
            for sell_ex in exchanges:
                if buy_ex == sell_ex:
                    continue
                
                buy_price, buy_qty = prices[buy_ex]['ask']
                sell_price, sell_qty = prices[sell_ex]['bid']
                
                # Skip invalid data
                if buy_price <= 0 or buy_qty <= 0 or sell_qty <= 0:
                    continue
                
                spread = (sell_price - buy_price) / buy_price * 100
                exec_qty = min(buy_qty, sell_qty)
                exec_value = exec_qty * buy_price
                gross = exec_qty * (sell_price - buy_price)
                net = gross - (exec_value * prices[buy_ex]['fee']) - (exec_qty * sell_price * prices[sell_ex]['fee'])
                
                if best is None or net > best['net_profit']:
                    best = {
                        'spread': spread,
                        'direction': f"{buy_ex}>{sell_ex}",
                        'exec_qty': exec_qty,
                        'exec_value': exec_value,
                        'net_profit': net,
                        'buy_price': buy_price,
                        'sell_price': sell_price,
                    }
        
        return best
    
    def _build_table(self):
        """Build display table."""
        table = Table(title=f"TRI-EXCHANGE ARBITRAGE - Updates: {self._update_count}")
        table.add_column("PAIR", width=8)
        table.add_column("SPREAD", justify="right", width=10)
        table.add_column("QTY", justify="right", width=12)
        table.add_column("VALUE", justify="right", width=10)
        table.add_column("PROFIT", justify="right", width=10)
        table.add_column("ROUTE", justify="center", width=8)
        
        # Calculate all opportunities
        results = []
        for base in self.pairs:
            calc = self._calc_best_opportunity(base)
            if calc:
                results.append((base, calc))
        
        # Sort by spread
        results.sort(key=lambda x: x[1]['spread'], reverse=True)
        
        for base, calc in results[:30]:
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
        
        console.print("[bold]TRI-EXCHANGE ARBITRAGE SCANNER[/]")
        console.print("Backpack x Paradex x Extended")
        console.print()
        
        total, bp, px, ex = await self.discover_pairs()
        console.print(f"Pairs: BP={bp} | PX={px} | EX={ex} | Total={total}")
        console.print(f"Fees: BP={BP_FEE*100:.2f}% | PX={PX_FEE*100:.2f}% | EX={EX_FEE*100:.3f}%")
        console.print()
        
        # Start all streams
        tasks = [
            asyncio.create_task(self._stream_backpack()),
            asyncio.create_task(self._stream_paradex()),
            asyncio.create_task(self._stream_extended()),
            asyncio.create_task(self._display_loop()),
        ]
        
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
    
    async def _display_loop(self):
        """Display table continuously."""
        await asyncio.sleep(2)  # Wait for initial data
        with Live(self._build_table(), refresh_per_second=4, console=console) as live:
            while self._running:
                await asyncio.sleep(0.25)
                live.update(self._build_table())
    
    async def _stream_backpack(self):
        """Stream Backpack data."""
        symbols = [(b, p['bp_symbol']) for b, p in self.pairs.items() if p['bp_symbol']]
        
        # Fetch snapshots
        console.print("Fetching Backpack snapshots...")
        async with httpx.AsyncClient(timeout=30) as client:
            for base, symbol in symbols:
                try:
                    r = await client.get('https://api.backpack.exchange/api/v1/depth', params={'symbol': symbol})
                    d = r.json()
                    bids, asks = d.get('bids', []), d.get('asks', [])
                    if bids and asks:
                        self.pairs[base]['bp_bid'] = (float(bids[0][0]), float(bids[0][1]))
                        self.pairs[base]['bp_ask'] = (float(asks[0][0]), float(asks[0][1]))
                except:
                    pass
        console.print(f"  ✓ Backpack ready")
        
        # Stream
        try:
            async with websockets.connect("wss://ws.backpack.exchange") as ws:
                for base, symbol in symbols:
                    await ws.send(orjson.dumps({"method": "SUBSCRIBE", "params": [f"depth.{symbol}"]}).decode())
                
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
                                bids, asks = payload.get('b', []), payload.get('a', [])
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
        symbols = [(b, p['px_symbol']) for b, p in self.pairs.items() if p['px_symbol']]
        
        try:
            async with websockets.connect("wss://ws.api.prod.paradex.trade/v1") as ws:
                for i, (base, symbol) in enumerate(symbols):
                    await ws.send(orjson.dumps({
                        "jsonrpc": "2.0",
                        "method": "subscribe",
                        "params": {"channel": f"order_book.{symbol}.snapshot@15@100ms"},
                        "id": i + 1
                    }).decode())
                
                console.print(f"  ✓ Paradex ready")
                
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
                    except:
                        pass
        except Exception as e:
            console.print(f"Paradex error: {e}")
    
    async def _stream_extended(self):
        """Stream Extended data - one WS per symbol."""
        symbols = [(b, p['ex_symbol']) for b, p in self.pairs.items() if p['ex_symbol']]
        
        console.print(f"  ✓ Extended connecting ({len(symbols)} symbols)...")
        
        tasks = [self._stream_extended_single(base, symbol) for base, symbol in symbols]
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _stream_extended_single(self, base: str, symbol: str):
        """Stream single Extended market."""
        ws_url = f"wss://api.starknet.extended.exchange/stream.extended.exchange/v1/orderbooks/{symbol}"
        
        while self._running:
            try:
                async with websockets.connect(ws_url) as ws:
                    async for msg in ws:
                        if not self._running:
                            break
                        try:
                            data = orjson.loads(msg)
                            book = data.get("data", {})
                            bids, asks = book.get("b", []), book.get("a", [])
                            if bids:
                                self.pairs[base]['ex_bid'] = (float(bids[0]["p"]), float(bids[0]["q"]))
                            if asks:
                                self.pairs[base]['ex_ask'] = (float(asks[0]["p"]), float(asks[0]["q"]))
                            self._update_count += 1
                        except:
                            pass
            except:
                if self._running:
                    await asyncio.sleep(2)
    
    def stop(self):
        self._running = False


async def main():
    scanner = TriArbScanner()
    try:
        await scanner.start()
    except KeyboardInterrupt:
        scanner.stop()
        console.print("\n[bold]Stopped[/]")


if __name__ == "__main__":
    asyncio.run(main())
