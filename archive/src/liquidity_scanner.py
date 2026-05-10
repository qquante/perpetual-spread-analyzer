"""
Multi-Pair Arbitrage Scanner with Liquidity Analysis
Backpack x Paradex - Shows executable quantity and profit
"""

import asyncio
import httpx
import websockets
import orjson
import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

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
BP_TAKER_FEE = 0.0002  # 0.02%
PX_TAKER_FEE = 0.0000  # 0% for retail


@dataclass
class PairData:
    bp_symbol: str
    px_symbol: str
    bp_book: Orderbook
    px_book: Orderbook
    # Spread info
    spread: float = 0.0
    direction: str = ""
    # Liquidity info
    exec_qty: float = 0.0
    exec_value: float = 0.0
    gross_profit: float = 0.0
    net_profit: float = 0.0
    # Prices
    buy_price: float = 0.0
    sell_price: float = 0.0


class LiquidityScanner:
    """Arbitrage scanner with liquidity analysis."""
    
    def __init__(self):
        self.pairs: Dict[str, PairData] = {}
        self._running = False
        self._update_count = 0
        self._start_time = None
        self.threshold = config.trading.profit_threshold_percent
        self._blocklist = {"ORDER", "TRX"}
        
    async def discover_pairs(self) -> List[Tuple[str, str]]:
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

    def _build_table(self) -> Table:
        """Build display table with liquidity info."""
        table = Table(
            title="BACKPACK x PARADEX - LIQUIDITY SCANNER",
            show_header=True,
            header_style="bold cyan",
            border_style="dim",
        )
        
        table.add_column("PAIR", style="white", width=8)
        table.add_column("SPREAD", justify="right", width=10)
        table.add_column("QTY", justify="right", width=10)
        table.add_column("VALUE", justify="right", width=12)
        table.add_column("PROFIT", justify="right", width=10)
        table.add_column("BUY @", justify="right", width=12)
        table.add_column("SELL @", justify="right", width=12)
        table.add_column("ROUTE", justify="center", width=8)
        
        # Sort by net profit
        sorted_pairs = sorted(
            [(b, p) for b, p in self.pairs.items() if p.net_profit != 0],
            key=lambda x: x[1].net_profit,
            reverse=True
        )
        
        for base, pair in sorted_pairs:
            # Format spread
            if pair.spread >= self.threshold:
                spread_str = f"[bold green]*{pair.spread:+.3f}%[/]"
            elif pair.spread > 0:
                spread_str = f"[green]{pair.spread:+.4f}%[/]"
            elif pair.spread > -0.1:
                spread_str = f"[yellow]{pair.spread:+.4f}%[/]"
            else:
                spread_str = f"[red]{pair.spread:+.4f}%[/]"
            
            # Format quantity
            if pair.exec_qty >= 1:
                qty_str = f"{pair.exec_qty:.2f}"
            elif pair.exec_qty >= 0.01:
                qty_str = f"{pair.exec_qty:.4f}"
            else:
                qty_str = f"{pair.exec_qty:.6f}"
            
            # Format value
            if pair.exec_value >= 1000:
                value_str = f"${pair.exec_value:,.0f}"
            else:
                value_str = f"${pair.exec_value:.2f}"
            
            # Format profit with color
            if pair.net_profit > 0:
                profit_str = f"[green]${pair.net_profit:.2f}[/]"
            else:
                profit_str = f"[red]${pair.net_profit:.2f}[/]"
            
            # Route
            route_str = f"[cyan]{pair.direction}[/]" if pair.spread > 0 else f"[dim]{pair.direction}[/]"
            
            table.add_row(
                base,
                spread_str,
                qty_str,
                value_str,
                profit_str,
                f"${pair.buy_price:,.2f}",
                f"${pair.sell_price:,.2f}",
                route_str,
            )
        
        return table

    def _build_status(self) -> Text:
        """Build status line."""
        time_str = datetime.now().strftime('%H:%M:%S')
        runtime = datetime.now() - self._start_time if self._start_time else None
        runtime_str = str(runtime).split('.')[0] if runtime else "0:00:00"
        
        # Count profitable opportunities
        profitable = sum(1 for p in self.pairs.values() if p.net_profit > 0)
        total_profit = sum(p.net_profit for p in self.pairs.values() if p.net_profit > 0)
        
        status = Text()
        status.append(f"  {time_str}", style="dim")
        status.append(f"  |  Updates: {self._update_count:,}", style="cyan")
        status.append(f"  |  Pairs: {len(self.pairs)}", style="white")
        status.append(f"  |  Runtime: {runtime_str}", style="dim")
        
        if profitable > 0:
            status.append(f"  |  Profitable: {profitable}", style="green")
            status.append(f"  |  Total: ${total_profit:.2f}", style="bold green")
        
        return status

    async def start(self) -> None:
        """Start scanner."""
        self._start_time = datetime.now()
        self._running = True
        
        console.clear()
        console.print("[bold]Discovering pairs...[/]", end="")
        
        pairs = await self.discover_pairs()
        console.print(f" [green]{len(pairs)} pairs found[/]")
        
        for bp_sym, px_sym in pairs:
            base = bp_sym.split('_')[0]
            self.pairs[base] = PairData(
                bp_symbol=bp_sym,
                px_symbol=px_sym,
                bp_book=Orderbook(exchange="backpack", symbol=bp_sym),
                px_book=Orderbook(exchange="paradex", symbol=px_sym),
            )
        
        console.print(f"[bold]Fees:[/] BP={BP_TAKER_FEE*100:.2f}% | PX={PX_TAKER_FEE*100:.2f}%")
        console.print()
        
        tasks = [
            asyncio.create_task(self._stream_backpack()),
            asyncio.create_task(self._stream_paradex()),
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
                    for base, pair in self.pairs.items():
                        await ws.send(orjson.dumps({
                            "method": "SUBSCRIBE",
                            "params": [f"depth.{pair.bp_symbol}"]
                        }).decode())
                    
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
                                self._calc_opportunity(base)
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
                                    self._calc_opportunity(base)
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
                                        
                                        if base in self.pairs:
                                            inserts = book_data.get("inserts", [])
                                            bids = [[e["price"], e["size"]] for e in inserts if e.get("side") == "BUY"]
                                            asks = [[e["price"], e["size"]] for e in inserts if e.get("side") == "SELL"]
                                            seq = book_data.get("seq_no", 0)
                                            
                                            self.pairs[base].px_book.apply_snapshot(bids, asks, seq)
                                            self._update_count += 1
                                            self._calc_opportunity(base)
                        except:
                            pass
            except:
                if self._running:
                    await asyncio.sleep(2)

    def _calc_opportunity(self, base: str) -> None:
        """Calculate arbitrage opportunity with liquidity."""
        pair = self.pairs.get(base)
        if not pair:
            return
        
        bp_bid = pair.bp_book.get_best_bid()
        bp_ask = pair.bp_book.get_best_ask()
        px_bid = pair.px_book.get_best_bid()
        px_ask = pair.px_book.get_best_ask()
        
        if not all([bp_bid, bp_ask, px_bid, px_ask]):
            return
        
        # Direction 1: Buy BP, Sell PX
        bp_to_px_spread = float((px_bid[0] - bp_ask[0]) / bp_ask[0] * 100)
        
        # Direction 2: Buy PX, Sell BP
        px_to_bp_spread = float((bp_bid[0] - px_ask[0]) / px_ask[0] * 100)
        
        # Pick best direction
        if bp_to_px_spread > px_to_bp_spread:
            pair.spread = bp_to_px_spread
            pair.direction = "BP>PX"
            buy_price = float(bp_ask[0])
            buy_qty = float(bp_ask[1])
            sell_price = float(px_bid[0])
            sell_qty = float(px_bid[1])
            buy_fee = BP_TAKER_FEE
            sell_fee = PX_TAKER_FEE
        else:
            pair.spread = px_to_bp_spread
            pair.direction = "PX>BP"
            buy_price = float(px_ask[0])
            buy_qty = float(px_ask[1])
            sell_price = float(bp_bid[0])
            sell_qty = float(bp_bid[1])
            buy_fee = PX_TAKER_FEE
            sell_fee = BP_TAKER_FEE
        
        # Executable quantity = min of both sides
        pair.exec_qty = min(buy_qty, sell_qty)
        pair.buy_price = buy_price
        pair.sell_price = sell_price
        
        # Calculate value and profit
        pair.exec_value = pair.exec_qty * buy_price
        pair.gross_profit = pair.exec_qty * (sell_price - buy_price)
        
        # Net profit after fees
        buy_cost = pair.exec_value * buy_fee
        sell_cost = (pair.exec_qty * sell_price) * sell_fee
        pair.net_profit = pair.gross_profit - buy_cost - sell_cost

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
    scanner = LiquidityScanner()
    try:
        await scanner.start()
    except KeyboardInterrupt:
        pass
    finally:
        await scanner.stop()


if __name__ == "__main__":
    asyncio.run(main())
