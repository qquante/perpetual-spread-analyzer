"""
ArbitrageBot - Main orchestrator class
"""

import asyncio
import time
import httpx
from datetime import datetime
from typing import Dict, Set

from bot.config import (
    DRY_RUN, DEBUG, 
    MIN_SPREAD, MIN_PROFIT_USD, MAX_ORDER_USD, SLIPPAGE_BUFFER,
    COOLDOWN_SUCCESS, COOLDOWN_FAILURE, COOLDOWN_PARTIAL, MAX_CONCURRENT,
    MAX_POSITION_AGE, BP_FEE, PX_FEE
)
from bot.models import Opportunity
from bot.orderbook import FastOrderbook
from bot.exchanges.backpack import BackpackClient
from bot.exchanges.paradex import ParadexClient
from bot.engine.detector import OpportunityDetector
from bot.engine.executor import OrderExecutor


class ArbitrageBot:
    """Main arbitrage bot orchestrator."""
    
    VERSION = "6.0"
    
    def __init__(self):
        self.dry_run = DRY_RUN
        
        # Orderbooks
        self.bp: Dict[str, FastOrderbook] = {}
        self.px: Dict[str, FastOrderbook] = {}
        
        # Pairs config: {base: {bp: symbol, px: symbol}}
        self.pairs: Dict[str, dict] = {}
        
        # Position tracking
        self.open_positions: Dict[str, dict] = {}
        self.executing: Set[str] = set()
        self.cooldown: Dict[str, float] = {}
        
        # Stats
        self.updates = 0
        self.checks = 0
        self.executions = 0
        self.profit = 0.0
        self.errors = 0
        
        # Opportunities
        self.opportunities: Dict[str, Opportunity] = {}
        
        # Control
        self._running = False
        self._start = 0
        
        # Components
        self.backpack = BackpackClient(self)
        self.paradex = ParadexClient(self)
        self.detector = OpportunityDetector(self)
        self.executor = OrderExecutor(self)
    
    # ========================================================================
    # LOGGING
    # ========================================================================
    
    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[{ts}] {msg}", flush=True)
    
    def _debug(self, msg: str):
        if DEBUG:
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"[{ts}] DBG: {msg}", flush=True)
    
    def _error(self, msg: str):
        self.errors += 1
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[{ts}] ERR: {msg}", flush=True)
    
    # ========================================================================
    # DELEGATE METHODS
    # ========================================================================
    
    def _check_and_execute(self, pair: str):
        """Delegate to detector."""
        self.detector.check_and_execute(pair)
    
    # ========================================================================
    # POSITION MANAGEMENT
    # ========================================================================
    
    async def _position_cleanup_loop(self):
        """Close stale positions."""
        while self._running:
            await asyncio.sleep(60)
            
            now = time.time()
            stale = [p for p, d in self.open_positions.items() 
                     if now - d.get("ts", 0) > MAX_POSITION_AGE]
            
            for pair in stale:
                self._log(f"TIMEOUT {pair} - closing position")
                del self.open_positions[pair]
    
    # ========================================================================
    # STATISTICS
    # ========================================================================
    
    async def _stats_loop(self):
        """Print statistics periodically."""
        while self._running:
            await asyncio.sleep(15)
            rt = time.time() - self._start
            
            bp_valid = sum(1 for b in self.bp.values() if b.best_bid > 0 and b.best_ask > 0)
            px_valid = sum(1 for p in self.px.values() if p.best_bid > 0 and p.best_ask > 0)
            
            # Calculate ALL spreads for all pairs
            all_spreads = []
            for pair in self.pairs:
                bp = self.bp.get(pair)
                px = self.px.get(pair)
                if not bp or not px:
                    continue
                if bp.best_bid <= 0 or bp.best_ask <= 0 or px.best_bid <= 0 or px.best_ask <= 0:
                    continue
                
                # Direction 1: Buy BP, Sell PX
                if px.best_bid > bp.best_ask:
                    spread1 = (px.best_bid - bp.best_ask) / bp.best_ask * 100
                    max_qty = min(bp.best_ask_qty, px.best_bid_qty, MAX_ORDER_USD / bp.best_ask)
                    profit1 = max_qty * (px.best_bid - bp.best_ask) - (max_qty * bp.best_ask * BP_FEE)
                    all_spreads.append((pair, spread1, profit1, "BP>PX", max_qty))
                
                # Direction 2: Buy PX, Sell BP
                if bp.best_bid > px.best_ask:
                    spread2 = (bp.best_bid - px.best_ask) / px.best_ask * 100
                    max_qty = min(px.best_ask_qty, bp.best_bid_qty, MAX_ORDER_USD / px.best_ask)
                    profit2 = max_qty * (bp.best_bid - px.best_ask) - (max_qty * bp.best_bid * BP_FEE)
                    all_spreads.append((pair, spread2, profit2, "PX>BP", max_qty))
            
            # Sort by spread descending
            all_spreads.sort(key=lambda x: x[1], reverse=True)
            top20 = all_spreads[:20]
            
            print(f"\n{'='*80}", flush=True)
            print(f" {int(rt//60)}m{int(rt%60)}s | upd:{self.updates:,} chk:{self.checks:,} "
                  f"exec:{self.executions} ${self.profit:.2f} err:{self.errors}", flush=True)
            print(f" Orderbooks: BP={bp_valid}/{len(self.bp)} PX={px_valid}/{len(self.px)} "
                  f"| Positions:{len(self.open_positions)} | Min spread: {MIN_SPREAD}%", flush=True)
            print(f"{'-'*80}", flush=True)
            print(f" {'PAIR':<10} {'SPREAD':>8} {'PROFIT':>10} {'ROUTE':<8} {'QTY':>10}", flush=True)
            print(f"{'-'*80}", flush=True)
            
            for pair, spread, profit, route, qty in top20:
                marker = ">>>" if spread >= MIN_SPREAD and profit >= MIN_PROFIT_USD else "   "
                print(f"{marker}{pair:<10} {spread:>7.3f}% ${profit:>9.2f} {route:<8} {qty:>10.4f}", flush=True)
            
            print(f"{'='*80}\n", flush=True)
    
    # ========================================================================
    # DISCOVERY
    # ========================================================================
    
    async def discover(self):
        """Discover common trading pairs between exchanges."""
        async with httpx.AsyncClient(timeout=30) as c:
            r1, r2 = await asyncio.gather(
                c.get('https://api.backpack.exchange/api/v1/markets'),
                c.get('https://api.prod.paradex.trade/v1/markets'),
            )
            
            bp_markets = r1.json()
            px_markets = r2.json()['results']
            
            # Build symbol maps
            bp = {m['symbol'].split('_')[0]: m['symbol'] 
                  for m in bp_markets if 'PERP' in m['symbol']}
            px = {m['symbol'].split('-')[0]: m['symbol'] 
                  for m in px_markets if 'PERP' in m['symbol']}
            
            # Load Backpack market specs
            for m in bp_markets:
                sym = m.get('symbol', '')
                if 'PERP' not in sym:
                    continue
                filters = m.get('filters', {})
                price_f = filters.get('price', {})
                qty_f = filters.get('quantity', {})
                self.backpack.market_specs[sym] = {
                    'tick_size': float(price_f.get('tickSize', '0.01')),
                    'step_size': float(qty_f.get('stepSize', '1')),
                    'min_qty': float(qty_f.get('minQuantity', '0.001')),
                }
            
            # Load Paradex market specs
            for m in px_markets:
                sym = m.get('symbol', '')
                if 'PERP' not in sym:
                    continue
                tick_size = float(m.get('price_tick_size', '0.0001'))
                step_size = float(m.get('quantity_step_size', '0.001'))
                min_qty = float(m.get('min_order_size', '0.001'))
                self.paradex.market_specs[sym] = {
                    'tick_size': tick_size,
                    'step_size': step_size,
                    'min_qty': min_qty,
                }
            
            self._log(f"Loaded {len(self.backpack.market_specs) + len(self.paradex.market_specs)} market specs")
        
        common = set(bp.keys()) & set(px.keys())
        for base in common:
            if base not in {"ORDER", "TRX"}:
                self.pairs[base] = {"bp": bp[base], "px": px[base]}
        
        return len(self.pairs)
    
    # ========================================================================
    # MAIN
    # ========================================================================
    
    async def run(self):
        """Main entry point."""
        self._running = True
        self._start = time.time()
        
        mode = "DRY" if self.dry_run else "LIVE"
        print(f"ARBITRAGE BOT v{self.VERSION} [{mode}]", flush=True)
        print(f"Min spread: {MIN_SPREAD}% | Min profit: ${MIN_PROFIT_USD} | Max order: ${MAX_ORDER_USD}", flush=True)
        print(f"Buffer: {SLIPPAGE_BUFFER*100:.2f}% | Cooldown: {COOLDOWN_SUCCESS}s/{COOLDOWN_FAILURE}s/{COOLDOWN_PARTIAL}s | Max concurrent: {MAX_CONCURRENT}", flush=True)
        
        n = await self.discover()
        print(f"Pairs: {n}", flush=True)
        
        # Initialize Paradex client
        if not self.dry_run:
            self.paradex.init()
        
        print("", flush=True)
        
        await asyncio.gather(
            self.backpack.stream(),
            self.paradex.stream(),
            self._stats_loop(),
            self._position_cleanup_loop(),
        )
    
    def stop(self):
        """Stop the bot."""
        self._running = False
