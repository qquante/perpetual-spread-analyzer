"""
Tri-Exchange Arbitrage Bot
Backpack x Paradex x Extended
Paper Trading Mode with Delta-Neutral Execution
"""

import asyncio
import httpx
import websockets
import orjson
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from src.config import config
from src.core.orderbook import Orderbook
from src.delta_neutral import PositionManager, ArbitrageEngine, PositionStatus

logging.disable(logging.CRITICAL)
console = Console()

# Fee rates
BP_FEE = 0.0002   # 0.02%
PX_FEE = 0.0000   # 0%
EX_FEE = 0.00025  # 0.025%


@dataclass
class Opportunity:
    """Single arbitrage opportunity between two exchanges."""
    spread: float
    exec_qty: float
    exec_value: float
    net_profit: float
    buy_exchange: str
    sell_exchange: str
    buy_price: float
    sell_price: float
    buy_symbol: str
    sell_symbol: str


@dataclass
class PairData:
    base: str
    bp_symbol: Optional[str] = None
    px_symbol: Optional[str] = None
    ex_symbol: Optional[str] = None
    bp_book: Optional[Orderbook] = None
    px_book: Optional[Orderbook] = None
    ex_book: Optional[Orderbook] = None
    # Best opportunity across all exchange pairs
    best_opp: Optional[Opportunity] = None


class TriExchangeBot:
    """Tri-exchange arbitrage bot with execution."""
    
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
            ex_map = {m['name'].split('-')[0]: m['name'] for m in r3.json()['data'] 
                     if m.get('status') == 'ACTIVE' and '-USD' in m['name']}
        
        # Get all unique bases
        all_bases = set(bp_map.keys()) | set(px_map.keys()) | set(ex_map.keys())
        
        for base in all_bases:
            if base in self._blocklist:
                continue
            
            bp_sym = bp_map.get(base)
            px_sym = px_map.get(base)
            ex_sym = ex_map.get(base)
            
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

    def _build_scanner_table(self) -> Table:
        """Build scanner table."""
        table = Table(show_header=True, header_style="bold cyan", border_style="dim")
        
        table.add_column("PAIR", width=7)
        table.add_column("SPREAD", justify="right", width=9)
        table.add_column("QTY", justify="right", width=10)
        table.add_column("VALUE", justify="right", width=9)
        table.add_column("PROFIT", justify="right", width=8)
        table.add_column("ROUTE", justify="center", width=7)
        table.add_column("ACT", justify="center", width=5)
        
        # Sort by spread
        pairs_with_opp = [(b, p) for b, p in self.pairs.items() if p.best_opp]
        sorted_pairs = sorted(pairs_with_opp, key=lambda x: x[1].best_opp.spread, reverse=True)
        
        for base, pair in sorted_pairs:
            opp = pair.best_opp
            
            # Format spread
            if opp.spread >= self.open_threshold:
                spread_str = f"[bold green]*{opp.spread:+.2f}%[/]"
                action = "[bold green]GO[/]"
            elif opp.spread > 0.05:
                spread_str = f"[green]{opp.spread:+.3f}%[/]"
                action = "[dim].[/]"
            elif opp.spread > 0:
                spread_str = f"[yellow]{opp.spread:+.4f}%[/]"
                action = "[dim].[/]"
            else:
                spread_str = f"[red]{opp.spread:+.4f}%[/]"
                action = "[dim]-[/]"
            
            # Format values
            qty_str = f"{opp.exec_qty:.2f}" if opp.exec_qty < 1000 else f"{opp.exec_qty:.0f}"
            value_str = f"${opp.exec_value:,.0f}" if opp.exec_value >= 100 else f"${opp.exec_value:.1f}"
            profit_str = f"[green]${opp.net_profit:.2f}[/]" if opp.net_profit > 0 else f"[dim]${opp.net_profit:.2f}[/]"
            
            # Route shows exchanges
            route_str = f"[cyan]{opp.buy_exchange}>{opp.sell_exchange}[/]"
            
            table.add_row(base, spread_str, qty_str, value_str, profit_str, route_str, action)
        
        return table

    def _build_positions_table(self) -> Table:
        """Build positions table."""
        table = Table(show_header=True, header_style="bold magenta", border_style="dim", width=30)
        
        table.add_column("POS", width=8)
        table.add_column("ST", width=4)
        table.add_column("OPEN", justify="right", width=6)
        table.add_column("P&L", justify="right", width=7)
        
        for pos in list(self.engine.position_manager.positions.values())[-8:]:
            status_color = {
                PositionStatus.OPEN: "green",
                PositionStatus.CLOSED: "dim",
                PositionStatus.PENDING_OPEN: "yellow",
            }.get(pos.status, "white")
            
            status_short = pos.status.value[:3].upper()
            pnl_str = f"${pos.realized_pnl:.2f}" if pos.status == PositionStatus.CLOSED else "-"
            
            table.add_row(
                pos.id,
                f"[{status_color}]{status_short}[/]",
                f"{pos.open_spread:.2f}%",
                pnl_str,
            )
        
        return table

    def _build_status(self) -> Text:
        """Build status line."""
        time_str = datetime.now().strftime('%H:%M:%S')
        runtime = datetime.now() - self._start_time if self._start_time else None
        runtime_str = str(runtime).split('.')[0] if runtime else "0:00:00"
        
        # Count exchanges per pair
        bp_count = sum(1 for p in self.pairs.values() if p.bp_symbol)
        px_count = sum(1 for p in self.pairs.values() if p.px_symbol)  
        ex_count = sum(1 for p in self.pairs.values() if p.ex_symbol)
        
        opportunities = sum(1 for p in self.pairs.values() 
                          if p.best_opp and p.best_opp.spread >= self.open_threshold)
        open_positions = len(self.engine.position_manager.get_open_positions())
        total_pnl = self.engine.position_manager.get_total_pnl()
        
        mode = "[yellow]PAPER[/]" if self.paper_trading else "[red]LIVE[/]"
        
        status = Text()
        status.append(f" {time_str}", style="dim")
        status.append(f" | ")
        status.append(mode)
        status.append(f" | BP:{bp_count} PX:{px_count} EX:{ex_count}", style="cyan")
        status.append(f" | Upd:{self._update_count:,}", style="dim")
        status.append(f" | Opps:{opportunities}", style="green" if opportunities > 0 else "dim")
        status.append(f" | Pos:{open_positions}", style="yellow" if open_positions > 0 else "dim")
        status.append(f" | P&L:${total_pnl:.2f}", style="bold green" if total_pnl > 0 else "white")
        
        return status

    async def start(self) -> None:
        """Start the bot."""
        self._start_time = datetime.now()
        self._running = True
        
        console.clear()
        console.print("[bold]TRI-EXCHANGE ARBITRAGE BOT[/]")
        console.print(f"Mode: {'PAPER TRADING' if self.paper_trading else 'LIVE'}")
        console.print(f"Thresholds: Open >= {self.open_threshold}% | Close <= {self.close_threshold}%")
        console.print()
        console.print("Discovering pairs...")
        
        await self.discover_pairs()
        
        bp = sum(1 for p in self.pairs.values() if p.bp_symbol)
        px = sum(1 for p in self.pairs.values() if p.px_symbol)
        ex = sum(1 for p in self.pairs.values() if p.ex_symbol)
        
        console.print(f"  Backpack: {bp} | Paradex: {px} | Extended: {ex}")
        console.print(f"  Total pairs: {len(self.pairs)}")
        console.print()
        
        # Start streams
        tasks = [
            asyncio.create_task(self._stream_backpack()),
            asyncio.create_task(self._stream_paradex()),
            asyncio.create_task(self._stream_extended()),
            asyncio.create_task(self._execution_loop()),
        ]
        
        try:
            with Live(Panel(self._build_scanner_table(), title=self._build_status()), 
                      refresh_per_second=2, console=console) as live:
                while self._running:
                    await asyncio.sleep(0.5)
                    live.update(Panel(self._build_scanner_table(), title=self._build_status()))
        except asyncio.CancelledError:
            pass
        
        for task in tasks:
            task.cancel()

    async def _execution_loop(self) -> None:
        """Main execution loop."""
        while self._running:
            await asyncio.sleep(1)
            
            # Check for close first
            spreads = {base: (p.best_opp.spread if p.best_opp else 0) for base, p in self.pairs.items()}
            await self.engine.check_close_opportunities(spreads)
            
            # Check for open
            for base, pair in self.pairs.items():
                opp = pair.best_opp
                if opp and opp.spread >= self.open_threshold and opp.exec_qty > 0:
                    await self.engine.check_open_opportunity(
                        base=base,
                        buy_exchange=opp.buy_exchange,
                        buy_symbol=opp.buy_symbol,
                        buy_price=opp.buy_price,
                        buy_qty=opp.exec_qty,
                        sell_exchange=opp.sell_exchange,
                        sell_symbol=opp.sell_symbol,
                        sell_price=opp.sell_price,
                        sell_qty=opp.exec_qty,
                        spread=opp.spread,
                    )

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
        
        # Get prices from each exchange
        prices = {}
        if pair.bp_book:
            bid = pair.bp_book.get_best_bid()
            ask = pair.bp_book.get_best_ask()
            if bid and ask:
                prices['BP'] = {
                    'bid': (float(bid[0]), float(bid[1])), 
                    'ask': (float(ask[0]), float(ask[1])), 
                    'fee': BP_FEE,
                    'symbol': pair.bp_symbol
                }
        if pair.px_book:
            bid = pair.px_book.get_best_bid()
            ask = pair.px_book.get_best_ask()
            if bid and ask:
                prices['PX'] = {
                    'bid': (float(bid[0]), float(bid[1])), 
                    'ask': (float(ask[0]), float(ask[1])), 
                    'fee': PX_FEE,
                    'symbol': pair.px_symbol
                }
        if pair.ex_book:
            bid = pair.ex_book.get_best_bid()
            ask = pair.ex_book.get_best_ask()
            if bid and ask:
                prices['EX'] = {
                    'bid': (float(bid[0]), float(bid[1])), 
                    'ask': (float(ask[0]), float(ask[1])), 
                    'fee': EX_FEE,
                    'symbol': pair.ex_symbol
                }
        
        if len(prices) < 2:
            return
        
        # Check all pairs for best opportunity
        best_opp = None
        exchanges = list(prices.keys())
        
        for i, buy_ex in enumerate(exchanges):
            for sell_ex in exchanges:
                if buy_ex == sell_ex:
                    continue
                
                buy_price, buy_qty = prices[buy_ex]['ask']
                sell_price, sell_qty = prices[sell_ex]['bid']
                
                if buy_price <= 0:
                    continue
                
                spread = (sell_price - buy_price) / buy_price * 100
                
                exec_qty = min(buy_qty, sell_qty)
                exec_value = exec_qty * buy_price
                gross = exec_qty * (sell_price - buy_price)
                buy_cost = exec_value * prices[buy_ex]['fee']
                sell_cost = (exec_qty * sell_price) * prices[sell_ex]['fee']
                net = gross - buy_cost - sell_cost
                
                if best_opp is None or spread > best_opp.spread:
                    best_opp = Opportunity(
                        spread=spread,
                        exec_qty=exec_qty,
                        exec_value=exec_value,
                        net_profit=net,
                        buy_exchange=buy_ex,
                        sell_exchange=sell_ex,
                        buy_price=buy_price,
                        sell_price=sell_price,
                        buy_symbol=prices[buy_ex]['symbol'],
                        sell_symbol=prices[sell_ex]['symbol'],
                    )
        
        pair.best_opp = best_opp

    async def stop(self) -> None:
        """Stop the bot."""
        self._running = False
        console.print()
        console.rule("[bold]SESSION SUMMARY[/]")
        console.print(f"  Total P&L: ${self.engine.position_manager.get_total_pnl():.2f}")
        console.rule()


async def main():
    bot = TriExchangeBot(paper_trading=True)
    try:
        await bot.start()
    except KeyboardInterrupt:
        pass
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
