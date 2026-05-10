"""
MVP ARBITRAGE SCANNER
Backpack x Paradex ONLY - Simplified version
Calculates profit from current spread down to 0.10% threshold.
"""

import asyncio
import httpx
import websockets
import orjson
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from rich.console import Console
from rich.table import Table
from rich.live import Live
from datetime import datetime

console = Console()

# Fees
BP_FEE = 0.0002   # 0.02%
PX_FEE = 0.0000   # 0%

# Minimum spread threshold
MIN_SPREAD = 0.10  # Stop at 0.10%


@dataclass
class Level:
    price: float
    qty: float


@dataclass
class Orderbook:
    bids: List[Level] = field(default_factory=list)
    asks: List[Level] = field(default_factory=list)


@dataclass
class PairData:
    base: str
    bp_symbol: Optional[str] = None
    px_symbol: Optional[str] = None
    bp_book: Orderbook = field(default_factory=Orderbook)
    px_book: Orderbook = field(default_factory=Orderbook)


class MVPScanner:
    """Simplified scanner for Backpack + Paradex only."""
    
    def __init__(self):
        self.pairs: Dict[str, PairData] = {}
        self._running = False
        self._update_count = 0
    
    def _calc_accumulated_profit(self, buy_asks: List[Level], sell_bids: List[Level], 
                                   buy_fee: float, sell_fee: float) -> dict:
        """
        Calculate accumulated profit from current spread down to MIN_SPREAD (0.10%).
        """
        if not buy_asks or not sell_bids:
            return None
        
        asks = sorted([a for a in buy_asks if a.qty > 0], key=lambda x: x.price)
        bids = sorted([b for b in sell_bids if b.qty > 0], key=lambda x: x.price, reverse=True)
        
        if not asks or not bids or asks[0].price <= 0:
            return None
        
        current_spread = (bids[0].price - asks[0].price) / asks[0].price * 100
        best_qty = min(asks[0].qty, bids[0].qty)
        
        # If spread below threshold, show best level only
        if current_spread < MIN_SPREAD:
            return {
                'current_spread': current_spread,
                'exec_qty': best_qty,
                'exec_value': best_qty * asks[0].price,
                'net_profit': best_qty * (bids[0].price - asks[0].price) - (best_qty * asks[0].price * buy_fee) - (best_qty * bids[0].price * sell_fee),
                'levels_consumed': 1,
                'final_spread': current_spread,
            }
        
        # Match levels until spread drops below MIN_SPREAD
        total_qty = 0.0
        total_buy_cost = 0.0
        total_sell_revenue = 0.0
        levels_consumed = 0
        final_spread = current_spread
        
        ask_i, bid_i = 0, 0
        ask_remaining = asks[0].qty
        bid_remaining = bids[0].qty
        
        while ask_i < len(asks) and bid_i < len(bids):
            ask_price, bid_price = asks[ask_i].price, bids[bid_i].price
            
            if ask_price <= 0:
                break
            
            spread_at_level = (bid_price - ask_price) / ask_price * 100
            
            # STOP when spread drops below threshold
            if spread_at_level < MIN_SPREAD:
                break
            
            exec_qty = min(ask_remaining, bid_remaining)
            if exec_qty <= 0:
                if ask_remaining <= 0:
                    ask_i += 1
                    if ask_i < len(asks):
                        ask_remaining = asks[ask_i].qty
                if bid_remaining <= 0:
                    bid_i += 1
                    if bid_i < len(bids):
                        bid_remaining = bids[bid_i].qty
                continue
            
            total_qty += exec_qty
            total_buy_cost += exec_qty * ask_price
            total_sell_revenue += exec_qty * bid_price
            final_spread = spread_at_level
            levels_consumed += 1
            
            ask_remaining -= exec_qty
            bid_remaining -= exec_qty
            
            if ask_remaining <= 0:
                ask_i += 1
                if ask_i < len(asks):
                    ask_remaining = asks[ask_i].qty
            
            if bid_remaining <= 0:
                bid_i += 1
                if bid_i < len(bids):
                    bid_remaining = bids[bid_i].qty
        
        if total_qty <= 0:
            return {
                'current_spread': current_spread,
                'exec_qty': best_qty,
                'exec_value': best_qty * asks[0].price,
                'net_profit': best_qty * (bids[0].price - asks[0].price) - (best_qty * asks[0].price * buy_fee) - (best_qty * bids[0].price * sell_fee),
                'levels_consumed': 1,
                'final_spread': current_spread,
            }
        
        gross = total_sell_revenue - total_buy_cost
        net = gross - (total_buy_cost * buy_fee) - (total_sell_revenue * sell_fee)
        
        return {
            'current_spread': current_spread,
            'exec_qty': total_qty,
            'exec_value': total_buy_cost,
            'net_profit': net,
            'levels_consumed': levels_consumed,
            'final_spread': final_spread,
        }
    
    def _calc_best_opportunity(self, base: str) -> Optional[dict]:
        """Find best arbitrage opportunity between Backpack and Paradex."""
        pair = self.pairs.get(base)
        if not pair:
            return None
        
        books = {}
        if pair.bp_book.bids and pair.bp_book.asks:
            books['BP'] = {'book': pair.bp_book, 'fee': BP_FEE}
        if pair.px_book.bids and pair.px_book.asks:
            books['PX'] = {'book': pair.px_book, 'fee': PX_FEE}
        
        if len(books) < 2:
            return None
        
        best = None
        
        # Check both directions: BP→PX and PX→BP
        for buy_ex, sell_ex in [('BP', 'PX'), ('PX', 'BP')]:
            result = self._calc_accumulated_profit(
                books[buy_ex]['book'].asks,
                books[sell_ex]['book'].bids,
                books[buy_ex]['fee'],
                books[sell_ex]['fee'],
            )
            
            if result:
                if best is None or result['current_spread'] > best['current_spread']:
                    best = {
                        **result,
                        'direction': f"{buy_ex}>{sell_ex}",
                    }
        
        return best
    
    async def discover_pairs(self):
        """Find pairs that exist on both Backpack and Paradex."""
        async with httpx.AsyncClient(timeout=30) as client:
            r1 = await client.get('https://api.backpack.exchange/api/v1/markets')
            r2 = await client.get('https://api.prod.paradex.trade/v1/markets')
            
            bp_syms = {m['symbol'].split('_')[0]: m['symbol'] for m in r1.json() if 'PERP' in m['symbol']}
            px_syms = {m['symbol'].split('-')[0]: m['symbol'] for m in r2.json()['results'] if 'PERP' in m['symbol']}
        
        # Only pairs that exist on BOTH exchanges
        common_bases = set(bp_syms.keys()) & set(px_syms.keys())
        blocklist = {"ORDER", "TRX"}
        
        for base in common_bases:
            if base in blocklist:
                continue
            self.pairs[base] = PairData(
                base=base,
                bp_symbol=bp_syms[base],
                px_symbol=px_syms[base],
            )
        
        return len(self.pairs)
    
    def _build_table(self) -> Table:
        """Build display table."""
        table = Table(title=f"MVP SCANNER (BP×PX) ≥{MIN_SPREAD}% | Updates: {self._update_count}")
        table.add_column("PAIR", width=8)
        table.add_column("SPREAD", justify="right", width=8)
        table.add_column("QTY", justify="right", width=12)
        table.add_column("VALUE", justify="right", width=10)
        table.add_column("PROFIT", justify="right", width=10)
        table.add_column("LVLS", justify="right", width=5)
        table.add_column("END%", justify="right", width=7)
        table.add_column("DIR", justify="center", width=7)
        
        results = []
        for base in self.pairs:
            calc = self._calc_best_opportunity(base)
            if calc:
                results.append((base, calc))
        
        results.sort(key=lambda x: x[1]['current_spread'], reverse=True)
        
        for base, calc in results[:50]:
            spread = calc['current_spread']
            
            if spread >= 0.15:
                spread_str = f"[bold green]*{spread:.2f}%[/]"
            elif spread >= 0.10:
                spread_str = f"[green]{spread:.2f}%[/]"
            else:
                spread_str = f"[yellow]{spread:.2f}%[/]"
            
            qty = calc['exec_qty']
            qty_str = f"{qty:.4f}" if qty < 10 else f"{qty:.2f}" if qty < 1000 else f"{qty:,.0f}"
            
            value = calc['exec_value']
            value_str = f"${value:,.0f}" if value >= 100 else f"${value:.2f}"
            
            net = calc['net_profit']
            profit_str = f"[bold green]${net:.2f}[/]" if net > 0 else f"[red]${net:.2f}[/]"
            
            end_spread = calc['final_spread']
            
            table.add_row(
                base,
                spread_str,
                qty_str,
                value_str,
                profit_str,
                str(calc['levels_consumed']),
                f"{end_spread:.2f}%",
                f"[cyan]{calc['direction']}[/]",
            )
        
        return table
    
    async def start(self):
        """Start the MVP scanner."""
        self._running = True
        
        console.print("[bold]MVP ARBITRAGE SCANNER[/]")
        console.print("Backpack × Paradex ONLY")
        console.print(f"Min spread threshold: {MIN_SPREAD}%")
        console.print()
        
        count = await self.discover_pairs()
        console.print(f"[green]✓ Found {count} common pairs[/]")
        console.print()
        
        tasks = [
            asyncio.create_task(self._stream_backpack()),
            asyncio.create_task(self._stream_paradex()),
            asyncio.create_task(self._display_loop()),
        ]
        
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
    
    async def _display_loop(self):
        await asyncio.sleep(3)
        with Live(self._build_table(), refresh_per_second=4, console=console) as live:
            while self._running:
                await asyncio.sleep(0.25)
                live.update(self._build_table())
    
    async def _stream_backpack(self):
        """Stream Backpack orderbooks."""
        symbols = [(b, p.bp_symbol) for b, p in self.pairs.items() if p.bp_symbol]
        
        console.print("Fetching Backpack snapshots...")
        async with httpx.AsyncClient(timeout=30) as client:
            for base, symbol in symbols:
                try:
                    r = await client.get('https://api.backpack.exchange/api/v1/depth',
                                        params={'symbol': symbol})
                    d = r.json()
                    bids = [Level(float(p), float(q)) for p, q in d.get('bids', [])]
                    asks = [Level(float(p), float(q)) for p, q in d.get('asks', [])]
                    self.pairs[base].bp_book = Orderbook(
                        bids=sorted(bids, key=lambda x: x.price, reverse=True),
                        asks=sorted(asks, key=lambda x: x.price),
                    )
                except:
                    pass
        console.print(f"[green]✓ Backpack ready ({len(symbols)} symbols)[/]")
        
        try:
            async with websockets.connect("wss://ws.backpack.exchange") as ws:
                for base, symbol in symbols:
                    await ws.send(orjson.dumps({
                        "method": "SUBSCRIBE",
                        "params": [f"depth.{symbol}"]
                    }).decode())
                
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
                                book = self.pairs[base].bp_book
                                
                                for p_str, q_str in payload.get('b', []):
                                    p, q = float(p_str), float(q_str)
                                    book.bids = [b for b in book.bids if b.price != p]
                                    if q > 0:
                                        book.bids.append(Level(p, q))
                                    book.bids = sorted(book.bids, key=lambda x: x.price, reverse=True)
                                
                                for p_str, q_str in payload.get('a', []):
                                    p, q = float(p_str), float(q_str)
                                    book.asks = [a for a in book.asks if a.price != p]
                                    if q > 0:
                                        book.asks.append(Level(p, q))
                                    book.asks = sorted(book.asks, key=lambda x: x.price)
                                
                                self._update_count += 1
                    except:
                        pass
        except Exception as e:
            console.print(f"[red]Backpack error: {e}[/]")
    
    async def _stream_paradex(self):
        """Stream Paradex orderbooks."""
        symbols = [(b, p.px_symbol) for b, p in self.pairs.items() if p.px_symbol]
        
        try:
            async with websockets.connect("wss://ws.api.prod.paradex.trade/v1") as ws:
                for i, (base, symbol) in enumerate(symbols):
                    await ws.send(orjson.dumps({
                        "jsonrpc": "2.0",
                        "method": "subscribe",
                        "params": {"channel": f"order_book.{symbol}.snapshot@15@100ms"},
                        "id": i + 1
                    }).decode())
                
                console.print(f"[green]✓ Paradex ready ({len(symbols)} symbols)[/]")
                
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
                                    bids = [Level(float(e["price"]), float(e["size"])) 
                                           for e in inserts if e.get("side") == "BUY" and float(e["size"]) > 0]
                                    asks = [Level(float(e["price"]), float(e["size"])) 
                                           for e in inserts if e.get("side") == "SELL" and float(e["size"]) > 0]
                                    
                                    self.pairs[base].px_book = Orderbook(
                                        bids=sorted(bids, key=lambda x: x.price, reverse=True),
                                        asks=sorted(asks, key=lambda x: x.price),
                                    )
                                    self._update_count += 1
                    except:
                        pass
        except Exception as e:
            console.print(f"[red]Paradex error: {e}[/]")
    
    def stop(self):
        self._running = False


async def main():
    scanner = MVPScanner()
    try:
        await scanner.start()
    except KeyboardInterrupt:
        scanner.stop()
        console.print("\n[bold]Stopped[/]")


if __name__ == "__main__":
    asyncio.run(main())
