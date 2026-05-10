"""
Tri-Exchange Arbitrage Scanner
Backpack x Paradex x Extended
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


@dataclass
class PairData:
    base: str
    bp_symbol: Optional[str] = None
    px_symbol: Optional[str] = None
    ex_symbol: Optional[str] = None
    bp_book: Optional[Orderbook] = None
    px_book: Optional[Orderbook] = None
    ex_book: Optional[Orderbook] = None
    # Spreads between each pair of exchanges
    bp_px_spread: float = 0.0
    bp_px_dir: str = ""
    bp_ex_spread: float = 0.0
    bp_ex_dir: str = ""
    px_ex_spread: float = 0.0
    px_ex_dir: str = ""


class TriExchangeScanner:
    """Tri-exchange arbitrage scanner with Rich CLI."""
    
    def __init__(self):
        self.pairs: Dict[str, PairData] = {}
        self._running = False
        self._update_count = 0
        self._start_time = None
        self.threshold = config.trading.profit_threshold_percent
        self._blocklist = {"ORDER", "TRX"}
        
    async def discover_pairs(self) -> None:
        """Discover pairs across all 3 exchanges."""
        async with httpx.AsyncClient(timeout=30) as client:
            # Backpack
            r1 = await client.get('https://api.backpack.exchange/api/v1/markets')
            bp_markets = {m['symbol']: m['symbol'].split('_')[0] for m in r1.json() if 'PERP' in m['symbol']}
            
            # Paradex
            r2 = await client.get('https://api.prod.paradex.trade/v1/markets')
            px_markets = {m['symbol']: m['symbol'].split('-')[0] for m in r2.json()['results'] if 'PERP' in m['symbol']}
            
            # Extended
            r3 = await client.get('https://api.starknet.extended.exchange/api/v1/info/markets')
            ex_markets = {m['name']: m['name'].split('-')[0] for m in r3.json()['data'] if m.get('status') == 'ACTIVE'}
        
        # Find all unique bases
        all_bases = set()
        for sym, base in bp_markets.items():
            if base not in self._blocklist:
                all_bases.add(base)
        for sym, base in px_markets.items():
            if base not in self._blocklist:
                all_bases.add(base)
        for sym, base in ex_markets.items():
            if base not in self._blocklist:
                all_bases.add(base)
        
        # Create pair data for each base
        for base in all_bases:
            bp_sym = f"{base}_USDC_PERP" if f"{base}_USDC_PERP" in bp_markets else None
            px_sym = f"{base}-USD-PERP" if f"{base}-USD-PERP" in px_markets else None
            ex_sym = f"{base}-USD" if f"{base}-USD" in ex_markets else None
            
            # Need at least 2 exchanges for arbitrage
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
            title="BACKPACK x PARADEX x EXTENDED",
            show_header=True,
            header_style="bold cyan",
            border_style="dim",
        )
        
        table.add_column("PAIR", style="white", width=8)
        table.add_column("BP-PX", justify="right", width=10)
        table.add_column("BP-EX", justify="right", width=10)
        table.add_column("PX-EX", justify="right", width=10)
        table.add_column("BEST", justify="right", width=10)
        table.add_column("ROUTE", justify="center", width=10)
        
        # Sort by best spread
        def get_best(p):
            spreads = [p.bp_px_spread, p.bp_ex_spread, p.px_ex_spread]
            return max(spreads) if spreads else 0
        
        sorted_pairs = sorted(
            [p for p in self.pairs.values() if get_best(p) != 0],
            key=get_best,
            reverse=True
        )
        
        for pair in sorted_pairs:
            # Format spreads
            def fmt_spread(spread, threshold):
                if spread == 0:
                    return "[dim]--[/]"
                elif spread >= threshold:
                    return f"[bold green]*{spread:+.3f}%[/]"
                elif spread > 0:
                    return f"[green]{spread:+.4f}%[/]"
                elif spread > -0.1:
                    return f"[yellow]{spread:+.4f}%[/]"
                else:
                    return f"[red]{spread:+.4f}%[/]"
            
            bp_px = fmt_spread(pair.bp_px_spread, self.threshold)
            bp_ex = fmt_spread(pair.bp_ex_spread, self.threshold)
            px_ex = fmt_spread(pair.px_ex_spread, self.threshold)
            
            # Find best
            spreads = [
                (pair.bp_px_spread, "BP-PX", pair.bp_px_dir),
                (pair.bp_ex_spread, "BP-EX", pair.bp_ex_dir),
                (pair.px_ex_spread, "PX-EX", pair.px_ex_dir),
            ]
            best = max(spreads, key=lambda x: x[0])
            best_str = fmt_spread(best[0], self.threshold)
            route = f"{best[1]} {best[2]}" if best[0] != 0 else ""
            
            table.add_row(pair.base, bp_px, bp_ex, px_ex, best_str, route)
        
        return table

    def _build_status(self) -> Text:
        """Build status line."""
        time_str = datetime.now().strftime('%H:%M:%S')
        runtime = datetime.now() - self._start_time if self._start_time else None
        runtime_str = str(runtime).split('.')[0] if runtime else "0:00:00"
        
        opportunities = sum(1 for p in self.pairs.values() 
                          if max(p.bp_px_spread, p.bp_ex_spread, p.px_ex_spread) >= self.threshold)
        
        status = Text()
        status.append(f"  {time_str}", style="dim")
        status.append(f"  |  Updates: {self._update_count:,}", style="cyan")
        status.append(f"  |  Pairs: {len(self.pairs)}", style="white")
        status.append(f"  |  Runtime: {runtime_str}", style="dim")
        
        if opportunities > 0:
            status.append(f"  |  OPPORTUNITIES: {opportunities}", style="bold green")
        
        return status

    async def start(self) -> None:
        """Start scanner."""
        self._start_time = datetime.now()
        self._running = True
        
        console.clear()
        console.print("[bold]Discovering pairs across 3 exchanges...[/]")
        
        await self.discover_pairs()
        
        bp_count = sum(1 for p in self.pairs.values() if p.bp_symbol)
        px_count = sum(1 for p in self.pairs.values() if p.px_symbol)
        ex_count = sum(1 for p in self.pairs.values() if p.ex_symbol)
        
        console.print(f"  Backpack: {bp_count} | Paradex: {px_count} | Extended: {ex_count}")
        console.print(f"  Total arbitrage pairs: {len(self.pairs)}")
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
        """Stream Backpack orderbooks."""
        ws_url = "wss://ws.backpack.exchange"
        
        while self._running:
            try:
                async with websockets.connect(ws_url) as ws:
                    # Subscribe
                    for base, pair in self.pairs.items():
                        if pair.bp_symbol:
                            await ws.send(orjson.dumps({
                                "method": "SUBSCRIBE",
                                "params": [f"depth.{pair.bp_symbol}"]
                            }).decode())
                    
                    # Initial snapshots
                    async with httpx.AsyncClient(timeout=30) as client:
                        for base, pair in self.pairs.items():
                            if pair.bp_symbol and pair.bp_book:
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
                                    self._calc_spreads(base)
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
                                if base in self.pairs and self.pairs[base].bp_book:
                                    payload = data.get('data', {})
                                    self.pairs[base].bp_book.apply_delta(
                                        payload.get('b', []),
                                        payload.get('a', []),
                                        payload.get('U', 0),
                                        payload.get('u', 0)
                                    )
                                    self._update_count += 1
                                    self._calc_spreads(base)
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
                        if pair.px_symbol:
                            channel = f"order_book.{pair.px_symbol}.snapshot@15@100ms"
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
                                        
                                        if base in self.pairs and self.pairs[base].px_book:
                                            inserts = book_data.get("inserts", [])
                                            bids = [[e["price"], e["size"]] for e in inserts if e.get("side") == "BUY"]
                                            asks = [[e["price"], e["size"]] for e in inserts if e.get("side") == "SELL"]
                                            seq = book_data.get("seq_no", 0)
                                            
                                            self.pairs[base].px_book.apply_snapshot(bids, asks, seq)
                                            self._update_count += 1
                                            self._calc_spreads(base)
                        except:
                            pass
            except:
                if self._running:
                    await asyncio.sleep(2)

    async def _stream_extended(self) -> None:
        """Stream Extended orderbooks."""
        base_url = "wss://api.starknet.extended.exchange/stream.extended.exchange/v1/orderbooks"
        
        # Extended uses separate WS per market, so we batch them
        tasks = []
        for base, pair in self.pairs.items():
            if pair.ex_symbol:
                tasks.append(self._stream_extended_single(base, pair.ex_symbol))
        
        if tasks:
            await asyncio.gather(*tasks)

    async def _stream_extended_single(self, base: str, symbol: str) -> None:
        """Stream single Extended orderbook."""
        ws_url = f"wss://api.starknet.extended.exchange/stream.extended.exchange/v1/orderbooks/{symbol}"
        
        while self._running:
            try:
                async with websockets.connect(ws_url) as ws:
                    async for message in ws:
                        if not self._running:
                            break
                        try:
                            data = orjson.loads(message)
                            msg_type = data.get("type", "")
                            book = data.get("data", {})
                            
                            if base in self.pairs and self.pairs[base].ex_book:
                                bids = [[b["p"], b["q"]] for b in book.get("b", [])]
                                asks = [[a["p"], a["q"]] for a in book.get("a", [])]
                                seq = data.get("seq", 0)
                                
                                if msg_type == "SNAPSHOT":
                                    self.pairs[base].ex_book.apply_snapshot(bids, asks, seq)
                                else:
                                    # Apply delta
                                    self.pairs[base].ex_book.apply_delta(bids, asks, seq, seq)
                                
                                self._update_count += 1
                                self._calc_spreads(base)
                        except:
                            pass
            except:
                if self._running:
                    await asyncio.sleep(2)

    def _calc_spreads(self, base: str) -> None:
        """Calculate spreads between all exchange pairs."""
        pair = self.pairs.get(base)
        if not pair:
            return
        
        # Get best prices from each exchange
        bp_bid = pair.bp_book.get_best_bid() if pair.bp_book else None
        bp_ask = pair.bp_book.get_best_ask() if pair.bp_book else None
        px_bid = pair.px_book.get_best_bid() if pair.px_book else None
        px_ask = pair.px_book.get_best_ask() if pair.px_book else None
        ex_bid = pair.ex_book.get_best_bid() if pair.ex_book else None
        ex_ask = pair.ex_book.get_best_ask() if pair.ex_book else None
        
        # BP <-> PX
        if all([bp_bid, bp_ask, px_bid, px_ask]):
            bp_to_px = float((px_bid[0] - bp_ask[0]) / bp_ask[0] * 100)
            px_to_bp = float((bp_bid[0] - px_ask[0]) / px_ask[0] * 100)
            if bp_to_px > px_to_bp:
                pair.bp_px_spread = bp_to_px
                pair.bp_px_dir = ">"
            else:
                pair.bp_px_spread = px_to_bp
                pair.bp_px_dir = "<"
        
        # BP <-> EX
        if all([bp_bid, bp_ask, ex_bid, ex_ask]):
            bp_to_ex = float((ex_bid[0] - bp_ask[0]) / bp_ask[0] * 100)
            ex_to_bp = float((bp_bid[0] - ex_ask[0]) / ex_ask[0] * 100)
            if bp_to_ex > ex_to_bp:
                pair.bp_ex_spread = bp_to_ex
                pair.bp_ex_dir = ">"
            else:
                pair.bp_ex_spread = ex_to_bp
                pair.bp_ex_dir = "<"
        
        # PX <-> EX
        if all([px_bid, px_ask, ex_bid, ex_ask]):
            px_to_ex = float((ex_bid[0] - px_ask[0]) / px_ask[0] * 100)
            ex_to_px = float((px_bid[0] - ex_ask[0]) / ex_ask[0] * 100)
            if px_to_ex > ex_to_px:
                pair.px_ex_spread = px_to_ex
                pair.px_ex_dir = ">"
            else:
                pair.px_ex_spread = ex_to_px
                pair.px_ex_dir = "<"

    async def stop(self) -> None:
        """Stop scanner."""
        self._running = False
        runtime = datetime.now() - self._start_time if self._start_time else None
        
        console.print()
        console.rule("[bold]SESSION SUMMARY[/]")
        console.print(f"  Runtime: {str(runtime).split('.')[0] if runtime else 'N/A'}")
        console.print(f"  Updates: {self._update_count:,}")
        console.print(f"  Pairs monitored: {len(self.pairs)}")
        console.rule()


async def main():
    scanner = TriExchangeScanner()
    try:
        await scanner.start()
    except KeyboardInterrupt:
        pass
    finally:
        await scanner.stop()


if __name__ == "__main__":
    asyncio.run(main())
