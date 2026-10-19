"""
Paper Trading Simulation Test.
Runs the arbitrage bot in paper trading mode to validate the entire flow.
"""

import asyncio
import logging
from decimal import Decimal
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


async def test_paper_trading_flow():
    """
    Test the complete paper trading flow:
    1. Create mock orderbooks with spread opportunity
    2. Detect opportunity
    3. Execute paper trade
    4. Verify PnL calculation
    """
    from src.core.orderbook import Orderbook
    from src.engine.arbitrage_engine import ArbitrageEngine, ArbitrageOpportunity
    from src.engine.order_executor import OrderExecutor
    from src.engine.risk_manager import risk_manager
    from src.exchanges.backpack_client import BackpackClient
    from src.exchanges.paradex_client import ParadexClient
    
    print("\n" + "=" * 60)
    print("  PAPER TRADING SIMULATION TEST")
    print("=" * 60)
    
    # 1. Create mock orderbooks with arbitrage spread
    print("\n📊 Creating mock orderbooks with 0.25% spread...")
    
    bp_book = Orderbook(exchange="backpack", symbol="SOL_USDC_PERP")
    px_book = Orderbook(exchange="paradex", symbol="SOL-USD-PERP")
    
    # Backpack: Ask at $136.50 (we buy here)
    bp_book.apply_snapshot(
        bids=[["136.40", "50.0"], ["136.35", "100.0"]],
        asks=[["136.50", "25.0"], ["136.55", "50.0"]],
        update_id=1
    )
    
    # Paradex: Bid at $136.85 (we sell here) = 0.256% gross spread
    px_book.apply_snapshot(
        bids=[["136.85", "30.0"], ["136.80", "60.0"]],
        asks=[["136.90", "20.0"], ["136.95", "40.0"]],
        update_id=1
    )
    
    print(f"   Backpack: Bid {bp_book.get_best_bid()[0]} | Ask {bp_book.get_best_ask()[0]}")
    print(f"   Paradex:  Bid {px_book.get_best_bid()[0]} | Ask {px_book.get_best_ask()[0]}")
    
    # 2. Setup arbitrage engine
    print("\n🔍 Setting up arbitrage engine...")
    engine = ArbitrageEngine()
    engine.profit_threshold = Decimal("0.10")  # Lower threshold for test
    engine.set_orderbooks(bp_book, px_book)
    
    # Calculate spreads
    spreads = engine.calculate_spreads()
    print(f"   BP → Paradex Spread: {spreads['bp_to_paradex']:+.4f}%")
    print(f"   Paradex → BP Spread: {spreads['paradex_to_bp']:+.4f}%")
    print(f"   Best Direction: {spreads['best_direction']}")
    
    # 3. Detect opportunity
    print("\n🎯 Detecting opportunity...")
    opportunity = engine.detect_opportunity()
    
    if not opportunity:
        print("   ❌ No opportunity detected!")
        return False
    
    print(f"   ✅ Opportunity detected!")
    print(f"   Direction: {opportunity.direction}")
    print(f"   Net Spread: {opportunity.spread_percent:.4f}%")
    print(f"   Buy @ {opportunity.buy_price} ({opportunity.buy_exchange})")
    print(f"   Sell @ {opportunity.sell_price} ({opportunity.sell_exchange})")
    print(f"   Available Qty: {opportunity.available_quantity}")
    
    # 4. Check if we can trade
    print("\n🛡️ Checking risk limits...")
    can_trade, reason = risk_manager.can_trade()
    if not can_trade:
        print(f"   ❌ Cannot trade: {reason}")
        return False
    print("   ✅ Risk check passed")
    
    # 5. Initialize order executor (with mock clients)
    print("\n💰 Executing paper trade...")
    
    # Create mock clients
    class MockBackpack:
        orderbook = bp_book
        async def place_order(self, **kwargs):
            return {"status": "paper", **kwargs}
    
    class MockParadex:
        orderbook = px_book
        async def place_order(self, **kwargs):
            return {"status": "paper", **kwargs}
    
    executor = OrderExecutor(MockBackpack(), MockParadex())
    
    # Execute
    result = await executor.execute_arbitrage(opportunity)
    
    # 6. Check results
    print("\n📈 Execution Results:")
    print(f"   Success: {result.success}")
    print(f"   Execution Time: {result.execution_time_ms:.1f}ms")
    
    if result.buy_order:
        print(f"   Buy Order: {result.buy_order.status.value}")
        print(f"     Exchange: {result.buy_order.exchange}")
        print(f"     Filled: {result.buy_order.filled_qty} @ ${result.buy_order.avg_price}")
        print(f"     Fee: ${result.buy_order.fee:.4f}")
    
    if result.sell_order:
        print(f"   Sell Order: {result.sell_order.status.value}")
        print(f"     Exchange: {result.sell_order.exchange}")
        print(f"     Filled: {result.sell_order.filled_qty} @ ${result.sell_order.avg_price}")
        print(f"     Fee: ${result.sell_order.fee:.4f}")
    
    print(f"\n   💵 Gross PnL: ${result.gross_pnl:.4f}")
    print(f"   💸 Total Fees: ${result.total_fees:.4f}")
    print(f"   💰 Net PnL: ${result.net_pnl:.4f}")
    
    # 7. Record trade in risk manager
    if result.success and result.buy_order and result.sell_order:
        risk_manager.record_trade(
            direction=opportunity.direction,
            quantity=result.buy_order.filled_qty,
            buy_price=result.buy_order.avg_price,
            sell_price=result.sell_order.avg_price,
            fees=result.total_fees
        )
    
    # 8. Show final stats
    print("\n📊 Risk Manager Stats:")
    stats = risk_manager.get_stats()
    for key, value in stats.items():
        print(f"   {key}: {value}")
    
    # 9. Show executor metrics
    print("\n📈 Executor Metrics:")
    metrics = executor.get_metrics()
    for key, value in metrics.items():
        print(f"   {key}: {value}")
    
    return result.success


async def test_multiple_trades():
    """Simulate multiple arbitrage trades."""
    from src.core.orderbook import Orderbook
    from src.engine.arbitrage_engine import ArbitrageEngine
    from src.engine.order_executor import OrderExecutor
    from src.engine.risk_manager import RiskManager
    from decimal import Decimal
    import random
    
    print("\n" + "=" * 60)
    print("  MULTIPLE TRADE SIMULATION (10 trades)")
    print("=" * 60)
    
    # Fresh risk manager for this test
    rm = RiskManager()
    
    bp_book = Orderbook(exchange="backpack", symbol="SOL_USDC_PERP")
    px_book = Orderbook(exchange="paradex", symbol="SOL-USD-PERP")
    
    engine = ArbitrageEngine()
    engine.profit_threshold = Decimal("0.10")
    engine.set_orderbooks(bp_book, px_book)
    
    class MockBackpack:
        orderbook = bp_book
        async def place_order(self, **kwargs):
            return {"status": "paper", **kwargs}
    
    class MockParadex:
        orderbook = px_book
        async def place_order(self, **kwargs):
            return {"status": "paper", **kwargs}
    
    executor = OrderExecutor(MockBackpack(), MockParadex())
    
    successful = 0
    total_pnl = Decimal("0")
    
    for i in range(10):
        # Generate random prices with spread
        base_price = Decimal("136.50") + Decimal(str(random.uniform(-2, 2)))
        spread = Decimal(str(random.uniform(0.15, 0.40)))  # 0.15% - 0.40% spread
        
        bp_ask = base_price
        px_bid = base_price * (1 + spread / 100)
        
        bp_book.apply_snapshot(
            bids=[[str(bp_ask - Decimal("0.05")), "50.0"]],
            asks=[[str(bp_ask), "25.0"]],
            update_id=i+1
        )
        
        px_book.apply_snapshot(
            bids=[[str(px_bid), "30.0"]],
            asks=[[str(px_bid + Decimal("0.05")), "20.0"]],
            update_id=i+1
        )
        
        # Detect and execute
        opportunity = engine.detect_opportunity()
        if opportunity:
            # Override cooldown for test
            rm.last_trade_time = None
            
            result = await executor.execute_arbitrage(opportunity)
            if result.success:
                successful += 1
                total_pnl += result.net_pnl
                
                rm.record_trade(
                    direction=opportunity.direction,
                    quantity=result.buy_order.filled_qty,
                    buy_price=result.buy_order.avg_price,
                    sell_price=result.sell_order.avg_price,
                    fees=result.total_fees
                )
        
        # Small delay
        await asyncio.sleep(0.05)
    
    print(f"\n   Trades Executed: 10")
    print(f"   Successful: {successful}")
    print(f"   Win Rate: {successful * 10}%")
    print(f"   Total Net PnL: ${total_pnl:.4f}")
    print(f"   Avg PnL per Trade: ${total_pnl / 10:.4f}")
    
    return successful > 5


async def main():
    """Run all paper trading tests."""
    print("\n" + "=" * 60)
    print("  PAPER TRADING VERIFICATION")
    print("  Backpack × Paradex Arbitrage Bot")
    print("=" * 60)
    
    results = []
    
    # Test 1: Single trade flow
    results.append(("Single Paper Trade", await test_paper_trading_flow()))
    
    # Test 2: Multiple trades
    results.append(("Multiple Trades Simulation", await test_multiple_trades()))
    
    # Summary
    print("\n" + "=" * 60)
    print("  TEST SUMMARY")
    print("=" * 60)
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"   {status} - {name}")
    
    all_passed = all(r[1] for r in results)
    print("\n" + ("✅ ALL TESTS PASSED!" if all_passed else "❌ SOME TESTS FAILED"))
    return all_passed


if __name__ == "__main__":
    asyncio.run(main())
