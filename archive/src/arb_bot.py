"""
Full Arbitrage Scanner + Executor
Combines liquidity scanning with delta-neutral execution.
Paper Trading Mode.
"""

import asyncio
import httpx
import websockets
import orjson
import logging
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich.layout import Layout

from src.config import config
from src.core.orderbook import Orderbook
from src.delta_neutral import PositionManager, ArbitrageEngine, PositionStatus

logging.disable(logging.CRITICAL)
console = Console()

# Fee rates
BP_FEE = 0.0002  # 0.02%
PX_FEE = 0.0000  # 0%


@dataclass
class PairData:
    bp_symbol: str
    px_symbol: str
    bp_book: Orderbook
    px_book: Orderbook
    spread: float = 0.0
    direction: str = ""
    exec_qty: float = 0.0
    exec_value: float = 0.0
    net_profit: float = 0.0
    buy_price: float = 0.0
    sell_price: float = 0.0
    buy_exchange: str = ""
    sell_exchange: str = ""
    buy_symbol: str = ""
    sell_symbol: str = ""


class ArbitrageBot:
    """Full arbitrage bot with scanning and execution."""
    
    def __init__(self, paper_trading: bool = True):
        self.pairs: Dict[str, PairData] = {}
        self._running = False
        self._update_count = 0
        self._start_time = None
        self._blocklist = {"ORDER", "TRX"}
        
        # Engine
        self.engine = ArbitrageEngine(paper_trading=paper_trading)
        self.paper_trading = paper_trading
        
        # Thresholds
        self.open_threshold = 0.15
        self.close_threshold = 0.02
        
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

    def _build_scanner_table(self) -> Table:
        """Build scanner table showing opportunities."""
        table = Table(show_header=True, header_style="bold cyan", border_style="dim")
        
        table.add_column("PAIR", width=8)
        table.add_column("SPREAD", justify="right", width=10)
        table.add_column("QTY", justify="right", width=10)
        table.add_column("VALUE", justify="right", width=10)
        table.add_column("PROFIT", justify="right", width=9)
        table.add_column("ROUTE", justify="center", width=8)
        table.add_column("ACTION", justify="center", width=8)
        
        # Sort by spread
        sorted_pairs = sorted(
            [(b, p) for b, p in self.pairs.items() if p.spread != 0],
            key=lambda x: x[1].spread,
            reverse=True
        )[:15]  # Top 15
        
        for base, pair in sorted_pairs:
            # Spread color
            if pair.spread >= self.open_threshold:
                spread_str = f"[bold green]*{pair.spread:+.3f}%[/]"
                action = "[bold green]OPEN[/]"
            elif pair.spread > 0:
                spread_str = f"[green]{pair.spread:+.4f}%[/]"
                action = "[dim]wait[/]"
            elif pair.spread > -0.05:
                spread_str = f"[yellow]{pair.spread:+.4f}%[/]"
                action = "[dim]-[/]"
            else:
                spread_str = f"[red]{pair.spread:+.4f}%[/]"
                action = "[dim]-[/]"
            
            # Format values
            qty_str = f"{pair.exec_qty:.4f}" if pair.exec_qty < 100 else f"{pair.exec_qty:.1f}"
            value_str = f"${pair.exec_value:,.0f}" if pair.exec_value >= 100 else f"${pair.exec_value:.2f}"
            profit_str = f"[green]${pair.net_profit:.2f}[/]" if pair.net_profit > 0 else f"[dim]${pair.net_profit:.2f}[/]"
            route_str = f"[cyan]{pair.direction}[/]"
            
            table.add_row(base, spread_str, qty_str, value_str, profit_str, route_str, action)
        
        return table

    def _build_positions_table(self) -> Table:
        """Build positions table."""
        table = Table(show_header=True, header_style="bold magenta", border_style="dim")
        
        table.add_column("POS", width=10)
        table.add_column("STATUS", width=8)
        table.add_column("OPEN", justify="right", width=8)
        table.add_column("CURR", justify="right", width=8)
        table.add_column("P&L", justify="right", width=9)
        
        for pos in list(self.engine.position_manager.positions.values())[-5:]:
            status_color = {
                PositionStatus.OPEN: "green",
                PositionStatus.CLOSED: "dim",
                PositionStatus.PENDING_OPEN: "yellow",
            }.get(pos.status, "white")
            
            # Get current spread for open positions
            curr_spread = "-"
            if pos.status == PositionStatus.OPEN:
                pair = self.pairs.get(pos.base)
                if pair:
                    curr_spread = f"{pair.spread:+.3f}%"
            elif pos.status == PositionStatus.CLOSED:
                curr_spread = f"{pos.close_spread:+.3f}%"
            
            pnl_str = f"${pos.realized_pnl:.2f}" if pos.status == PositionStatus.CLOSED else "-"
            
            table.add_row(
                pos.id,
                f"[{status_color}]{pos.status.value[:6]}[/]",
                f"{pos.open_spread:+.3f}%",
                curr_spread,
                pnl_str,
            )
        
        return table

    def _build_status(self) -> Text:
        """Build status line."""
        time_str = datetime.now().strftime('%H:%M:%S')
        runtime = datetime.now() - self._start_time if self._start_time else None
        runtime_str = str(runtime).split('.')[0] if runtime else "0:00:00"
        
        # Count opportunities
        opportunities = sum(1 for p in self.pairs.values() if p.spread >= self.open_threshold)
        open_positions = len(self.engine.position_manager.get_open_positions())
        total_pnl = self.engine.position_manager.get_total_pnl()
        
        mode = "[yellow]PAPER[/]" if self.paper_trading else "[red]LIVE[/]"
        
        status = Text()
        status.append(f"  {time_str}", style="dim")
        status.append(f"  |  Mode: ")
        status.append(mode)
        status.append(f"  |  Updates: {self._update_count:,}", style="cyan")
        status.append(f"  |  Runtime: {runtime_str}", style="dim")
        status.append(f"  |  Opps: {opportunities}", style="green" if opportunities > 0 else "dim")
        status.append(f"  |  Positions: {open_positions}", style="yellow" if open_positions > 0 else "dim")
        status.append(f"  |  P&L: ${total_pnl:.2f}", style="bold green" if total_pnl > 0 else "white")
        
        return status

    def _build_display(self) -> Panel:
        """Build full display."""
        layout = Layout()
        layout.split_row(
            Layout(self._build_scanner_table(), name="scanner", ratio=2),
            Layout(self._build_positions_table(), name="positions", ratio=1),
        )
        
        return Panel(layout, title=self._build_status(), border_style="blue")

    async def start(self) -> None:
        """Start the bot."""
        self._start_time = datetime.now()
        self._running = True
        
        console.clear()
        console.print("[bold]ARBITRAGE BOT - Starting...[/]")
        console.print(f"Mode: {'PAPER TRADING' if self.paper_trading else 'LIVE'}")
        console.print(f"Open threshold: >= {self.open_threshold}%")
        console.print(f"Close threshold: <= {self.close_threshold}%")
        console.print()
        
        # Discover pairs
        pairs = await self.discover_pairs()
        console.print(f"Discovered {len(pairs)} pairs")
        
        for bp_sym, px_sym in pairs:
            base = bp_sym.split('_')[0]
            self.pairs[base] = PairData(
                bp_symbol=bp_sym,
                px_symbol=px_sym,
                bp_book=Orderbook(exchange="backpack", symbol=bp_sym),
                px_book=Orderbook(exchange="paradex", symbol=px_sym),
            )
        
        console.print()
        
        # Start streams
        tasks = [
            asyncio.create_task(self._stream_backpack()),
            asyncio.create_task(self._stream_paradex()),
            asyncio.create_task(self._execution_loop()),
        ]
        
        try:
            with Live(self._build_display(), refresh_per_second=2, console=console) as live:
                while self._running:
                    await asyncio.sleep(0.5)
                    live.update(self._build_display())
        except asyncio.CancelledError:
            pass
        
        for task in tasks:
            task.cancel()

    async def _execution_loop(self) -> None:
        """Main execution loop - checks for open/close opportunities."""
        while self._running:
            await asyncio.sleep(1)  # Check every second
            
            # Check for close opportunities first
            spreads = {base: pair.spread for base, pair in self.pairs.items()}
            await self.engine.check_close_opportunities(spreads)
            
            # Check for open opportunities
            for base, pair in self.pairs.items():
                if pair.spread >= self.open_threshold and pair.exec_qty > 0:
                    # Try to open
                    await self.engine.check_open_opportunity(
                        base=base,
                        buy_exchange=pair.buy_exchange,
                        buy_symbol=pair.buy_symbol,
                        buy_price=pair.buy_price,
                        buy_qty=pair.exec_qty,
                        sell_exchange=pair.sell_exchange,
                        sell_symbol=pair.sell_symbol,
                        sell_price=pair.sell_price,
                        sell_qty=pair.exec_qty,
                        spread=pair.spread,
                    )

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
        """Calculate arbitrage opportunity."""
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
        bp_to_px = float((px_bid[0] - bp_ask[0]) / bp_ask[0] * 100)
        
        # Direction 2: Buy PX, Sell BP
        px_to_bp = float((bp_bid[0] - px_ask[0]) / px_ask[0] * 100)
        
        if bp_to_px > px_to_bp:
            pair.spread = bp_to_px
            pair.direction = "BP>PX"
            pair.buy_price = float(bp_ask[0])
            pair.buy_exchange = "BP"
            pair.buy_symbol = pair.bp_symbol
            pair.sell_price = float(px_bid[0])
            pair.sell_exchange = "PX"
            pair.sell_symbol = pair.px_symbol
            buy_qty = float(bp_ask[1])
            sell_qty = float(px_bid[1])
        else:
            pair.spread = px_to_bp
            pair.direction = "PX>BP"
            pair.buy_price = float(px_ask[0])
            pair.buy_exchange = "PX"
            pair.buy_symbol = pair.px_symbol
            pair.sell_price = float(bp_bid[0])
            pair.sell_exchange = "BP"
            pair.sell_symbol = pair.bp_symbol
            buy_qty = float(px_ask[1])
            sell_qty = float(bp_bid[1])
        
        pair.exec_qty = min(buy_qty, sell_qty)
        pair.exec_value = pair.exec_qty * pair.buy_price
        
        # Net profit after fees
        gross = pair.exec_qty * (pair.sell_price - pair.buy_price)
        buy_fee = pair.exec_value * (BP_FEE if pair.buy_exchange == "BP" else PX_FEE)
        sell_fee = (pair.exec_qty * pair.sell_price) * (BP_FEE if pair.sell_exchange == "BP" else PX_FEE)
        pair.net_profit = gross - buy_fee - sell_fee

    async def stop(self) -> None:
        """Stop the bot."""
        self._running = False
        console.print()
        console.rule("[bold]SESSION SUMMARY[/]")
        console.print(f"  Total P&L: ${self.engine.position_manager.get_total_pnl():.2f}")
        console.rule()


async def main():
    bot = ArbitrageBot(paper_trading=True)
    try:
        await bot.start()
    except KeyboardInterrupt:
        pass
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
