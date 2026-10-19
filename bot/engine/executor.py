"""
Order Executor - Executes arbitrage trades on both exchanges
"""

import asyncio
import time
from typing import TYPE_CHECKING

from bot.config import (
    DRY_RUN, SLIPPAGE_BUFFER,
    COOLDOWN_SUCCESS, COOLDOWN_FAILURE, COOLDOWN_PARTIAL
)
from bot.models import Opportunity

if TYPE_CHECKING:
    from bot.main import ArbitrageBot


class OrderExecutor:
    """Executes arbitrage trades on both exchanges."""
    
    def __init__(self, bot: "ArbitrageBot"):
        self.bot = bot
    
    async def execute(self, opp: Opportunity):
        """Execute an arbitrage opportunity."""
        pair = opp.pair
        t0 = time.time()
        cooldown_time = COOLDOWN_FAILURE
        
        try:
            if DRY_RUN:
                cooldown_time = await self._execute_dry(opp, t0)
            else:
                cooldown_time = await self._execute_live(opp, t0)
                
        except Exception as e:
            self.bot._error(f"Execute {pair}: {e}")
            cooldown_time = COOLDOWN_FAILURE
        finally:
            # Always remove from executing and apply cooldown
            self.bot.executing.discard(pair)
            self.bot.cooldown[pair] = time.time() + cooldown_time
            self.bot._debug(f"{pair} cooldown for {cooldown_time}s")
    
    async def _execute_dry(self, opp: Opportunity, t0: float) -> float:
        """Simulate execution (dry run)."""
        await asyncio.sleep(0.01)
        latency = (time.time() - t0) * 1000
        
        self.bot.executions += 1
        self.bot.profit += opp.profit
        
        self.bot.open_positions[opp.pair] = {
            "buy": opp.buy_exchange,
            "sell": opp.sell_exchange,
            "qty": opp.qty,
            "entry_spread": opp.spread,
            "ts": time.time(),
        }
        
        route = f"{opp.buy_exchange}>{opp.sell_exchange}"
        self.bot._log(
            f"EXEC {opp.pair:8} {route} {opp.spread:.2f}% "
            f"${opp.profit:.2f} {latency:.1f}ms"
        )
        return COOLDOWN_SUCCESS
    
    async def _execute_live(self, opp: Opportunity, t0: float) -> float:
        """Execute real trades on both exchanges."""
        pair = opp.pair
        
        # Get market symbols
        bp_symbol = self.bot.pairs[pair]["bp"]
        px_symbol = self.bot.pairs[pair]["px"]
        
        # Log opportunity details
        route = f"{opp.buy_exchange}>{opp.sell_exchange}"
        self.bot._log(f"")
        self.bot._log(f"{'='*60}")
        self.bot._log(f">>> EXEC {pair} {route} spread={opp.spread:.3f}%")
        self.bot._log(f"{'='*60}")
        
        # VERBOSE: Show CURRENT orderbook state
        bp_ob = self.bot.bp.get(pair)
        px_ob = self.bot.px.get(pair)
        self.bot._log(f"[ORDERBOOK STATE NOW]")
        if bp_ob:
            self.bot._log(f"  BP: bid={bp_ob.best_bid:.2f} (qty={bp_ob.best_bid_qty:.4f}) | ask={bp_ob.best_ask:.2f} (qty={bp_ob.best_ask_qty:.4f})")
        if px_ob:
            self.bot._log(f"  PX: bid={px_ob.best_bid:.2f} (qty={px_ob.best_bid_qty:.4f}) | ask={px_ob.best_ask:.2f} (qty={px_ob.best_ask_qty:.4f})")
        
        # VERBOSE: Show opportunity prices (from detection time)
        self.bot._log(f"[OPPORTUNITY PRICES (from detection)]")
        self.bot._log(f"  BUY  @ {opp.buy_price:.6f} qty={opp.qty:.6f} on {opp.buy_exchange}")
        self.bot._log(f"  SELL @ {opp.sell_price:.6f} qty={opp.qty:.6f} on {opp.sell_exchange}")
        
        try:
            # Apply slippage buffer
            buy_price_buffered = opp.buy_price * (1 + SLIPPAGE_BUFFER)
            sell_price_buffered = opp.sell_price * (1 - SLIPPAGE_BUFFER)
            
            # VERBOSE: Show buffered prices
            self.bot._log(f"[BUFFERED PRICES (slippage={SLIPPAGE_BUFFER*100:.2f}%)]")
            self.bot._log(f"  BUY  @ {buy_price_buffered:.6f} (original + {SLIPPAGE_BUFFER*100:.2f}%)")
            self.bot._log(f"  SELL @ {sell_price_buffered:.6f} (original - {SLIPPAGE_BUFFER*100:.2f}%)")
            
            # Execute both legs in PARALLEL
            if opp.buy_exchange == "BP":
                self.bot._log(f"[SENDING ORDERS - PARALLEL]")
                self.bot._log(f"  -> BP BUY  {bp_symbol} qty={opp.qty:.6f} @ {buy_price_buffered:.6f}")
                self.bot._log(f"  -> PX SELL {px_symbol} qty={opp.qty:.6f} @ {sell_price_buffered:.6f}")
                buy_task = asyncio.create_task(
                    self.bot.backpack.buy(bp_symbol, opp.qty, buy_price_buffered)
                )
                sell_task = asyncio.create_task(
                    self.bot.paradex.sell(px_symbol, opp.qty, sell_price_buffered)
                )
            else:
                self.bot._log(f"[SENDING ORDERS - PARALLEL]")
                self.bot._log(f"  -> PX BUY  {px_symbol} qty={opp.qty:.6f} @ {buy_price_buffered:.6f}")
                self.bot._log(f"  -> BP SELL {bp_symbol} qty={opp.qty:.6f} @ {sell_price_buffered:.6f}")
                buy_task = asyncio.create_task(
                    self.bot.paradex.buy(px_symbol, opp.qty, buy_price_buffered)
                )
                sell_task = asyncio.create_task(
                    self.bot.backpack.sell(bp_symbol, opp.qty, sell_price_buffered)
                )
            
            # Wait for both
            buy_result, sell_result = await asyncio.gather(
                buy_task, sell_task, return_exceptions=True
            )
            
            latency = (time.time() - t0) * 1000
            
            # VERBOSE: Log detailed results
            buy_ok = not isinstance(buy_result, Exception)
            sell_ok = not isinstance(sell_result, Exception)
            
            self.bot._log(f"[API RESPONSES (latency={latency:.1f}ms)]")
            self.bot._log(f"  BUY  {'OK' if buy_ok else 'FAIL'}:")
            if buy_ok:
                self.bot._log(f"    {buy_result}")
            else:
                self.bot._log(f"    ERROR: {buy_result}")
            
            self.bot._log(f"  SELL {'OK' if sell_ok else 'FAIL'}:")
            if sell_ok:
                self.bot._log(f"    {sell_result}")
            else:
                self.bot._log(f"    ERROR: {sell_result}")
            
            # Check actual fill quantities
            if opp.buy_exchange == "BP":
                buy_filled_qty = self.bot.backpack.get_fill_qty(buy_result) if buy_ok else 0
                sell_filled_qty = self.bot.paradex.get_fill_qty(sell_result) if sell_ok else 0
            else:
                buy_filled_qty = self.bot.paradex.get_fill_qty(buy_result) if buy_ok else 0
                sell_filled_qty = self.bot.backpack.get_fill_qty(sell_result) if sell_ok else 0
            
            self.bot._log(f"    BUY  filled qty: {buy_filled_qty}")
            self.bot._log(f"    SELL filled qty: {sell_filled_qty}")
            
            # Determine outcome
            both_filled = buy_filled_qty > 0 and sell_filled_qty > 0
            one_filled = (buy_filled_qty > 0) != (sell_filled_qty > 0)
            
            if both_filled:
                # SUCCESS
                self.bot.executions += 1
                self.bot.profit += opp.profit
                
                self.bot.open_positions[pair] = {
                    "buy": opp.buy_exchange,
                    "sell": opp.sell_exchange,
                    "qty": opp.qty,
                    "entry_spread": opp.spread,
                    "ts": time.time(),
                    "buy_order": buy_result,
                    "sell_order": sell_result,
                }
                
                self.bot._log(
                    f"<<< DONE {pair:8} {route} {opp.spread:.2f}% "
                    f"${opp.profit:.2f} {latency:.1f}ms"
                )
                return COOLDOWN_SUCCESS
                
            elif one_filled:
                # PARTIAL FILL - DANGEROUS!
                if buy_filled_qty > 0:
                    self.bot._error(f"PARTIAL FILL! {pair}: BUY filled ({buy_filled_qty}), SELL failed!")
                    self.bot._error(f"  ACTION NEEDED: You have LONG exposure on {opp.buy_exchange}")
                else:
                    self.bot._error(f"PARTIAL FILL! {pair}: SELL filled ({sell_filled_qty}), BUY failed!")
                    self.bot._error(f"  ACTION NEEDED: You have SHORT exposure on {opp.sell_exchange}")
                return COOLDOWN_PARTIAL
                
            else:
                # NONE FILLED
                self.bot._log(f"    NO FILLS {pair} - orders expired/failed, no exposure")
                return COOLDOWN_FAILURE
                    
        except Exception as e:
            self.bot._error(f"Live exec {pair}: {e}")
            return COOLDOWN_FAILURE
