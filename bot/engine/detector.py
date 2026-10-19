"""
Opportunity Detector - Checks for arbitrage opportunities
"""

import time
import asyncio
from typing import Dict, Optional, Set, TYPE_CHECKING

from bot.config import (
    MIN_SPREAD, MIN_PROFIT_USD, MAX_ORDER_USD,
    BP_FEE, PX_FEE, MAX_CONCURRENT
)
from bot.models import Opportunity

if TYPE_CHECKING:
    from bot.main import ArbitrageBot


class OpportunityDetector:
    """Detects arbitrage opportunities between exchanges."""
    
    def __init__(self, bot: "ArbitrageBot"):
        self.bot = bot
    
    def check_and_execute(self, pair: str):
        """Check for arbitrage opportunity and execute if found."""
        now = time.time()
        
        # ATOMIC CHECK 1: Already executing?
        if pair in self.bot.executing:
            return
        
        # ATOMIC CHECK 2: In cooldown?
        if pair in self.bot.cooldown and self.bot.cooldown[pair] > now:
            return
        
        # ATOMIC CHECK 3: Too many concurrent executions?
        if len(self.bot.executing) >= MAX_CONCURRENT:
            return
        
        bp = self.bot.bp.get(pair)
        px = self.bot.px.get(pair)
        
        if not bp or not px:
            return
        
        bp_bid, bp_bid_qty = bp.best_bid, bp.best_bid_qty
        bp_ask, bp_ask_qty = bp.best_ask, bp.best_ask_qty
        px_bid, px_bid_qty = px.best_bid, px.best_bid_qty
        px_ask, px_ask_qty = px.best_ask, px.best_ask_qty
        
        if bp_bid <= 0 or bp_ask <= 0 or px_bid <= 0 or px_ask <= 0:
            return
        
        self.bot.checks += 1
        best: Optional[Opportunity] = None
        
        # Direction 1: Buy BP, Sell PX
        if px_bid > bp_ask:
            spread = (px_bid - bp_ask) / bp_ask * 100
            if spread >= MIN_SPREAD:
                max_qty_usd = MAX_ORDER_USD / bp_ask
                qty = min(bp_ask_qty, px_bid_qty, max_qty_usd)
                if qty > 0:
                    gross = qty * (px_bid - bp_ask)
                    fees = (qty * bp_ask * BP_FEE) + (qty * px_bid * PX_FEE)
                    net = gross - fees
                    if net >= MIN_PROFIT_USD:
                        best = Opportunity(
                            pair=pair, spread=spread, qty=qty, profit=net,
                            buy_exchange="BP", sell_exchange="PX",
                            buy_price=bp_ask, sell_price=px_bid, ts=time.time()
                        )
        
        # Direction 2: Buy PX, Sell BP
        if bp_bid > px_ask:
            spread = (bp_bid - px_ask) / px_ask * 100
            if spread >= MIN_SPREAD:
                max_qty_usd = MAX_ORDER_USD / px_ask
                qty = min(px_ask_qty, bp_bid_qty, max_qty_usd)
                if qty > 0:
                    gross = qty * (bp_bid - px_ask)
                    fees = (qty * px_ask * PX_FEE) + (qty * bp_bid * BP_FEE)
                    net = gross - fees
                    if net >= MIN_PROFIT_USD:
                        if best is None or net > best.profit:
                            best = Opportunity(
                                pair=pair, spread=spread, qty=qty, profit=net,
                                buy_exchange="PX", sell_exchange="BP",
                                buy_price=px_ask, sell_price=bp_bid, ts=time.time()
                            )
        
        if best:
            self.bot.opportunities[pair] = best
            
            should_execute = True
            if pair in self.bot.open_positions:
                entry_spread = self.bot.open_positions[pair].get("entry_spread", 0)
                if best.spread <= entry_spread * 1.5:
                    should_execute = False
            
            if should_execute:
                # ATOMIC LOCK: Re-check and add immediately
                if pair in self.bot.executing:
                    return
                self.bot.executing.add(pair)
                asyncio.create_task(self.bot.executor.execute(best))
