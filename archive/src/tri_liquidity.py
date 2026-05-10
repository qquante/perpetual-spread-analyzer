"""
Tri-Exchange Liquidity Scanner
Backpack x Paradex x Extended
Shows opportunities with spread > 0.1% and liquidity depth
"""

import asyncio
import httpx
import websockets
import orjson
import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field

from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from src.config import config
from src.core.orderbook import Orderbook

logging.disable(logging.CRITICAL)
console = Console()

# Fee rates
BP_FEE = 0.0002  # 0.02%
PX_FEE = 0.0000  # 0%
EX_FEE = 0.00025  # 0.025%

MIN_SPREAD = 0.10  # Show only > 0.1%


@dataclass 
class Opportunity:
    base: str
    spread: float
    exec_qty: float
    exec_value: float
    net_profit: float
    buy_exchange: str
    sell_exchange: str
    buy_price: float
    sell_price: float


@dataclass
class PairData:
    base: str
    bp_symbol: Optional[str] = None
    px_symbol: Optional[str] = None
    ex_symbol: Optional[str] = None
    bp_book: Optional[Orderbook] = None
    px_book: Optional[Orderbook] = None
    ex_book: Optional[Orderbook] = None
    best_opp: Optional[Opportunity] = None


class TriLiquidityScanner:
    """Tri-exchange scanner with liquidity analysis."""
    
    def __init__(self):
        self.pairs: Dict[str, PairData] = {}
        self._running = False
        self._update_count = 0
        self._start_time = None
        self._blocklist = {"ORDER", "TRX"}
        
    async def discover_pairs(self) -> None:
        """Discover pairs across all 3 exchanges."""
        async with httpx.AsyncClient(timeout=30) as client:
            # Backpack
            r1 = await client.get('https://api.backpack.exchange/api/v1/markets')
            bp_map = {m['symbol'].split('_')[0]: m['symbol'] for m in r1.json() if 'PERP' in m['symbol']}
            
            # Paradex
            r2 = await client.get('https://api.prod.paradex.trade/v1/markets')
            px_map = {m['symbol'].split('-')[0]: m['symbol'] for m in r2.json()['results'] if 'PERP' in m['symbol']}
            
            # Extended
            r3 = await client.get('https://api.starknet.extended.exchange/api/v1/info/markets')
            ex_map = {m['name'].split('-')[0]: m['name'] for m in r3.json()['data'] if m.get('status') == 'ACTIVE' and '-USD' in m['name']}
        
        # Get all unique bases
        all_bases = set(bp_map.keys()) | set(px_map.keys()) | set(ex_map.keys())
        
        for base in all_bases:
            if base in self._blocklist:
                continue
                
            bp_sym = bp_map.get(base)
            px_sym = px_map.get(base)
            ex_sym = ex_map.get(base)
            
            # Need at least 2 exchanges
            count = sum([bp_sym is not None, px_sym is not None, ex_sym is not None])
            if count >= 2:
                self.pairs[base] = PairData(
                    base=base,
                    bp_symbol=bp_sym,
                    px_symbol=px_sym,
                    ex_symbol=ex_sym,
                    bp_book=Orderbook("backpack", bp_sym) if bp_sym else None,
                    px_book=Orderbook("paradex", px_sym) if px_sym else None,
                    ex_book=Orderbook("extended", ex_sym) if ex_sym else None,
                )

    def _build_table(self) -> Table:
        """Build display table."""
        table = Table(
            title="BP x PX x EX - LIQUIDITY SCANNER (>0.1%)",
            show_header=True,
            header_style="bold cyan",
            border_style="dim",
        )
        
        table.add_column("PAIR", style="white", width=8)
        table.add_column("SPREAD", justify="right", width=10)
        table.add_column("QTY", justify="right", width=12)
        table.add_column("VALUE", justify="right", width=12)
        table.add_column("PROFIT", justify="right", width=10)
        table.add_column("BUY @", justify="right", width=12)
        table.add_column("SELL @", justify="right", width=12)
        table.add_column("ROUTE", justify="center", width=10)
        
        # Filter and sort by net profit
        good_opps = [
            p for p in self.pairs.values() 
            if p.best_opp and p.best_opp.spread >= MIN_SPREAD
        ]
        sorted_pairs = sorted(good_opps, key=lambda x: x.best_opp.net_profit, reverse=True)
        
        for pair in sorted_pairs:
            opp = pair.best_opp
            
            # Format spread
            if opp.spread >= 0.15:
                spread_str = f"[bold green]*{opp.spread:+.3f}%[/]"
            else:
                spread_str = f"[green]{opp.spread:+.4f}%[/]"
            
            # Format quantity
            if opp.exec_qty >= 100:
                qty_str = f"{opp.exec_qty:,.0f}"
            elif opp.exec_qty >= 1:
                qty_str = f"{opp.exec_qty:.2f}"
            else:
                qty_str = f"{opp.exec_qty:.4f}"
            
            # Format value
            value_str = f"${opp.exec_value:,.0f}" if opp.exec_value >= 1000 else f"${opp.exec_value:.2f}"
            
            # Format profit
            profit_str = f"[bold green]${opp.net_profit:.2f}[/]"
            
            # Route
            route_str = f"[cyan]{opp.buy_exchange}>{opp.sell_exchange}[/]"
            
            table.add_row(
                pair.base,
                spread_str,
                qty_str,
                value_str,
                profit_str,
                f"${opp.buy_price:,.2f}",
                f"${opp.sell_price:,.2f}",
                route_str,
            )
        
        return table

    def _build_status(self) -> Text:
        """Build status line."""
        time_str = datetime.now().strftime('%H:%M:%S')
        runtime = datetime.now() - self._start_time if self._start_time else None
        runtime_str = str(runtime).split('.')[0] if runtime else "0:00:00"
        
        # Count opportunities above threshold
        good_opps = [p for p in self.pairs.values() if p.best_opp and p.best_opp.spread >= MIN_SPREAD]
        total_profit = sum(p.best_opp.net_profit for p in good_opps)
        total_value = sum(p.best_opp.exec_value for p in good_opps)
        
        status = Text()
        status.append(f"  {time_str}", style="dim")
        status.append(f"  |  Updates: {self._update_count:,}", style="cyan")
        status.append(f"  |  Pairs: {len(self.pairs)}", style="white")
        status.append(f"  |  Runtime: {runtime_str}", style="dim")
        status.append(f"  |  Opps: {len(good_opps)}", style="green")
        status.append(f"  |  Liquidity: ${total_value:,.0f}", style="yellow")
        status.append(f"  |  Profit: ${total_profit:.2f}", style="bold green")
        
        return status

    async def start(self) -> None:
        """Start scanner."""
        self._start_time = datetime.now()
        self._running = True
        
        console.clear()
        console.print("[bold]Discovering pairs across 3 exchanges...[/]")
        
        await self.discover_pairs()
        
        bp = sum(1 for p in self.pairs.values() if p.bp_symbol)
        px = sum(1 for p in self.pairs.values() if p.px_symbol)
        ex = sum(1 for p in self.pairs.values() if p.ex_symbol)
        
        console.print(f"  BP: {bp} | PX: {px} | EX: {ex} | Total: {len(self.pairs)}")
        console.print(f"[bold]Fees:[/] BP={BP_FEE*100:.2f}% | PX={PX_FEE*100:.2f}% | EX={EX_FEE*100:.3f}%")
        console.print(f"[bold]Filter:[/] Spread >= {MIN_SPREAD}%")
        console.print()
        
        tasks = [
            asyncio.create_task(self._stream_backpack()),
            asyncio.create_task(self._stream_paradex()),
            asyncio.create_task(self._stream_extended()),
        ]
        
        try:
            with Live(self._build_table(), refresh_per_second=4, console=console) as live:
                while self._running:
                    await asyncio.sleep(0.25)
                    live.update(Panel.fit(
                        self._build_table(),
                        title=self._build_status(),
                        border_style="blue"
                    ))
        except asyncio.CancelledError:
            pass
        
        for task in tasks:
            task.cancel()

    async def _stream_backpack(self) -> None:
        """Stream Backpack."""
        ws_url = "wss://ws.backpack.exchange"
        
        while self._running:
            try:
                async with websockets.connect(ws_url) as ws:
                    for base, pair in self.pairs.items():
                        if pair.bp_symbol:
                            await ws.send(orjson.dumps({
                                "method": "SUBSCRIBE",
                                "params": [f"depth.{pair.bp_symbol}"]
                            }).decode())
                    
                    # Snapshots
                    async with httpx.AsyncClient(timeout=30) as client:
                        for base, pair in self.pairs.items():
                            if pair.bp_symbol and pair.bp_book:
                                try:
                                    r = await client.get('https://api.backpack.exchange/api/v1/depth',
                                                        params={'symbol': pair.bp_symbol})
                                    d = r.json()
                                    pair.bp_book.apply_snapshot(d.get('bids', []), d.get('asks', []), d.get('lastUpdateId', 0))
                                    self._calc_best(base)
                                except:
                                    pass
                    
                    async for msg in ws:
                        if not self._running:
                            break
                        try:
                            data = orjson.loads(msg)
                            if 'stream' in data and 'depth' in data['stream']:
                                sym = data['stream'].replace('depth.', '')
                                base = sym.split('_')[0]
                                if base in self.pairs and self.pairs[base].bp_book:
                                    p = data.get('data', {})
                                    self.pairs[base].bp_book.apply_delta(p.get('b', []), p.get('a', []), p.get('U', 0), p.get('u', 0))
                                    self._update_count += 1
                                    self._calc_best(base)
                        except:
                            pass
            except:
                if self._running:
                    await asyncio.sleep(2)

    async def _stream_paradex(self) -> None:
        """Stream Paradex."""
        ws_url = "wss://ws.api.prod.paradex.trade/v1"
        
        while self._running:
            try:
                async with websockets.connect(ws_url) as ws:
                    for i, (base, pair) in enumerate(self.pairs.items()):
                        if pair.px_symbol:
                            await ws.send(orjson.dumps({
                                "jsonrpc": "2.0",
                                "method": "subscribe",
                                "params": {"channel": f"order_book.{pair.px_symbol}.snapshot@15@100ms"},
                                "id": i + 1
                            }).decode())
                    
                    async for msg in ws:
                        if not self._running:
                            break
                        try:
                            data = orjson.loads(msg)
                            if "params" in data and data.get("method") == "subscription":
                                ch = data["params"].get("channel", "")
                                bd = data["params"].get("data", {})
                                if "order_book." in ch:
                                    px_sym = ch.split(".")[1]
                                    base = px_sym.split("-")[0]
                                    if base in self.pairs and self.pairs[base].px_book:
                                        ins = bd.get("inserts", [])
                                        bids = [[e["price"], e["size"]] for e in ins if e.get("side") == "BUY"]
                                        asks = [[e["price"], e["size"]] for e in ins if e.get("side") == "SELL"]
                                        self.pairs[base].px_book.apply_snapshot(bids, asks, bd.get("seq_no", 0))
                                        self._update_count += 1
                                        self._calc_best(base)
                        except:
                            pass
            except:
                if self._running:
                    await asyncio.sleep(2)

    async def _stream_extended(self) -> None:
        """Stream Extended - one WS per market."""
        tasks = []
        for base, pair in self.pairs.items():
            if pair.ex_symbol:
                tasks.append(self._stream_ex_single(base, pair.ex_symbol))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _stream_ex_single(self, base: str, symbol: str) -> None:
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
                            if base in self.pairs and self.pairs[base].ex_book:
                                bids = [[b["p"], b["q"]] for b in book.get("b", [])]
                                asks = [[a["p"], a["q"]] for a in book.get("a", [])]
                                seq = data.get("seq", 0)
                                if data.get("type") == "SNAPSHOT":
                                    self.pairs[base].ex_book.apply_snapshot(bids, asks, seq)
                                else:
                                    self.pairs[base].ex_book.apply_delta(bids, asks, seq, seq)
                                self._update_count += 1
                                self._calc_best(base)
                        except:
                            pass
            except:
                if self._running:
                    await asyncio.sleep(2)

    def _calc_best(self, base: str) -> None:
        """Calculate best opportunity across all exchange pairs."""
        pair = self.pairs.get(base)
        if not pair:
            return
        
        # Get all available prices
        prices = {}
        if pair.bp_book:
            bid = pair.bp_book.get_best_bid()
            ask = pair.bp_book.get_best_ask()
            if bid and ask:
                prices['BP'] = {'bid': (float(bid[0]), float(bid[1])), 'ask': (float(ask[0]), float(ask[1])), 'fee': BP_FEE}
        if pair.px_book:
            bid = pair.px_book.get_best_bid()
            ask = pair.px_book.get_best_ask()
            if bid and ask:
                prices['PX'] = {'bid': (float(bid[0]), float(bid[1])), 'ask': (float(ask[0]), float(ask[1])), 'fee': PX_FEE}
        if pair.ex_book:
            bid = pair.ex_book.get_best_bid()
            ask = pair.ex_book.get_best_ask()
            if bid and ask:
                prices['EX'] = {'bid': (float(bid[0]), float(bid[1])), 'ask': (float(ask[0]), float(ask[1])), 'fee': EX_FEE}
        
        if len(prices) < 2:
            return
        
        # Check all pairs
        best_opp = None
        exchanges = list(prices.keys())
        
        for i, buy_ex in enumerate(exchanges):
            for sell_ex in exchanges[i+1:]:
                # Try both directions
                for bex, sex in [(buy_ex, sell_ex), (sell_ex, buy_ex)]:
                    buy_price, buy_qty = prices[bex]['ask']
                    sell_price, sell_qty = prices[sex]['bid']
                    
                    if buy_price <= 0:
                        continue
                    
                    spread = (sell_price - buy_price) / buy_price * 100
                    
                    if spread > 0:
                        exec_qty = min(buy_qty, sell_qty)
                        exec_value = exec_qty * buy_price
                        gross = exec_qty * (sell_price - buy_price)
                        buy_cost = exec_value * prices[bex]['fee']
                        sell_cost = (exec_qty * sell_price) * prices[sex]['fee']
                        net = gross - buy_cost - sell_cost
                        
                        if best_opp is None or net > best_opp.net_profit:
                            best_opp = Opportunity(
                                base=base,
                                spread=spread,
                                exec_qty=exec_qty,
                                exec_value=exec_value,
                                net_profit=net,
                                buy_exchange=bex,
                                sell_exchange=sex,
                                buy_price=buy_price,
                                sell_price=sell_price,
                            )
        
        pair.best_opp = best_opp

    async def stop(self) -> None:
        """Stop scanner."""
        self._running = False
        runtime = datetime.now() - self._start_time if self._start_time else None
        
        console.print()
        console.rule("[bold]SESSION SUMMARY[/]")
        console.print(f"  Runtime: {str(runtime).split('.')[0] if runtime else 'N/A'}")
        console.print(f"  Updates: {self._update_count:,}")
        console.print(f"  Pairs: {len(self.pairs)}")
        console.rule()


async def main():
    scanner = TriLiquidityScanner()
    try:
        await scanner.start()
    except KeyboardInterrupt:
        pass
    finally:
        await scanner.stop()


if __name__ == "__main__":
    asyncio.run(main())
