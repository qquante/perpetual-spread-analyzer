"""
Delta-Neutral Arbitrage Executor
Opens and closes hedged positions across exchanges.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from enum import Enum

from rich.console import Console
from rich.table import Table

console = Console()


class PositionStatus(Enum):
    PENDING_OPEN = "pending_open"
    OPEN = "open"
    PENDING_CLOSE = "pending_close"
    CLOSED = "closed"
    FAILED = "failed"


@dataclass
class Leg:
    """One leg of a delta-neutral position."""
    exchange: str  # BP, PX, EX
    symbol: str
    side: str  # BUY or SELL
    qty: float
    price: float
    fill_price: Optional[float] = None
    order_id: Optional[str] = None
    filled: bool = False


@dataclass
class Position:
    """A complete delta-neutral position (2 legs)."""
    id: str
    base: str  # e.g., BTC
    status: PositionStatus = PositionStatus.PENDING_OPEN
    # Opening legs
    open_buy_leg: Optional[Leg] = None
    open_sell_leg: Optional[Leg] = None
    open_spread: float = 0.0
    open_time: Optional[datetime] = None
    # Closing legs
    close_buy_leg: Optional[Leg] = None
    close_sell_leg: Optional[Leg] = None
    close_spread: float = 0.0
    close_time: Optional[datetime] = None
    # P&L
    realized_pnl: float = 0.0


class PositionManager:
    """Manages all open delta-neutral positions."""
    
    def __init__(self):
        self.positions: Dict[str, Position] = {}
        self._position_counter = 0
        # Config
        self.open_threshold = 0.15  # Open when spread >= 0.15%
        self.close_threshold = 0.02  # Close when spread <= 0.02%
        self.max_positions_per_pair = 1
        self.max_total_positions = 5
        
    def can_open_position(self, base: str) -> bool:
        """Check if we can open a new position for this pair."""
        # Check max total
        open_positions = [p for p in self.positions.values() 
                        if p.status in [PositionStatus.OPEN, PositionStatus.PENDING_OPEN]]
        if len(open_positions) >= self.max_total_positions:
            return False
        
        # Check max per pair
        pair_positions = [p for p in open_positions if p.base == base]
        if len(pair_positions) >= self.max_positions_per_pair:
            return False
        
        return True
    
    def create_position(self, base: str, 
                       buy_exchange: str, buy_symbol: str, buy_price: float,
                       sell_exchange: str, sell_symbol: str, sell_price: float,
                       qty: float, spread: float) -> Position:
        """Create a new position ready to open."""
        self._position_counter += 1
        pos_id = f"{base}-{self._position_counter}"
        
        position = Position(
            id=pos_id,
            base=base,
            open_buy_leg=Leg(
                exchange=buy_exchange,
                symbol=buy_symbol,
                side="BUY",
                qty=qty,
                price=buy_price,
            ),
            open_sell_leg=Leg(
                exchange=sell_exchange,
                symbol=sell_symbol,
                side="SELL",
                qty=qty,
                price=sell_price,
            ),
            open_spread=spread,
        )
        
        self.positions[pos_id] = position
        return position
    
    def mark_opened(self, pos_id: str, buy_fill: float, sell_fill: float) -> None:
        """Mark position as successfully opened."""
        pos = self.positions.get(pos_id)
        if pos:
            pos.status = PositionStatus.OPEN
            pos.open_time = datetime.now()
            pos.open_buy_leg.fill_price = buy_fill
            pos.open_buy_leg.filled = True
            pos.open_sell_leg.fill_price = sell_fill
            pos.open_sell_leg.filled = True
            console.print(f"[green]✓ Position {pos_id} OPENED @ {pos.open_spread:.3f}%[/]")
    
    def should_close(self, pos_id: str, current_spread: float) -> bool:
        """Check if position should be closed based on spread."""
        pos = self.positions.get(pos_id)
        if not pos or pos.status != PositionStatus.OPEN:
            return False
        
        # Close if spread converged or inverted
        if current_spread <= self.close_threshold:
            return True
        
        # Close if spread inverted (we can profit again!)
        if current_spread < 0 and abs(current_spread) >= self.open_threshold:
            return True
        
        return False
    
    def prepare_close(self, pos_id: str, current_spread: float,
                     buy_price: float, sell_price: float) -> Position:
        """Prepare position for closing."""
        pos = self.positions.get(pos_id)
        if not pos:
            return None
        
        # Close is opposite of open
        # If we opened: BUY on A, SELL on B
        # To close: SELL on A, BUY on B
        pos.close_sell_leg = Leg(
            exchange=pos.open_buy_leg.exchange,
            symbol=pos.open_buy_leg.symbol,
            side="SELL",
            qty=pos.open_buy_leg.qty,
            price=sell_price,
        )
        pos.close_buy_leg = Leg(
            exchange=pos.open_sell_leg.exchange,
            symbol=pos.open_sell_leg.symbol,
            side="BUY",
            qty=pos.open_sell_leg.qty,
            price=buy_price,
        )
        pos.close_spread = current_spread
        pos.status = PositionStatus.PENDING_CLOSE
        
        return pos
    
    def mark_closed(self, pos_id: str, buy_fill: float, sell_fill: float) -> None:
        """Mark position as successfully closed and calculate P&L."""
        pos = self.positions.get(pos_id)
        if not pos:
            return
        
        pos.status = PositionStatus.CLOSED
        pos.close_time = datetime.now()
        pos.close_buy_leg.fill_price = buy_fill
        pos.close_buy_leg.filled = True
        pos.close_sell_leg.fill_price = sell_fill
        pos.close_sell_leg.filled = True
        
        # Calculate P&L
        # Open: bought at X, sold at Y
        # Close: sold at A, bought at B
        qty = pos.open_buy_leg.qty
        
        open_buy = pos.open_buy_leg.fill_price
        open_sell = pos.open_sell_leg.fill_price
        close_sell = pos.close_sell_leg.fill_price
        close_buy = pos.close_buy_leg.fill_price
        
        # P&L from buy leg (bought, then sold)
        pnl_buy_leg = (close_sell - open_buy) * qty
        # P&L from sell leg (sold, then bought back)
        pnl_sell_leg = (open_sell - close_buy) * qty
        
        pos.realized_pnl = pnl_buy_leg + pnl_sell_leg
        
        console.print(f"[green]✓ Position {pos_id} CLOSED | P&L: ${pos.realized_pnl:.2f}[/]")
    
    def get_open_positions(self) -> List[Position]:
        """Get all open positions."""
        return [p for p in self.positions.values() if p.status == PositionStatus.OPEN]
    
    def get_total_pnl(self) -> float:
        """Get total realized P&L."""
        return sum(p.realized_pnl for p in self.positions.values() 
                  if p.status == PositionStatus.CLOSED)
    
    def summary(self) -> Table:
        """Build summary table."""
        table = Table(title="POSITIONS", show_header=True)
        table.add_column("ID", width=12)
        table.add_column("Status", width=10)
        table.add_column("Open Spread", justify="right", width=10)
        table.add_column("Close Spread", justify="right", width=10)
        table.add_column("P&L", justify="right", width=10)
        
        for pos in self.positions.values():
            status_color = {
                PositionStatus.OPEN: "green",
                PositionStatus.CLOSED: "dim",
                PositionStatus.PENDING_OPEN: "yellow",
                PositionStatus.PENDING_CLOSE: "yellow",
                PositionStatus.FAILED: "red",
            }.get(pos.status, "white")
            
            pnl_str = f"${pos.realized_pnl:.2f}" if pos.status == PositionStatus.CLOSED else "-"
            close_str = f"{pos.close_spread:.3f}%" if pos.close_spread else "-"
            
            table.add_row(
                pos.id,
                f"[{status_color}]{pos.status.value}[/]",
                f"{pos.open_spread:.3f}%",
                close_str,
                pnl_str,
            )
        
        return table


# Simple executor interface (to be connected to real executors)
class MockExecutor:
    """Mock executor for paper trading."""
    
    async def execute_pair(self, 
                          leg1: Leg, 
                          leg2: Leg) -> Tuple[bool, float, float]:
        """
        Execute both legs simultaneously.
        Returns: (success, leg1_fill_price, leg2_fill_price)
        """
        # In paper trading, assume fills at expected price
        await asyncio.sleep(0.1)  # Simulate latency
        
        console.print(f"  [cyan]→ {leg1.side} {leg1.qty:.4f} {leg1.exchange} @ ${leg1.price:,.2f}[/]")
        console.print(f"  [cyan]→ {leg2.side} {leg2.qty:.4f} {leg2.exchange} @ ${leg2.price:,.2f}[/]")
        
        return True, leg1.price, leg2.price


# Main arbitrage logic
class ArbitrageEngine:
    """Main engine that ties everything together."""
    
    def __init__(self, paper_trading: bool = True):
        self.position_manager = PositionManager()
        self.executor = MockExecutor() if paper_trading else None
        self._running = False
        
    async def check_open_opportunity(self, 
                                     base: str,
                                     buy_exchange: str, buy_symbol: str, buy_price: float, buy_qty: float,
                                     sell_exchange: str, sell_symbol: str, sell_price: float, sell_qty: float,
                                     spread: float) -> Optional[Position]:
        """Check and execute opening opportunity."""
        
        # Check if spread meets threshold
        if spread < self.position_manager.open_threshold:
            return None
        
        # Check if we can open
        if not self.position_manager.can_open_position(base):
            return None
        
        # Determine quantity
        exec_qty = min(buy_qty, sell_qty)
        
        # Create position
        pos = self.position_manager.create_position(
            base=base,
            buy_exchange=buy_exchange, buy_symbol=buy_symbol, buy_price=buy_price,
            sell_exchange=sell_exchange, sell_symbol=sell_symbol, sell_price=sell_price,
            qty=exec_qty,
            spread=spread,
        )
        
        console.print(f"\n[bold yellow]⚡ OPENING {pos.id} | Spread: {spread:.3f}%[/]")
        
        # Execute
        success, buy_fill, sell_fill = await self.executor.execute_pair(
            pos.open_buy_leg, pos.open_sell_leg
        )
        
        if success:
            self.position_manager.mark_opened(pos.id, buy_fill, sell_fill)
            return pos
        else:
            pos.status = PositionStatus.FAILED
            return None
    
    async def check_close_opportunities(self, spreads: Dict[str, float]) -> None:
        """Check all open positions for close conditions."""
        
        for pos in self.position_manager.get_open_positions():
            current_spread = spreads.get(pos.base, 0)
            
            if self.position_manager.should_close(pos.id, current_spread):
                # Get current prices (simplified - would need real prices)
                # For now, use the spread to estimate
                
                console.print(f"\n[bold yellow]⚡ CLOSING {pos.id} | Spread: {current_spread:.3f}%[/]")
                
                # Prepare close legs
                # In real implementation, would get actual prices from orderbook
                buy_price = pos.open_sell_leg.fill_price  # ~same as where we sold
                sell_price = pos.open_buy_leg.fill_price  # ~same as where we bought
                
                pos = self.position_manager.prepare_close(
                    pos.id, current_spread, buy_price, sell_price
                )
                
                # Execute close
                success, buy_fill, sell_fill = await self.executor.execute_pair(
                    pos.close_buy_leg, pos.close_sell_leg
                )
                
                if success:
                    self.position_manager.mark_closed(pos.id, buy_fill, sell_fill)


# Example usage
async def demo():
    """Demo the arbitrage engine."""
    console.print("[bold]Delta-Neutral Arbitrage Engine Demo[/]\n")
    
    engine = ArbitrageEngine(paper_trading=True)
    
    # Simulate finding an opportunity
    console.print("[dim]Simulating opportunity: BTC spread +0.18%[/]")
    
    await engine.check_open_opportunity(
        base="BTC",
        buy_exchange="BP", buy_symbol="BTC_USDC_PERP", buy_price=91000, buy_qty=0.1,
        sell_exchange="PX", sell_symbol="BTC-USD-PERP", sell_price=91163.80, sell_qty=0.15,
        spread=0.18,
    )
    
    # Simulate spread converging
    console.print("\n[dim]Simulating spread convergence: +0.01%[/]")
    
    await engine.check_close_opportunities({"BTC": 0.01})
    
    # Show summary
    console.print()
    console.print(engine.position_manager.summary())
    console.print(f"\n[bold]Total P&L: ${engine.position_manager.get_total_pnl():.2f}[/]")


if __name__ == "__main__":
    asyncio.run(demo())
