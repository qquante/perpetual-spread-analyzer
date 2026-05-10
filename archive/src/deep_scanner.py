"""
Deep Liquidity Scanner
Analyzes multiple orderbook levels to find optimal execution size.
Shows how much you can execute at each spread threshold.
"""

import asyncio
import httpx
import websockets
import orjson
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
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
BP_FEE = 0.0002   # 0.02%
PX_FEE = 0.0000   # 0%


@dataclass
class DepthLevel:
    """Execution stats at a specific spread threshold."""
    min_spread: float
    exec_qty: float
    exec_value: float
    gross_profit: float
    net_profit: float


@dataclass
class PairAnalysis:
    """Full depth analysis for a pair."""
    base: str
    bp_symbol: str
    px_symbol: str
    bp_book: Orderbook
    px_book: Orderbook
    direction: str = ""  # BP>PX or PX>BP
    # Depth analysis at different spread levels
    depth_levels: List[DepthLevel] = field(default_factory=list)
    # Best profit stats
    best_profit: float = 0.0
    best_value_at_profit: float = 0.0
    best_spread_at_profit: float = 0.0


class DeepLiquidityScanner:
    """Scanner that analyzes orderbook depth."""
    
    def __init__(self):
        self.pairs: Dict[str, PairAnalysis] = {}
        self._running = False
        self._update_count = 0
        self._start_time = None
        self._blocklist = {"ORDER", "TRX"}
        
        # Spread thresholds to analyze (from lowest to highest for correct matching)
        self.thresholds = [0.01, 0.02, 0.03, 0.05, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20]
        
    async def discover_pairs(self) -> List[tuple]:
        """Discover common pairs."""
        async with httpx.AsyncClient(timeout=30) as client:
            r1 = await client.get('https://api.backpack.exchange/api/v1/markets')
            r2 = await client.get('https://api.prod.paradex.trade/v1/markets')
            
            bp_perps = {m['symbol'] for m in r1.json() if 'PERP' in m['symbol']}
            px_perps = [m['symbol'] for m in r2.json()['results'] if 'PERP' in m['symbol']]
            
            common = []
            for bp in bp_perps:
                base = bp.split('_')[0]
                if base in self._blocklist:
                    continue
                px_match = f"{base}-USD-PERP"
                if px_match in px_perps:
                    common.append((bp, px_match))
            return common

    def _analyze_depth(self, base: str) -> None:
        """Analyze orderbook depth for a pair."""
        pair = self.pairs.get(base)
        if not pair:
            return
        
        # Get best prices (same pattern as tri_liquidity.py that works)
        bp_bid = pair.bp_book.get_best_bid()
        bp_ask = pair.bp_book.get_best_ask()
        px_bid = pair.px_book.get_best_bid()
        px_ask = pair.px_book.get_best_ask()
        
        if not bp_bid or not bp_ask or not px_bid or not px_ask:
            return
        
        # Convert to floats
        bp_best_bid = (float(bp_bid[0]), float(bp_bid[1]))
        bp_best_ask = (float(bp_ask[0]), float(bp_ask[1]))
        px_best_bid = (float(px_bid[0]), float(px_bid[1]))
        px_best_ask = (float(px_ask[0]), float(px_ask[1]))
        
        if bp_best_ask[0] <= 0 or px_best_bid[0] <= 0:
            return
        
        # Check both directions
        bp_to_px = (px_best_bid[0] - bp_best_ask[0]) / bp_best_ask[0] * 100
        px_to_bp = (bp_best_bid[0] - px_best_ask[0]) / px_best_ask[0] * 100
        
        if bp_to_px > px_to_bp:
            pair.direction = "BP>PX"
            buy_price, buy_qty = bp_best_ask
            sell_price, sell_qty = px_best_bid
            buy_fee = BP_FEE
            sell_fee = PX_FEE
            best_spread = bp_to_px
        else:
            pair.direction = "PX>BP"
            buy_price, buy_qty = px_best_ask
            sell_price, sell_qty = bp_best_bid
            buy_fee = PX_FEE
            sell_fee = BP_FEE
            best_spread = px_to_bp
        
        # Calculate profit for best level (simplified like tri_liquidity.py)
        exec_qty = min(buy_qty, sell_qty)
        exec_value = exec_qty * buy_price
        gross = exec_qty * (sell_price - buy_price)
        buy_cost = exec_value * buy_fee
        sell_cost = (exec_qty * sell_price) * sell_fee
        net = gross - buy_cost - sell_cost
        
        # Store as single depth level
        pair.depth_levels = [DepthLevel(
            min_spread=best_spread,
            exec_qty=exec_qty,
            exec_value=exec_value,
            gross_profit=gross,
            net_profit=net,
        )]
        
        pair.best_profit = net
        pair.best_value_at_profit = exec_value
        pair.best_spread_at_profit = best_spread

    def _build_table(self) -> Table:
        """Build display table with depth analysis."""
        table = Table(show_header=True, header_style="bold cyan", border_style="dim")
        
        table.add_column("PAIR", width=6)
        table.add_column("DIR", width=6)
        # Show profit at each threshold
        table.add_column(">=0.15%", justify="right", width=9)
        table.add_column(">=0.10%", justify="right", width=9)
        table.add_column(">=0.05%", justify="right", width=9)
        table.add_column("BEST $", justify="right", width=9)
        table.add_column("@ VALUE", justify="right", width=10)
        table.add_column("@ SPREAD", justify="right", width=8)
        
        # Sort by best profit - show all pairs with direction set
        sorted_pairs = sorted(
            [p for p in self.pairs.values() if p.direction],
            key=lambda x: x.best_profit,
            reverse=True
        )[:30]
        
        for pair in sorted_pairs:
            # Get profit at each threshold
            def get_profit_at(thresh):
                for dl in pair.depth_levels:
                    if dl.min_spread == thresh:
                        return dl.net_profit
                return 0
            
            p15 = get_profit_at(0.15)
            p10 = get_profit_at(0.10)
            p05 = get_profit_at(0.05)
            
            # Format
            def fmt_profit(p):
                if p > 0:
                    return f"[green]${p:.2f}[/]"
                elif p < 0:
                    return f"[red]${p:.2f}[/]"
                else:
                    return "[dim]-[/]"
            
            best_str = f"[bold green]${pair.best_profit:.2f}[/]" if pair.best_profit > 0 else f"[dim]${pair.best_profit:.2f}[/]"
            value_str = f"${pair.best_value_at_profit:,.0f}"
            spread_str = f"{pair.best_spread_at_profit:.2f}%"
            
            table.add_row(
                pair.base,
                f"[cyan]{pair.direction}[/]",
                fmt_profit(p15),
                fmt_profit(p10),
                fmt_profit(p05),
                best_str,
                value_str,
                spread_str,
            )
        
        return table

    def _build_status(self) -> Text:
        """Build status line."""
        time_str = datetime.now().strftime('%H:%M:%S')
        runtime = datetime.now() - self._start_time if self._start_time else None
        runtime_str = str(runtime).split('.')[0] if runtime else "0:00:00"
        
        total_best_profit = sum(p.best_profit for p in self.pairs.values() if p.best_profit > 0)
        profitable = sum(1 for p in self.pairs.values() if p.best_profit > 0)
        
        status = Text()
        status.append(f" {time_str}", style="dim")
        status.append(f" | Updates: {self._update_count:,}", style="cyan")
        status.append(f" | Pairs: {len(self.pairs)}", style="white")
        status.append(f" | Runtime: {runtime_str}", style="dim")
        status.append(f" | Profitable: {profitable}", style="green" if profitable > 0 else "dim")
        status.append(f" | Total Best: ${total_best_profit:.2f}", style="bold green" if total_best_profit > 0 else "white")
        
        return status

    async def start(self) -> None:
        """Start scanner."""
        self._start_time = datetime.now()
        self._running = True
        
        console.clear()
        console.print("[bold]DEEP LIQUIDITY SCANNER[/]")
        console.print("Analyzing orderbook depth at multiple spread levels...")
        console.print()
        
        pairs = await self.discover_pairs()
        console.print(f"Discovered {len(pairs)} pairs")
        
        for bp_sym, px_sym in pairs:
            base = bp_sym.split('_')[0]
            self.pairs[base] = PairAnalysis(
                base=base,
                bp_symbol=bp_sym,
                px_symbol=px_sym,
                bp_book=Orderbook(exchange="backpack", symbol=bp_sym),
                px_book=Orderbook(exchange="paradex", symbol=px_sym),
            )
        
        console.print()
        
        tasks = [
            asyncio.create_task(self._stream_backpack()),
            asyncio.create_task(self._stream_paradex()),
        ]
        
        try:
            with Live(Panel(self._build_table(), title=self._build_status()), 
                      refresh_per_second=2, console=console) as live:
                while self._running:
                    await asyncio.sleep(0.5)
                    live.update(Panel(self._build_table(), title=self._build_status()))
        except asyncio.CancelledError:
            pass
        
        for task in tasks:
            task.cancel()

    async def _stream_backpack(self) -> None:
        """Stream Backpack orderbooks with full depth."""
        ws_url = "wss://ws.backpack.exchange"
        
        while self._running:
            try:
                async with websockets.connect(ws_url) as ws:
                    for base, pair in self.pairs.items():
                        await ws.send(orjson.dumps({
                            "method": "SUBSCRIBE",
                            "params": [f"depth.{pair.bp_symbol}"]
                        }).decode())
                    
                    # Initial snapshots with full depth
                    async with httpx.AsyncClient(timeout=30) as client:
                        for base, pair in self.pairs.items():
                            try:
                                r = await client.get(
                                    'https://api.backpack.exchange/api/v1/depth',
                                    params={'symbol': pair.bp_symbol}
                                )
                                data = r.json()
                                pair.bp_book.apply_snapshot(
                                    data.get('bids', []),
                                    data.get('asks', []),
                                    data.get('lastUpdateId', 0)
                                )
                                self._analyze_depth(base)
                            except:
                                pass
                    
                    async for message in ws:
                        if not self._running:
                            break
                        try:
                            data = orjson.loads(message)
                            if 'stream' in data and 'depth' in data['stream']:
                                symbol = data['stream'].replace('depth.', '')
                                base = symbol.split('_')[0]
                                if base in self.pairs:
                                    payload = data.get('data', {})
                                    self.pairs[base].bp_book.apply_delta(
                                        payload.get('b', []),
                                        payload.get('a', []),
                                        payload.get('U', 0),
                                        payload.get('u', 0)
                                    )
                                    self._update_count += 1
                                    self._analyze_depth(base)
                        except:
                            pass
            except:
                if self._running:
                    await asyncio.sleep(2)

    async def _stream_paradex(self) -> None:
        """Stream Paradex orderbooks."""
        ws_url = "wss://ws.api.prod.paradex.trade/v1"
        
        while self._running:
            try:
                async with websockets.connect(ws_url) as ws:
                    for i, (base, pair) in enumerate(self.pairs.items()):
                        channel = f"order_book.{pair.px_symbol}.snapshot@50@100ms"  # 50 levels!
                        await ws.send(orjson.dumps({
                            "jsonrpc": "2.0",
                            "method": "subscribe",
                            "params": {"channel": channel},
                            "id": i + 1
                        }).decode())
                    
                    async for message in ws:
                        if not self._running:
                            break
                        try:
                            data = orjson.loads(message)
                            if "params" in data and data.get("method") == "subscription":
                                params = data["params"]
                                channel = params.get("channel", "")
                                book_data = params.get("data", {})
                                
                                if "order_book." in channel:
                                    parts = channel.split(".")
                                    if len(parts) >= 2:
                                        px_symbol = parts[1]
                                        base = px_symbol.split("-")[0]
                                        
                                        if base in self.pairs:
                                            inserts = book_data.get("inserts", [])
                                            bids = [[e["price"], e["size"]] for e in inserts if e.get("side") == "BUY"]
                                            asks = [[e["price"], e["size"]] for e in inserts if e.get("side") == "SELL"]
                                            seq = book_data.get("seq_no", 0)
                                            
                                            self.pairs[base].px_book.apply_snapshot(bids, asks, seq)
                                            self._update_count += 1
                                            self._analyze_depth(base)
                        except:
                            pass
            except:
                if self._running:
                    await asyncio.sleep(2)

    async def stop(self) -> None:
        """Stop scanner."""
        self._running = False
        console.print()
        console.rule("[bold]SESSION SUMMARY[/]")
        total = sum(p.best_profit for p in self.pairs.values() if p.best_profit > 0)
        console.print(f"  Total Best Profit Potential: ${total:.2f}")
        console.rule()


async def main():
    scanner = DeepLiquidityScanner()
    try:
        await scanner.start()
    except KeyboardInterrupt:
        pass
    finally:
        await scanner.stop()


if __name__ == "__main__":
    asyncio.run(main())
