"""
Multi-Pair Arbitrage Scanner - Backpack x Paradex
Professional CLI with Rich library.
"""

import asyncio
import httpx
import websockets
import orjson
import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Tuple
from dataclasses import dataclass, field

from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from src.config import config
from src.core.orderbook import Orderbook

# Suppress all logs
logging.disable(logging.CRITICAL)

console = Console()


@dataclass
class PairData:
    bp_symbol: str
    px_symbol: str
    bp_book: Orderbook
    px_book: Orderbook
    last_spread: float = 0.0
    direction: str = ""
    valid: bool = True


class MultiPairScanner:
    """Professional multi-pair arbitrage scanner with Rich CLI."""
    
    def __init__(self):
        self.pairs: Dict[str, PairData] = {}
        self._running = False
        self._update_count = 0
        self._start_time = None
        self.threshold = config.trading.profit_threshold_percent
        self._bp_symbols: set = set()
        # Pairs to exclude (don't exist on Backpack)
        self._blocklist = {"ORDER", "TRX"}
        
    async def discover_pairs(self) -> List[Tuple[str, str]]:
        """Discover common pairs between exchanges."""
        async with httpx.AsyncClient(timeout=30) as client:
            r1 = await client.get('https://api.backpack.exchange/api/v1/markets')
            r2 = await client.get('https://api.prod.paradex.trade/v1/markets')
            
            bp_markets = r1.json()
            bp_perps = {m['symbol'] for m in bp_markets if 'PERP' in m['symbol']}
            self._bp_symbols = bp_perps
            
            px_perps = [m['symbol'] for m in r2.json()['results'] if 'PERP' in m['symbol']]
            
            common = []
            for bp in bp_perps:
                base = bp.split('_')[0]
                # Skip blocklisted pairs
                if base in self._blocklist:
                    continue
                px_match = f"{base}-USD-PERP"
                if px_match in px_perps:
                    common.append((bp, px_match))
            return common

    def _build_table(self) -> Table:
        """Build the display table."""
        table = Table(
            title=f"BACKPACK x PARADEX ARBITRAGE SCANNER",
            show_header=True,
            header_style="bold cyan",
            border_style="dim",
            title_style="bold white",
        )
        
        table.add_column("PAIR", style="white", width=10)
        table.add_column("BP BID", justify="right", style="green", width=12)
        table.add_column("BP ASK", justify="right", style="red", width=12)
        table.add_column("PX BID", justify="right", style="green", width=12)
        table.add_column("PX ASK", justify="right", style="red", width=12)
        table.add_column("SPREAD", justify="right", width=12)
        table.add_column("DIR", justify="center", width=8)
        
        # Sort by spread
        sorted_pairs = sorted(
            [(b, p) for b, p in self.pairs.items() if p.valid and p.last_spread != 0],
            key=lambda x: x[1].last_spread,
            reverse=True
        )
        
        for base, pair in sorted_pairs:
            bp_bid = pair.bp_book.get_best_bid()
            bp_ask = pair.bp_book.get_best_ask()
            px_bid = pair.px_book.get_best_bid()
            px_ask = pair.px_book.get_best_ask()
            
            if not all([bp_bid, bp_ask, px_bid, px_ask]):
                continue
            
            # Format spread with color
            spread = pair.last_spread
            if spread >= self.threshold:
                spread_str = f"[bold green]*{spread:+.3f}%[/]"
            elif spread > 0:
                spread_str = f"[green]{spread:+.4f}%[/]"
            elif spread > -0.1:
                spread_str = f"[yellow]{spread:+.4f}%[/]"
            else:
                spread_str = f"[red]{spread:+.4f}%[/]"
            
            # Direction style
            dir_str = f"[cyan]{pair.direction}[/]" if spread > 0 else f"[dim]{pair.direction}[/]"
            
            table.add_row(
                base,
                f"{float(bp_bid[0]):.4f}",
                f"{float(bp_ask[0]):.4f}",
                f"{float(px_bid[0]):.4f}",
                f"{float(px_ask[0]):.4f}",
                spread_str,
                dir_str,
            )
        
        return table

    def _build_status(self) -> Text:
        """Build status line."""
        time_str = datetime.now().strftime('%H:%M:%S')
        runtime = datetime.now() - self._start_time if self._start_time else None
        runtime_str = str(runtime).split('.')[0] if runtime else "0:00:00"
        
        # Count opportunities
        opportunities = sum(1 for p in self.pairs.values() if p.last_spread >= self.threshold)
        
        status = Text()
        status.append(f"  {time_str}", style="dim")
        status.append(f"  |  Updates: {self._update_count:,}", style="cyan")
        status.append(f"  |  Pairs: {len(self.pairs)}", style="white")
        status.append(f"  |  Runtime: {runtime_str}", style="dim")
        status.append(f"  |  Threshold: {self.threshold}%", style="yellow")
        
        if opportunities > 0:
            status.append(f"  |  OPPORTUNITIES: {opportunities}", style="bold green")
        
        return status

    async def start(self) -> None:
        """Start scanner with Rich Live display."""
        self._start_time = datetime.now()
        self._running = True
        
        console.clear()
        console.print("[bold]Discovering pairs...[/]", end="")
        
        pairs = await self.discover_pairs()
        console.print(f" [green]{len(pairs)} pairs found[/]")
        
        # Initialize orderbooks
        for bp_sym, px_sym in pairs:
            base = bp_sym.split('_')[0]
            self.pairs[base] = PairData(
                bp_symbol=bp_sym,
                px_symbol=px_sym,
                bp_book=Orderbook(exchange="backpack", symbol=bp_sym),
                px_book=Orderbook(exchange="paradex", symbol=px_sym),
                valid=True
            )
        
        console.print(f"[bold]Mode:[/] {'PAPER' if config.trading.paper_trading else 'LIVE'}")
        console.print()
        
        # Start streams
        tasks = [
            asyncio.create_task(self._stream_backpack()),
            asyncio.create_task(self._stream_paradex()),
        ]
        
        # Live display
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
        """Stream Backpack orderbooks."""
        ws_url = "wss://ws.backpack.exchange"
        
        while self._running:
            try:
                async with websockets.connect(ws_url) as ws:
                    for base, pair in self.pairs.items():
                        if pair.bp_symbol in self._bp_symbols:
                            sub_msg = {"method": "SUBSCRIBE", "params": [f"depth.{pair.bp_symbol}"]}
                            await ws.send(orjson.dumps(sub_msg).decode())
                    
                    # Initial snapshots
                    async with httpx.AsyncClient(timeout=30) as client:
                        for base, pair in self.pairs.items():
                            if pair.bp_symbol not in self._bp_symbols:
                                pair.valid = False
                                continue
                            try:
                                r = await client.get(
                                    'https://api.backpack.exchange/api/v1/depth',
                                    params={'symbol': pair.bp_symbol}
                                )
                                if r.status_code == 200:
                                    data = r.json()
                                    pair.bp_book.apply_snapshot(
                                        data.get('bids', []),
                                        data.get('asks', []),
                                        data.get('lastUpdateId', 0)
                                    )
                                else:
                                    pair.valid = False
                            except:
                                pair.valid = False
                    
                    async for message in ws:
                        if not self._running:
                            break
                        try:
                            data = orjson.loads(message)
                            if 'stream' in data and 'depth' in data['stream']:
                                symbol = data['stream'].replace('depth.', '')
                                base = symbol.split('_')[0]
                                if base in self.pairs and self.pairs[base].valid:
                                    payload = data.get('data', {})
                                    self.pairs[base].bp_book.apply_delta(
                                        payload.get('b', []),
                                        payload.get('a', []),
                                        payload.get('U', 0),
                                        payload.get('u', 0)
                                    )
                                    self._on_update(base)
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
                        if not pair.valid:
                            continue
                        channel = f"order_book.{pair.px_symbol}.snapshot@15@100ms"
                        sub_msg = {
                            "jsonrpc": "2.0",
                            "method": "subscribe",
                            "params": {"channel": channel},
                            "id": i + 1
                        }
                        await ws.send(orjson.dumps(sub_msg).decode())
                    
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
                                        
                                        if base in self.pairs and self.pairs[base].valid:
                                            inserts = book_data.get("inserts", [])
                                            bids = [[e["price"], e["size"]] for e in inserts if e.get("side") == "BUY"]
                                            asks = [[e["price"], e["size"]] for e in inserts if e.get("side") == "SELL"]
                                            seq = book_data.get("seq_no", 0)
                                            
                                            self.pairs[base].px_book.apply_snapshot(bids, asks, seq)
                                            self._on_update(base)
                        except:
                            pass
            except:
                if self._running:
                    await asyncio.sleep(2)

    def _on_update(self, base: str) -> None:
        """Calculate spread on update."""
        self._update_count += 1
        pair = self.pairs.get(base)
        if not pair or not pair.valid:
            return
        
        bp_bid = pair.bp_book.get_best_bid()
        bp_ask = pair.bp_book.get_best_ask()
        px_bid = pair.px_book.get_best_bid()
        px_ask = pair.px_book.get_best_ask()
        
        if all([bp_bid, bp_ask, px_bid, px_ask]):
            bp_to_px = float((px_bid[0] - bp_ask[0]) / bp_ask[0] * 100)
            px_to_bp = float((bp_bid[0] - px_ask[0]) / px_ask[0] * 100)
            
            if bp_to_px > px_to_bp:
                pair.last_spread = bp_to_px
                pair.direction = "BP>PX"
            else:
                pair.last_spread = px_to_bp
                pair.direction = "PX>BP"

    async def stop(self) -> None:
        """Stop scanner."""
        self._running = False
        runtime = datetime.now() - self._start_time if self._start_time else None
        
        console.print()
        console.rule("[bold]SESSION SUMMARY[/]")
        console.print(f"  Runtime: {str(runtime).split('.')[0] if runtime else 'N/A'}")
        console.print(f"  Updates: {self._update_count:,}")
        console.print(f"  Valid Pairs: {sum(1 for p in self.pairs.values() if p.valid)}")
        console.rule()


async def main():
    scanner = MultiPairScanner()
    try:
        await scanner.start()
    except KeyboardInterrupt:
        pass
    finally:
        await scanner.stop()


if __name__ == "__main__":
    asyncio.run(main())
