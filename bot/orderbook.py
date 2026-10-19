"""
FastOrderbook - O(1) best bid/ask using heaps
"""

import time
import heapq
from typing import Dict, List, Tuple


class FastOrderbook:
    """Orderbook with O(1) best bid/ask using heaps."""
    
    __slots__ = ('bids', 'asks', 'bid_heap', 'ask_heap', 'best_bid', 'best_bid_qty', 
                 'best_ask', 'best_ask_qty', 'ts')
    
    def __init__(self):
        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}
        self.bid_heap: list = []  # Max heap (negated)
        self.ask_heap: list = []  # Min heap
        self.best_bid: float = 0.0
        self.best_bid_qty: float = 0.0
        self.best_ask: float = 0.0
        self.best_ask_qty: float = 0.0
        self.ts: float = 0
    
    def update(self, bids: List[Tuple[float, float]], asks: List[Tuple[float, float]]):
        """Update orderbook with O(log n) operations."""
        for price, qty in bids:
            if qty > 0:
                self.bids[price] = qty
                heapq.heappush(self.bid_heap, -price)  # Negated for max heap
            elif price in self.bids:
                del self.bids[price]
        
        for price, qty in asks:
            if qty > 0:
                self.asks[price] = qty
                heapq.heappush(self.ask_heap, price)
            elif price in self.asks:
                del self.asks[price]
        
        self._refresh_best()
        self.ts = time.time()
    
    def set_full(self, bids: List[Tuple[float, float]], asks: List[Tuple[float, float]]):
        """Set full snapshot."""
        self.bids = {p: q for p, q in bids if q > 0}
        self.asks = {p: q for p, q in asks if q > 0}
        self.bid_heap = [-p for p in self.bids]
        self.ask_heap = list(self.asks.keys())
        heapq.heapify(self.bid_heap)
        heapq.heapify(self.ask_heap)
        self._refresh_best()
        self.ts = time.time()
    
    def _refresh_best(self):
        """Refresh best bid/ask by popping stale entries from heaps."""
        # Best bid (max)
        while self.bid_heap:
            price = -self.bid_heap[0]
            if price in self.bids:
                self.best_bid = price
                self.best_bid_qty = self.bids[price]
                break
            heapq.heappop(self.bid_heap)
        else:
            self.best_bid = 0.0
            self.best_bid_qty = 0.0
        
        # Best ask (min)
        while self.ask_heap:
            price = self.ask_heap[0]
            if price in self.asks:
                self.best_ask = price
                self.best_ask_qty = self.asks[price]
                break
            heapq.heappop(self.ask_heap)
        else:
            self.best_ask = 0.0
            self.best_ask_qty = 0.0
    
    def age_ms(self) -> float:
        """Get age of orderbook in milliseconds."""
        return (time.time() - self.ts) * 1000 if self.ts > 0 else float('inf')
