"""
Main entry point for the Backpack × Paradex Arbitrage Bot.
Professional-grade HFT arbitrage monitoring.
"""

import asyncio
import logging
import signal
import sys
from datetime import datetime

from src.config import config
from src.core.redis_client import redis_client
from src.exchanges.backpack_client import backpack_client
from src.exchanges.paradex_client import paradex_client
from src.engine.arbitrage_engine import arbitrage_engine, ArbitrageOpportunity
from src.engine.order_executor import OrderExecutor
from src.engine.risk_manager import risk_manager

# Setup logging - minimal output for production
log_format = '%(asctime)s | %(levelname)-5s | %(message)s'
logging.basicConfig(
    level=logging.WARNING if config.log_level == "INFO" else getattr(logging, config.log_level),
    format=log_format,
    datefmt='%H:%M:%S'
)

# Reduce noise from external libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("src.core.orderbook").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ArbitrageBot:
    """Main bot controller."""

    def __init__(self):
        self._running = False
        self._display_task = None
        self.order_executor = None
        self._start_time = None
        self._last_opportunity_time = None

    async def start(self) -> None:
        """Start the arbitrage bot."""
        self._start_time = datetime.now()
        
        # Clean startup banner
        print("\n" + "═" * 58)
        print("  ⚡ BACKPACK × PARADEX ARBITRAGE BOT")
        print("═" * 58)
        print(f"  Mode: {'📄 PAPER' if config.trading.paper_trading else '💰 LIVE'} | "
              f"Capital: ${config.trading.capital_usd} | "
              f"Leverage: {config.trading.max_leverage}x")
        print(f"  Threshold: {config.trading.profit_threshold_percent}% | "
              f"Symbols: {config.backpack.symbol} ↔ {config.paradex.symbol}")
        print("═" * 58 + "\n")
        
        self._running = True
        
        # Silent initialization
        try:
            await redis_client.connect()
        except Exception:
            pass  # Continue without Redis
        
        await backpack_client.connect()
        
        try:
            await paradex_client.connect()
        except Exception:
            pass  # Use public data only
        
        self.order_executor = OrderExecutor(backpack_client, paradex_client)
        
        # Start streams
        await backpack_client.start_orderbook_stream(on_update=self._on_backpack_update)
        await paradex_client.start_orderbook_stream(on_update=self._on_paradex_update)
        
        # Wait for data
        print("  Connecting to exchanges...", end="", flush=True)
        await asyncio.sleep(3)
        print(" ✓")
        
        # Setup arbitrage engine
        arbitrage_engine.set_orderbooks(
            backpack_client.orderbook,
            paradex_client.orderbook
        )
        
        await arbitrage_engine.start_monitoring(on_opportunity=self._on_opportunity)
        
        # Start display
        self._display_task = asyncio.create_task(self._display_loop())
        
        print("  Monitoring for arbitrage opportunities...\n")
        print("─" * 58)

    async def stop(self) -> None:
        """Gracefully stop the bot."""
        self._running = False
        
        if self._display_task:
            self._display_task.cancel()
            try:
                await self._display_task
            except asyncio.CancelledError:
                pass
        
        await arbitrage_engine.stop_monitoring()
        await backpack_client.disconnect()
        await paradex_client.disconnect()
        
        try:
            await redis_client.disconnect()
        except Exception:
            pass
        
        # Final stats
        if self.order_executor:
            metrics = self.order_executor.get_metrics()
            risk_stats = risk_manager.get_stats()
            runtime = datetime.now() - self._start_time if self._start_time else None
            
            print("\n\n" + "═" * 58)
            print("  SESSION SUMMARY")
            print("═" * 58)
            print(f"  Runtime: {str(runtime).split('.')[0] if runtime else 'N/A'}")
            print(f"  Trades: {metrics.get('total_trades', 0)} | "
                  f"Win Rate: {metrics.get('win_rate', '0%')} | "
                  f"PnL: ${metrics.get('total_pnl', 0)}")
            print(f"  Capital: ${risk_stats.get('current_capital', 'N/A')} | "
                  f"ROI: {risk_stats.get('roi_percent', '0%')} | "
                  f"Max DD: {risk_stats.get('max_drawdown', '0%')}")
            print("═" * 58 + "\n")

    async def _on_backpack_update(self, orderbook) -> None:
        """Handle Backpack orderbook update."""
        try:
            best_bid = orderbook.get_best_bid()
            best_ask = orderbook.get_best_ask()
            if best_bid:
                await redis_client.set_best_price("backpack", orderbook.symbol, "bid", str(best_bid[0]))
            if best_ask:
                await redis_client.set_best_price("backpack", orderbook.symbol, "ask", str(best_ask[0]))
        except Exception:
            pass

    async def _on_paradex_update(self, orderbook) -> None:
        """Handle Paradex orderbook update."""
        try:
            best_bid = orderbook.get_best_bid()
            best_ask = orderbook.get_best_ask()
            if best_bid:
                await redis_client.set_best_price("paradex", orderbook.symbol, "bid", str(best_bid[0]))
            if best_ask:
                await redis_client.set_best_price("paradex", orderbook.symbol, "ask", str(best_ask[0]))
        except Exception:
            pass

    async def _on_opportunity(self, opportunity: ArbitrageOpportunity) -> None:
        """Handle detected arbitrage opportunity."""
        self._last_opportunity_time = datetime.now()
        
        # Clear line and show opportunity
        print(f"\r{'':80}")  # Clear status line
        print("\n" + "🎯 " + "─" * 54)
        print(f"  OPPORTUNITY: {opportunity.direction.upper()}")
        print(f"  Spread: {opportunity.spread_percent:.4f}% | Qty: {opportunity.available_quantity:.4f}")
        print(f"  BUY {opportunity.buy_exchange.upper()}: ${opportunity.buy_price} → "
              f"SELL {opportunity.sell_exchange.upper()}: ${opportunity.sell_price}")
        print(f"  Est. Profit: ${float(opportunity.profit_per_unit * opportunity.available_quantity):.4f}")
        print("─" * 58)
        
        # Check risk
        can_trade, reason = risk_manager.can_trade()
        if not can_trade:
            print(f"  ⚠ Blocked: {reason}")
            return
        
        # Execute
        if self.order_executor:
            result = await self.order_executor.execute_arbitrage(opportunity)
            
            if result.success:
                print(f"  ✓ Executed | Net PnL: ${result.net_pnl:.4f}")
                
                if result.buy_order and result.sell_order:
                    risk_manager.record_trade(
                        direction=opportunity.direction,
                        quantity=result.buy_order.filled_qty,
                        buy_price=result.buy_order.avg_price,
                        sell_price=result.sell_order.avg_price,
                        fees=result.total_fees
                    )
            else:
                print(f"  ✗ Failed: {result.error_message}")
        
        print()

    async def _display_loop(self) -> None:
        """Clean status display."""
        while self._running:
            try:
                await asyncio.sleep(2)
                
                status = arbitrage_engine.get_status()
                spreads = status.get("current_spreads", {})
                
                if "error" not in spreads:
                    bp_bid = spreads.get("bp_bid", "---")
                    bp_ask = spreads.get("bp_ask", "---")
                    px_bid = spreads.get("px_bid", "---")
                    px_ask = spreads.get("px_ask", "---")
                    
                    bp_to_px = spreads.get("bp_to_paradex", 0)
                    px_to_bp = spreads.get("paradex_to_bp", 0)
                    best_spread = max(bp_to_px, px_to_bp)
                    
                    # Get metrics
                    metrics = self.order_executor.get_metrics() if self.order_executor else {}
                    trades = metrics.get("total_trades", 0)
                    pnl = metrics.get("total_pnl", "0")
                    
                    # Compact status line
                    time_str = datetime.now().strftime('%H:%M:%S')
                    
                    print(f"\r  {time_str} │ "
                          f"BP {bp_bid}/{bp_ask} │ "
                          f"PX {px_bid}/{px_ask} │ "
                          f"Δ {best_spread:+.3f}% │ "
                          f"T:{trades} P:${pnl}     ", 
                          end="", flush=True)
                
            except asyncio.CancelledError:
                break
            except Exception:
                pass


async def main():
    """Main entry point."""
    bot = ArbitrageBot()
    
    try:
        await bot.start()
        
        while bot._running:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        pass
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
