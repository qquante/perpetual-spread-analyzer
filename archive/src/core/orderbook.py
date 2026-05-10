"""
Orderbook management with efficient sorted data structures.
Handles snapshot initialization and incremental delta updates.
"""

import logging
from typing import Optional, Tuple, List, Dict
from dataclasses import dataclass, field
from sortedcontainers import SortedDict
from decimal import Decimal

logger = logging.getLogger(__name__)


@dataclass
class OrderbookLevel:
    """Single price level in the orderbook."""
    price: Decimal
    quantity: Decimal


@dataclass
class Orderbook:
    """
    Local orderbook representation with sorted bids/asks.
    
    Bids: sorted descending (highest first)
    Asks: sorted ascending (lowest first)
    """
    exchange: str
    symbol: str
    
    # SortedDict for O(log n) insertions and O(1) best price access
    # Bids: negative keys for descending order
    bids: SortedDict = field(default_factory=SortedDict)
    asks: SortedDict = field(default_factory=SortedDict)
    
    # Sequence tracking for delta validation
    last_update_id: int = 0
    is_initialized: bool = False

    def apply_snapshot(self, bids: List[List[str]], asks: List[List[str]], update_id: int = 0) -> None:
        """
        Initialize orderbook from REST API snapshot.
        
        Args:
            bids: [[price, quantity], ...] sorted descending
            asks: [[price, quantity], ...] sorted ascending
            update_id: Snapshot sequence number
        """
        self.bids.clear()
        self.asks.clear()
        
        for price, qty in bids:
            p = Decimal(price)
            q = Decimal(qty)
            if q > 0:
                # Negative key for descending sort
                self.bids[-p] = q
        
        for price, qty in asks:
            p = Decimal(price)
            q = Decimal(qty)
            if q > 0:
                self.asks[p] = q
        
        self.last_update_id = int(update_id) if update_id else 0
        self.is_initialized = True
        
        # Only log on first init, use DEBUG for subsequent updates
        if len(self.bids) > 0:
            logger.debug(
                f"[{self.exchange}] {self.symbol} snapshot: "
                f"{len(self.bids)} bids, {len(self.asks)} asks"
            )

    def apply_delta(
        self,
        bids: List[List[str]],
        asks: List[List[str]],
        first_update_id: int,
        last_update_id: int
    ) -> bool:
        """
        Apply incremental delta update from WebSocket.
        
        Args:
            bids: [[price, quantity], ...] changed bid levels
            asks: [[price, quantity], ...] changed ask levels
            first_update_id: First sequence in this delta (U)
            last_update_id: Last sequence in this delta (u)
            
        Returns:
            True if delta was applied, False if sequence gap detected
        """
        # Convert to int if needed (WebSocket may send as string)
        first_update_id = int(first_update_id) if first_update_id else 0
        last_update_id = int(last_update_id) if last_update_id else 0
        
        # Validate sequence continuity
        if self.is_initialized and first_update_id > self.last_update_id + 1:
            logger.warning(
                f"[{self.exchange}] {self.symbol} sequence gap: "
                f"expected <= {self.last_update_id + 1}, got {first_update_id}"
            )
            return False
        
        # Apply bid updates
        for price, qty in bids:
            p = Decimal(price)
            q = Decimal(qty)
            
            if q == 0:
                # Remove price level
                self.bids.pop(-p, None)
            else:
                # Update/insert price level
                self.bids[-p] = q
        
        # Apply ask updates
        for price, qty in asks:
            p = Decimal(price)
            q = Decimal(qty)
            
            if q == 0:
                # Remove price level
                self.asks.pop(p, None)
            else:
                # Update/insert price level
                self.asks[p] = q
        
        self.last_update_id = last_update_id
        return True

    def get_best_bid(self) -> Optional[Tuple[Decimal, Decimal]]:
        """Get highest bid price and quantity."""
        if not self.bids:
            return None
        neg_price = self.bids.peekitem(0)[0]
        return (-neg_price, self.bids[neg_price])

    def get_best_ask(self) -> Optional[Tuple[Decimal, Decimal]]:
        """Get lowest ask price and quantity."""
        if not self.asks:
            return None
        price = self.asks.peekitem(0)[0]
        return (price, self.asks[price])

    def get_spread(self) -> Optional[Tuple[Decimal, Decimal]]:
        """
        Calculate bid-ask spread.
        
        Returns:
            (absolute_spread, percent_spread) or None if book is empty
        """
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        
        if not best_bid or not best_ask:
            return None
        
        bid_price = best_bid[0]
        ask_price = best_ask[0]
        
        absolute = ask_price - bid_price
        percent = (absolute / ask_price) * 100
        
        return (absolute, percent)

    def get_mid_price(self) -> Optional[Decimal]:
        """Get mid-market price."""
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        
        if not best_bid or not best_ask:
            return None
        
        return (best_bid[0] + best_ask[0]) / 2

    def get_depth(self, levels: int = 5) -> Dict:
        """
        Get top N levels of orderbook.
        
        Returns:
            {
                "bids": [[price, qty], ...],
                "asks": [[price, qty], ...],
                "best_bid": price,
                "best_ask": price,
                "spread_pct": percent
            }
        """
        bid_levels = []
        for i, (neg_price, qty) in enumerate(self.bids.items()):
            if i >= levels:
                break
            bid_levels.append([str(-neg_price), str(qty)])
        
        ask_levels = []
        for i, (price, qty) in enumerate(self.asks.items()):
            if i >= levels:
                break
            ask_levels.append([str(price), str(qty)])
        
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        spread = self.get_spread()
        
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "bids": bid_levels,
            "asks": ask_levels,
            "best_bid": str(best_bid[0]) if best_bid else None,
            "best_ask": str(best_ask[0]) if best_ask else None,
            "spread_pct": str(round(spread[1], 4)) if spread else None,
            "update_id": self.last_update_id
        }

    def get_executable_quantity(self, side: str, limit_price: Decimal) -> Decimal:
        """
        Calculate total executable quantity at or better than limit price.
        Used for aggressive order sizing.
        
        Args:
            side: "buy" (look at asks) or "sell" (look at bids)
            limit_price: Maximum price willing to pay (buy) or minimum to receive (sell)
            
        Returns:
            Total quantity available at price levels within limit
        """
        total_qty = Decimal(0)
        
        if side == "buy":
            # Sweep asks up to limit_price
            for price, qty in self.asks.items():
                if price <= limit_price:
                    total_qty += qty
                else:
                    break
        else:
            # Sweep bids down to limit_price
            for neg_price, qty in self.bids.items():
                price = -neg_price
                if price >= limit_price:
                    total_qty += qty
                else:
                    break
        
        return total_qty

    def __repr__(self) -> str:
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        return (
            f"Orderbook({self.exchange}:{self.symbol} "
            f"bid={best_bid[0] if best_bid else 'N/A'} "
            f"ask={best_ask[0] if best_ask else 'N/A'} "
            f"levels={len(self.bids)}x{len(self.asks)})"
        )
