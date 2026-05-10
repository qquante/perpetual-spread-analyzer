"""
Risk Manager - Controls position sizing, exposure limits, and PnL tracking.
"""

import logging
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from collections import deque

from src.config import config

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Represents an open position."""
    exchange: str
    symbol: str
    side: str  # "LONG" or "SHORT"
    quantity: Decimal
    entry_price: Decimal
    current_price: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    timestamp: datetime = field(default_factory=datetime.now)
    
    def update_pnl(self, current_price: Decimal) -> None:
        """Update unrealized PnL based on current price."""
        self.current_price = current_price
        if self.side == "LONG":
            self.unrealized_pnl = (current_price - self.entry_price) * self.quantity
        else:
            self.unrealized_pnl = (self.entry_price - current_price) * self.quantity


@dataclass 
class TradeRecord:
    """Record of a completed trade."""
    timestamp: datetime
    direction: str
    quantity: Decimal
    buy_price: Decimal
    sell_price: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal
    fees: Decimal


class RiskManager:
    """
    Manages risk exposure and capital allocation.
    
    Responsibilities:
    - Position sizing based on capital and risk
    - Max position limits
    - Drawdown monitoring
    - PnL tracking (realized + unrealized)
    - Trade cooldown enforcement
    """

    def __init__(self):
        # Configuration
        self.capital_usd = Decimal(str(config.trading.capital_usd))
        self.max_leverage = Decimal(str(config.trading.max_leverage))
        self.max_position_size = Decimal(str(config.trading.max_position_size))
        self.profit_threshold = Decimal(str(config.trading.profit_threshold_percent))
        self.trade_cooldown = config.trading.trade_cooldown_seconds
        
        # State
        self.positions: Dict[str, Position] = {}  # key: "exchange:symbol"
        self.trade_history: deque = deque(maxlen=1000)
        self.last_trade_time: Optional[datetime] = None
        
        # PnL tracking
        self.realized_pnl = Decimal("0")
        self.starting_capital = self.capital_usd
        self.peak_capital = self.capital_usd
        self.max_drawdown = Decimal("0")
        
        # Risk limits
        self.max_daily_loss = self.capital_usd * Decimal("0.05")  # 5% max daily loss
        self.daily_pnl = Decimal("0")
        self.daily_reset_time: Optional[datetime] = None
        
        # Trade count limits
        self.max_trades_per_hour = 60
        self.hourly_trades: deque = deque(maxlen=60)

    def can_trade(self) -> Tuple[bool, Optional[str]]:
        """
        Check if we're allowed to trade.
        
        Returns:
            (allowed: bool, reason: Optional[str])
        """
        # Check cooldown
        if self.last_trade_time:
            elapsed = (datetime.now() - self.last_trade_time).total_seconds()
            if elapsed < self.trade_cooldown:
                remaining = self.trade_cooldown - elapsed
                return False, f"Cooldown: {remaining:.1f}s remaining"
        
        # Check daily loss limit
        self._check_daily_reset()
        if self.daily_pnl < -self.max_daily_loss:
            return False, f"Daily loss limit reached: ${self.daily_pnl:.2f}"
        
        # Check hourly trade limit
        self._cleanup_hourly_trades()
        if len(self.hourly_trades) >= self.max_trades_per_hour:
            return False, f"Hourly trade limit reached: {self.max_trades_per_hour}"
        
        # Check max drawdown (10% circuit breaker)
        current_capital = self.starting_capital + self.realized_pnl
        if current_capital < self.starting_capital * Decimal("0.9"):
            return False, "Circuit breaker: 10% drawdown limit reached"
        
        return True, None

    def calculate_position_size(
        self,
        price: Decimal,
        available_liquidity: Decimal,
        spread_percent: Decimal
    ) -> Decimal:
        """
        Calculate optimal position size based on:
        - Available capital
        - Risk limits
        - Available liquidity
        - Expected profit
        
        Uses fractional Kelly criterion for position sizing.
        """
        # Available capital with leverage
        available_capital = self.capital_usd * self.max_leverage
        
        # Risk per trade: 1% of capital
        risk_per_trade = self.capital_usd * Decimal("0.01")
        
        # Max quantity by capital
        max_qty_capital = risk_per_trade / price
        
        # Max quantity by position limit
        max_qty_limit = self.max_position_size
        
        # Max quantity by liquidity (take max 50% of available)
        max_qty_liquidity = available_liquidity * Decimal("0.5")
        
        # Kelly criterion sizing (simplified)
        # f = (p * b - q) / b
        # where p = win probability, b = win/loss ratio, q = 1-p
        win_prob = Decimal("0.6")  # Assume 60% win rate
        win_loss_ratio = spread_percent / Decimal("0.05")  # Expected profit vs typical loss
        kelly_fraction = (win_prob * win_loss_ratio - (1 - win_prob)) / win_loss_ratio
        kelly_fraction = max(Decimal("0"), min(kelly_fraction, Decimal("0.25")))  # Cap at 25%
        
        # Apply Kelly to capital-based sizing
        kelly_qty = max_qty_capital * kelly_fraction * 4  # Scale up since we're fractional
        
        # Take minimum of all constraints
        quantity = min(max_qty_capital, max_qty_limit, max_qty_liquidity, kelly_qty)
        
        # Ensure non-negative
        quantity = max(Decimal("0"), quantity)
        
        # Round to 4 decimal places
        return round(quantity, 4)

    def record_trade(
        self,
        direction: str,
        quantity: Decimal,
        buy_price: Decimal,
        sell_price: Decimal,
        fees: Decimal
    ) -> None:
        """Record a completed trade."""
        gross_pnl = (sell_price - buy_price) * quantity
        net_pnl = gross_pnl - fees
        
        record = TradeRecord(
            timestamp=datetime.now(),
            direction=direction,
            quantity=quantity,
            buy_price=buy_price,
            sell_price=sell_price,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            fees=fees
        )
        
        self.trade_history.append(record)
        self.realized_pnl += net_pnl
        self.daily_pnl += net_pnl
        self.last_trade_time = datetime.now()
        self.hourly_trades.append(datetime.now())
        
        # Update peak and drawdown
        current_capital = self.starting_capital + self.realized_pnl
        if current_capital > self.peak_capital:
            self.peak_capital = current_capital
        
        drawdown = (self.peak_capital - current_capital) / self.peak_capital
        if drawdown > self.max_drawdown:
            self.max_drawdown = drawdown
        
        logger.info(
            f"Trade recorded: {direction} | Qty: {quantity} | "
            f"Net PnL: ${net_pnl:.4f} | Total: ${self.realized_pnl:.4f}"
        )

    def update_position(
        self,
        exchange: str,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal
    ) -> None:
        """Update or create a position."""
        key = f"{exchange}:{symbol}"
        
        if key in self.positions:
            pos = self.positions[key]
            if pos.side == side:
                # Add to position
                total_value = pos.entry_price * pos.quantity + price * quantity
                pos.quantity += quantity
                pos.entry_price = total_value / pos.quantity
            else:
                # Reduce position
                if quantity >= pos.quantity:
                    # Close position
                    del self.positions[key]
                else:
                    pos.quantity -= quantity
        else:
            # New position
            self.positions[key] = Position(
                exchange=exchange,
                symbol=symbol,
                side=side,
                quantity=quantity,
                entry_price=price
            )

    def get_net_exposure(self) -> Dict[str, Decimal]:
        """Get net exposure by symbol across all exchanges."""
        exposure: Dict[str, Decimal] = {}
        
        for key, pos in self.positions.items():
            symbol = pos.symbol
            qty = pos.quantity if pos.side == "LONG" else -pos.quantity
            
            if symbol in exposure:
                exposure[symbol] += qty
            else:
                exposure[symbol] = qty
        
        return exposure

    def _check_daily_reset(self) -> None:
        """Reset daily PnL at midnight UTC."""
        now = datetime.now()
        if self.daily_reset_time is None or now.date() > self.daily_reset_time.date():
            self.daily_pnl = Decimal("0")
            self.daily_reset_time = now

    def _cleanup_hourly_trades(self) -> None:
        """Remove trades older than 1 hour from the counter."""
        cutoff = datetime.now() - timedelta(hours=1)
        while self.hourly_trades and self.hourly_trades[0] < cutoff:
            self.hourly_trades.popleft()

    def get_stats(self) -> Dict:
        """Get current risk management statistics."""
        current_capital = self.starting_capital + self.realized_pnl
        roi = ((current_capital - self.starting_capital) / self.starting_capital) * 100
        
        return {
            "starting_capital": str(self.starting_capital),
            "current_capital": str(current_capital),
            "realized_pnl": str(self.realized_pnl),
            "daily_pnl": str(self.daily_pnl),
            "roi_percent": f"{roi:.2f}%",
            "max_drawdown": f"{self.max_drawdown * 100:.2f}%",
            "total_trades": len(self.trade_history),
            "open_positions": len(self.positions),
            "net_exposure": self.get_net_exposure(),
            "last_trade": self.last_trade_time.isoformat() if self.last_trade_time else None
        }


# Global risk manager instance
risk_manager = RiskManager()
