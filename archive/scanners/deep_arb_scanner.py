"""
DEEP ARBITRAGE SCANNER
Analyzes orderbook depth across 3 exchanges (Backpack, Paradex, Extended).
Calculates accumulated profit by consuming levels until spread reaches 0.10%.
"""

import asyncio
import httpx
import websockets
import orjson
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from rich.console import Console
from rich.table import Table
from rich.live import Live
from datetime import datetime

console = Console()

# Fees
BP_FEE = 0.0002   # 0.02%
PX_FEE = 0.0000   # 0%
EX_FEE = 0.00025  # 0.025%

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
    ex_symbol: Optional[str] = None
    bp_book: Orderbook = field(default_factory=Orderbook)
    px_book: Orderbook = field(default_factory=Orderbook)
    ex_book: Orderbook = field(default_factory=Orderbook)


class DeepArbScanner:
    def __init__(self):
        self.pairs: Dict[str, PairData] = {}
        self._running = False
        self._update_count = 0
        self._start_time = None
    
    def _calc_accumulated_profit(self, buy_asks: List[Level], sell_bids: List[Level], 
                                   buy_fee: float, sell_fee: float) -> dict:
        """
        Calculate accumulated profit by matching orderbook levels.
        Consumes levels from current spread down to MIN_SPREAD (0.10%).
        
        Returns:
            {
                'current_spread': starting spread %,
                'exec_qty': total qty executed,
                'exec_value': total USD value,
                'gross_profit': profit before fees,
                'net_profit': profit after fees,
                'levels_consumed': how many levels we went through,
                'final_spread': spread at last executed level,
                'best_qty': qty available at best level (for display when no opportunity),
            }
        """
        if not buy_asks or not sell_bids:
            return None
        
        # Sort: asks ascending (cheapest first), bids descending (highest first)
        asks = sorted([a for a in buy_asks if a.qty > 0], key=lambda x: x.price)
        bids = sorted([b for b in sell_bids if b.qty > 0], key=lambda x: x.price, reverse=True)
        
        if not asks or not bids:
            return None
        
        # Check current best spread
        if asks[0].price <= 0:
            return None
        
        current_spread = (bids[0].price - asks[0].price) / asks[0].price * 100
        best_qty = min(asks[0].qty, bids[0].qty)
        
        # If spread is below threshold, show best level only
        if current_spread < MIN_SPREAD:
            return {
                'current_spread': current_spread,
                'exec_qty': best_qty,
                'exec_value': best_qty * asks[0].price,
                'gross_profit': best_qty * (bids[0].price - asks[0].price),
                'net_profit': best_qty * (bids[0].price - asks[0].price) - (best_qty * asks[0].price * buy_fee) - (best_qty * bids[0].price * sell_fee),
                'levels_consumed': 1,
                'final_spread': current_spread,
                'best_qty': best_qty,
            }
        
        # Match levels until spread drops below MIN_SPREAD
        total_qty = 0.0
        total_buy_cost = 0.0
        total_sell_revenue = 0.0
        levels_consumed = 0
        final_spread = current_spread
        
        # Use indices to walk through both books
        ask_i = 0
        bid_i = 0
        ask_remaining = asks[0].qty
        bid_remaining = bids[0].qty
        
        # Match levels while spread >= MIN_SPREAD
        while ask_i < len(asks) and bid_i < len(bids):
            ask_price = asks[ask_i].price
            bid_price = bids[bid_i].price
            
            if ask_price <= 0:
                break
            
            spread_at_level = (bid_price - ask_price) / ask_price * 100
            
            # STOP when spread drops below threshold
            if spread_at_level < MIN_SPREAD:
                break
            
            # Execute minimum of remaining quantities
            exec_qty = min(ask_remaining, bid_remaining)
            
            if exec_qty <= 0:
                # Move to next level
                if ask_remaining <= 0:
                    ask_i += 1
                    if ask_i < len(asks):
                        ask_remaining = asks[ask_i].qty
                if bid_remaining <= 0:
                    bid_i += 1
                    if bid_i < len(bids):
                        bid_remaining = bids[bid_i].qty
                continue
            
            # Accumulate
            total_qty += exec_qty
            total_buy_cost += exec_qty * ask_price
            total_sell_revenue += exec_qty * bid_price
            final_spread = spread_at_level
            levels_consumed += 1
            
            # Update remaining
            ask_remaining -= exec_qty
            bid_remaining -= exec_qty
            
            # Move to next level if exhausted
            if ask_remaining <= 0:
                ask_i += 1
                if ask_i < len(asks):
                    ask_remaining = asks[ask_i].qty
            
            if bid_remaining <= 0:
                bid_i += 1
                if bid_i < len(bids):
                    bid_remaining = bids[bid_i].qty
        
        # Calculate profit even if negative
        if total_qty <= 0:
            return {
                'current_spread': current_spread,
                'exec_qty': best_qty,
                'exec_value': best_qty * asks[0].price,
                'gross_profit': best_qty * (bids[0].price - asks[0].price),
                'net_profit': best_qty * (bids[0].price - asks[0].price) - (best_qty * asks[0].price * buy_fee) - (best_qty * bids[0].price * sell_fee),
                'levels_consumed': 1,
                'final_spread': current_spread,
                'best_qty': best_qty,
            }
        
        gross = total_sell_revenue - total_buy_cost
        buy_fees = total_buy_cost * buy_fee
        sell_fees = total_sell_revenue * sell_fee
        net = gross - buy_fees - sell_fees
        
        return {
            'current_spread': current_spread,
            'exec_qty': total_qty,
            'exec_value': total_buy_cost,
            'gross_profit': gross,
            'net_profit': net,
            'levels_consumed': levels_consumed,
            'final_spread': final_spread,
            'best_qty': best_qty,
        }
    
    def _calc_best_opportunity(self, base: str) -> Optional[dict]:
        """Find best arbitrage opportunity across all exchange pairs."""
        pair = self.pairs.get(base)
        if not pair:
            return None
        
        # Collect orderbooks by exchange
        books = {}
        if pair.bp_book.bids and pair.bp_book.asks:
            books['BP'] = {'book': pair.bp_book, 'fee': BP_FEE}
        if pair.px_book.bids and pair.px_book.asks:
            books['PX'] = {'book': pair.px_book, 'fee': PX_FEE}
        if pair.ex_book.bids and pair.ex_book.asks:
            books['EX'] = {'book': pair.ex_book, 'fee': EX_FEE}
        
        if len(books) < 2:
            return None
        
        best = None
        exchanges = list(books.keys())
        
        for buy_ex in exchanges:
            for sell_ex in exchanges:
                if buy_ex == sell_ex:
                    continue
                
                # Buy on buy_ex asks, sell on sell_ex bids
                result = self._calc_accumulated_profit(
                    books[buy_ex]['book'].asks,
                    books[sell_ex]['book'].bids,
                    books[buy_ex]['fee'],
                    books[sell_ex]['fee'],
                )
                
                if result:
                    # Keep best by current spread (highest opportunity)
                    if best is None or result['current_spread'] > best['current_spread']:
                        best = {
                            **result,
                            'direction': f"{buy_ex}>{sell_ex}",
                            'buy_ex': buy_ex,
                            'sell_ex': sell_ex,
                        }
        
        return best
    
    async def discover_pairs(self):
        """Find pairs across all 3 exchanges."""
        async with httpx.AsyncClient(timeout=30) as client:
            r1 = await client.get('https://api.backpack.exchange/api/v1/markets')
            r2 = await client.get('https://api.prod.paradex.trade/v1/markets')
            r3 = await client.get('https://api.starknet.extended.exchange/api/v1/info/markets')
            
            bp_syms = {m['symbol'].split('_')[0]: m['symbol'] for m in r1.json() if 'PERP' in m['symbol']}
            px_syms = {m['symbol'].split('-')[0]: m['symbol'] for m in r2.json()['results'] if 'PERP' in m['symbol']}
            ex_syms = {m['name'].split('-')[0]: m['name'] for m in r3.json()['data'] if m.get('status') == 'ACTIVE' and '-USD' in m['name']}
        
        all_bases = set(bp_syms.keys()) | set(px_syms.keys()) | set(ex_syms.keys())
        blocklist = {"ORDER", "TRX"}
        
        for base in all_bases:
            if base in blocklist:
                continue
            count = sum([base in bp_syms, base in px_syms, base in ex_syms])
            if count >= 2:
                self.pairs[base] = PairData(
                    base=base,
                    bp_symbol=bp_syms.get(base),
                    px_symbol=px_syms.get(base),
                    ex_symbol=ex_syms.get(base),
                )
        
        return len(self.pairs)
    
    def _build_table(self) -> Table:
        """Build display table with depth analysis."""
        table = Table(title=f"DEEP ARB SCANNER (≥{MIN_SPREAD}%) - Updates: {self._update_count}")
        table.add_column("PAIR", width=8)
        table.add_column("SPREAD", justify="right", width=8)
        table.add_column("QTY", justify="right", width=12)
        table.add_column("VALUE", justify="right", width=10)
        table.add_column("PROFIT", justify="right", width=10)
        table.add_column("LEVELS", justify="right", width=6)
        table.add_column("END %", justify="right", width=7)
        table.add_column("ROUTE", justify="center", width=8)
        
        # Calculate all opportunities - show ALL pairs with data
        results = []
        for base in self.pairs:
            calc = self._calc_best_opportunity(base)
            if calc:
                results.append((base, calc))
        
        # Sort by current spread (highest first)
        results.sort(key=lambda x: x[1]['current_spread'], reverse=True)
        
        for base, calc in results[:40]:
            spread = calc['current_spread']
            
            if spread >= 0.15:
                spread_str = f"[bold green]*{spread:.2f}%[/]"
            elif spread >= 0.10:
                spread_str = f"[green]{spread:.2f}%[/]"
            else:
                spread_str = f"[yellow]{spread:.2f}%[/]"
            
            qty = calc['exec_qty']
            best_qty = calc.get('best_qty', 0)
            # Show best_qty in parentheses if exec_qty is 0
            if qty > 0:
                qty_str = f"{qty:.4f}" if qty < 10 else f"{qty:.2f}" if qty < 1000 else f"{qty:,.0f}"
            elif best_qty > 0:
                # Show available qty at best level in dim color
                qty_str = f"[dim]({best_qty:.2f})[/]" if best_qty < 1000 else f"[dim]({best_qty:,.0f})[/]"
            else:
                qty_str = "-"
            
            value = calc['exec_value']
            value_str = f"${value:,.0f}" if value >= 100 else f"${value:.2f}"
            
            net = calc['net_profit']
            profit_str = f"[bold green]${net:.2f}[/]" if net > 0 else f"[red]${net:.2f}[/]"
            
            end_spread = calc['final_spread']
            end_str = f"{end_spread:.2f}%"
            
            table.add_row(
                base,
                spread_str,
                qty_str,
                value_str,
                profit_str,
                str(calc['levels_consumed']),
                end_str,
                f"[cyan]{calc['direction']}[/]",
            )
        
        return table
    
    async def start(self):
        """Start scanner."""
        self._running = True
        self._start_time = datetime.now()
        
        console.print("[bold]DEEP ARBITRAGE SCANNER[/]")
        console.print(f"Backpack x Paradex x Extended | Min spread: {MIN_SPREAD}%")
        console.print()
        
        count = await self.discover_pairs()
        console.print(f"Discovered {count} pairs")
        console.print()
        
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
        console.print(f"  ✓ Backpack ready")
        
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
                                # Update orderbook with deltas
                                for p_str, q_str in payload.get('b', []):
                                    p, q = float(p_str), float(q_str)
                                    book = self.pairs[base].bp_book
                                    book.bids = [b for b in book.bids if b.price != p]
                                    if q > 0:
                                        book.bids.append(Level(p, q))
                                    book.bids = sorted(book.bids, key=lambda x: x.price, reverse=True)
                                
                                for p_str, q_str in payload.get('a', []):
                                    p, q = float(p_str), float(q_str)
                                    book = self.pairs[base].bp_book
                                    book.asks = [a for a in book.asks if a.price != p]
                                    if q > 0:
                                        book.asks.append(Level(p, q))
                                    book.asks = sorted(book.asks, key=lambda x: x.price)
                                
                                self._update_count += 1
                    except:
                        pass
        except Exception as e:
            console.print(f"Backpack error: {e}")
    
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
            console.print(f"Paradex error: {e}")
    
    async def _stream_extended(self):
        """Stream Extended orderbooks."""
        symbols = [(b, p.ex_symbol) for b, p in self.pairs.items() if p.ex_symbol]
        
        console.print(f"  ➤ Extended connecting ({len(symbols)} symbols)...")
        
        # Track connected count
        self._extended_connected = 0
        tasks = [self._stream_extended_single(base, symbol) for base, symbol in symbols]
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _stream_extended_single(self, base: str, symbol: str):
        ws_url = f"wss://api.starknet.extended.exchange/stream.extended.exchange/v1/orderbooks/{symbol}"
        
        while self._running:
            try:
                async with websockets.connect(ws_url) as ws:
                    async for msg in ws:
                        if not self._running:
                            break
                        try:
                            data = orjson.loads(msg)
                            book_data = data.get("data", {})
                            bids = [Level(float(e["p"]), float(e["q"])) 
                                   for e in book_data.get("b", []) if float(e["q"]) > 0]
                            asks = [Level(float(e["p"]), float(e["q"])) 
                                   for e in book_data.get("a", []) if float(e["q"]) > 0]
                            
                            self.pairs[base].ex_book = Orderbook(
                                bids=sorted(bids, key=lambda x: x.price, reverse=True),
                                asks=sorted(asks, key=lambda x: x.price),
                            )
                            self._update_count += 1
                            
                            # First time we get data for this symbol
                            if not hasattr(self, '_extended_connected'):
                                self._extended_connected = 0
                            self._extended_connected += 1
                            if self._extended_connected <= len(self.pairs):
                                pass  # Connection tracked
                        except:
                            pass
            except:
                if self._running:
                    await asyncio.sleep(2)
    
    def stop(self):
        self._running = False


async def main():
    scanner = DeepArbScanner()
    try:
        await scanner.start()
    except KeyboardInterrupt:
        scanner.stop()
        console.print("\n[bold]Stopped[/]")


if __name__ == "__main__":
    asyncio.run(main())
