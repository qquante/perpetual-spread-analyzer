"""
Arbitrage Engine - Detects spread opportunities between exchanges.
"""

import asyncio
import logging
from typing import Optional, Callable, Dict, Tuple
from decimal import Decimal
from dataclasses import dataclass
from datetime import datetime

from src.config import config
from src.core.orderbook import Orderbook
from src.core.redis_client import redis_client

logger = logging.getLogger(__name__)


@dataclass
class ArbitrageOpportunity:
    """Represents a detected arbitrage opportunity."""
    direction: str  # "bp_to_paradex" or "paradex_to_bp"
    spread_percent: Decimal
    buy_exchange: str
    sell_exchange: str
    buy_price: Decimal
    sell_price: Decimal
    available_quantity: Decimal
    timestamp: datetime
    
    @property
    def profit_per_unit(self) -> Decimal:
        """Gross profit per unit before fees."""
        return self.sell_price - self.buy_price
    
    def is_profitable(self, threshold_percent: Decimal) -> bool:
        """Check if opportunity exceeds profit threshold."""
        return self.spread_percent >= threshold_percent


class ArbitrageEngine:
    """
    Detects arbitrage opportunities between Backpack and Paradex.
    
    Strategy: Aggressive Orderbook Taker
    - Buy on exchange with lower ask (agredir o ask)
    - Sell on exchange with higher bid (agredir o bid)
    - Both orders are IOC (Immediate or Cancel)
    """

    def __init__(self):
        self.bp_orderbook: Optional[Orderbook] = None
        self.px_orderbook: Optional[Orderbook] = None
        
        self.profit_threshold = Decimal(str(config.trading.profit_threshold_percent))
        self.trade_cooldown = config.trading.trade_cooldown_seconds
        
        self._running = False
        self._last_trade_time: Optional[datetime] = None
        self._on_opportunity: Optional[Callable] = None
        self._check_task: Optional[asyncio.Task] = None
        
        # Fee configuration
        self.fees = {
            "backpack": {
                "maker": Decimal("0.0002"),  # 0.02%
                "taker": Decimal("0.0005"),  # 0.05%
            },
            "paradex": {
                "maker": Decimal("0"),  # 0% for retail!
                "taker": Decimal("0"),  # 0% for retail!
            }
        }

    def set_orderbooks(self, bp_book: Orderbook, px_book: Orderbook) -> None:
        """Set orderbook references."""
        self.bp_orderbook = bp_book
        self.px_orderbook = px_book

    def calculate_spreads(self) -> Dict:
        """
        Calculate cross-exchange spreads.
        
        Returns:
            {
                "bp_to_paradex": spread% for buy BP → sell Paradex,
                "paradex_to_bp": spread% for buy Paradex → sell BP,
                "best_direction": which direction is profitable,
                "best_spread": highest spread percentage
            }
        """
        if not self.bp_orderbook or not self.px_orderbook:
            return {"error": "Orderbooks not initialized"}
        
        bp_best_bid = self.bp_orderbook.get_best_bid()
        bp_best_ask = self.bp_orderbook.get_best_ask()
        px_best_bid = self.px_orderbook.get_best_bid()
        px_best_ask = self.px_orderbook.get_best_ask()
        
        if not all([bp_best_bid, bp_best_ask, px_best_bid, px_best_ask]):
            return {"error": "Incomplete orderbook data"}
        
        # Direction 1: Buy on Backpack (taker ask), Sell on Paradex (taker bid)
        # Spread = (paradex_bid - backpack_ask) / backpack_ask
        bp_ask_price = bp_best_ask[0]
        px_bid_price = px_best_bid[0]
        bp_to_px_spread = ((px_bid_price - bp_ask_price) / bp_ask_price) * 100
        
        # Direction 2: Buy on Paradex (taker ask), Sell on Backpack (taker bid)
        px_ask_price = px_best_ask[0]
        bp_bid_price = bp_best_bid[0]
        px_to_bp_spread = ((bp_bid_price - px_ask_price) / px_ask_price) * 100
        
        # Determine best direction
        if bp_to_px_spread > px_to_bp_spread:
            best_direction = "bp_to_paradex"
            best_spread = bp_to_px_spread
        else:
            best_direction = "paradex_to_bp"
            best_spread = px_to_bp_spread
        
        return {
            "bp_to_paradex": round(bp_to_px_spread, 4),
            "paradex_to_bp": round(px_to_bp_spread, 4),
            "best_direction": best_direction,
            "best_spread": round(best_spread, 4),
            "threshold": float(self.profit_threshold),
            "bp_ask": str(bp_ask_price),
            "bp_bid": str(bp_bid_price),
            "px_ask": str(px_ask_price),
            "px_bid": str(px_bid_price),
        }

    def calculate_net_spread(self, direction: str, spread_percent: Decimal) -> Decimal:
        """
        Calculate net spread after fees.
        
        For aggressive execution (taker on both sides):
        - Backpack taker fee: 0.05%
        - Paradex taker fee: 0% (retail)
        
        Net spread = Gross spread - Total fees
        """
        if direction == "bp_to_paradex":
            # Buy BP (taker) → Sell PX (taker)
            total_fees = self.fees["backpack"]["taker"] + self.fees["paradex"]["taker"]
        else:
            # Buy PX (taker) → Sell BP (taker)
            total_fees = self.fees["paradex"]["taker"] + self.fees["backpack"]["taker"]
        
        net_spread = spread_percent - (total_fees * 100)
        return net_spread

    def detect_opportunity(self) -> Optional[ArbitrageOpportunity]:
        """
        Check for profitable arbitrage opportunity.
        
        Returns:
            ArbitrageOpportunity if found, None otherwise
        """
        spreads = self.calculate_spreads()
        
        if "error" in spreads:
            return None
        
        best_direction = spreads["best_direction"]
        gross_spread = Decimal(str(spreads["best_spread"]))
        
        # Calculate net spread after fees
        net_spread = self.calculate_net_spread(best_direction, gross_spread)
        
        # Check if above threshold
        if net_spread < self.profit_threshold:
            return None
        
        # Get price and quantity info
        if best_direction == "bp_to_paradex":
            buy_price = Decimal(spreads["bp_ask"])
            sell_price = Decimal(spreads["px_bid"])
            buy_exchange = "backpack"
            sell_exchange = "paradex"
            # Get available quantity (minimum of both sides)
            bp_qty = self.bp_orderbook.get_best_ask()[1]
            px_qty = self.px_orderbook.get_best_bid()[1]
        else:
            buy_price = Decimal(spreads["px_ask"])
            sell_price = Decimal(spreads["bp_bid"])
            buy_exchange = "paradex"
            sell_exchange = "backpack"
            px_qty = self.px_orderbook.get_best_ask()[1]
            bp_qty = self.bp_orderbook.get_best_bid()[1]
        
        available_qty = min(bp_qty, px_qty)
        
        return ArbitrageOpportunity(
            direction=best_direction,
            spread_percent=net_spread,
            buy_exchange=buy_exchange,
            sell_exchange=sell_exchange,
            buy_price=buy_price,
            sell_price=sell_price,
            available_quantity=available_qty,
            timestamp=datetime.now()
        )

    async def start_monitoring(self, on_opportunity: Callable) -> None:
        """
        Start monitoring for arbitrage opportunities.
        
        Runs check every `trade_cooldown` seconds.
        """
        self._on_opportunity = on_opportunity
        self._running = True
        
        self._check_task = asyncio.create_task(self._monitor_loop())
        logger.info(f"Arbitrage engine started (threshold: {self.profit_threshold}%, cooldown: {self.trade_cooldown}s)")

    async def stop_monitoring(self) -> None:
        """Stop monitoring loop."""
        self._running = False
        
        if self._check_task:
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass
        
        logger.info("Arbitrage engine stopped")

    async def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        while self._running:
            try:
                # Check cooldown
                if self._last_trade_time:
                    elapsed = (datetime.now() - self._last_trade_time).total_seconds()
                    if elapsed < self.trade_cooldown:
                        await asyncio.sleep(self.trade_cooldown - elapsed)
                        continue
                
                # Detect opportunity
                opportunity = self.detect_opportunity()
                
                if opportunity:
                    logger.info(
                        f"🎯 OPPORTUNITY: {opportunity.direction} "
                        f"spread={opportunity.spread_percent:.4f}% "
                        f"qty={opportunity.available_quantity}"
                    )
                    
                    if self._on_opportunity:
                        await self._on_opportunity(opportunity)
                    
                    self._last_trade_time = datetime.now()
                
                await asyncio.sleep(0.1)  # Check frequently
                
            except Exception as e:
                logger.error(f"Arbitrage engine error: {e}")
                await asyncio.sleep(1)

    def get_status(self) -> Dict:
        """Get current engine status."""
        spreads = self.calculate_spreads()
        
        return {
            "running": self._running,
            "threshold": float(self.profit_threshold),
            "cooldown": self.trade_cooldown,
            "last_trade": self._last_trade_time.isoformat() if self._last_trade_time else None,
            "current_spreads": spreads,
            "bp_book_ready": self.bp_orderbook.is_initialized if self.bp_orderbook else False,
            "px_book_ready": self.px_orderbook.is_initialized if self.px_orderbook else False,
        }


# Global engine instance
arbitrage_engine = ArbitrageEngine()
