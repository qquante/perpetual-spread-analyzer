"""
Quick test script to verify orderbook construction works correctly.
Tests the Backpack orderbook stream without requiring full credentials.
"""

import asyncio
import logging
from decimal import Decimal

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


async def test_orderbook_class():
    """Test the Orderbook class with mock data."""
    from src.core.orderbook import Orderbook
    
    print("\n" + "=" * 60)
    print("  TEST 1: Orderbook Class")
    print("=" * 60)
    
    book = Orderbook(exchange="test", symbol="SOL_USDC_PERP")
    
    # Apply snapshot
    snapshot_bids = [
        ["160.50", "10.5"],
        ["160.45", "20.0"],
        ["160.40", "50.0"]
    ]
    snapshot_asks = [
        ["160.55", "8.0"],
        ["160.60", "15.0"],
        ["160.65", "30.0"]
    ]
    
    book.apply_snapshot(snapshot_bids, snapshot_asks, update_id=1000)
    
    print(f"✅ Snapshot applied: {book}")
    print(f"   Best Bid: {book.get_best_bid()}")
    print(f"   Best Ask: {book.get_best_ask()}")
    print(f"   Spread: {book.get_spread()}")
    print(f"   Mid Price: {book.get_mid_price()}")
    
    # Apply delta - remove a level
    delta_bids = [["160.50", "0"]]  # Remove best bid
    delta_asks = [["160.52", "5.0"]]  # Add new best ask
    
    book.apply_delta(delta_bids, delta_asks, 1001, 1001)
    
    print(f"\n✅ Delta applied:")
    print(f"   New Best Bid: {book.get_best_bid()}")
    print(f"   New Best Ask: {book.get_best_ask()}")
    print(f"   New Spread: {book.get_spread()}")
    
    # Test depth
    depth = book.get_depth(3)
    print(f"\n   Depth (top 3):")
    print(f"   Bids: {depth['bids']}")
    print(f"   Asks: {depth['asks']}")
    
    return True


async def test_backpack_snapshot():
    """Test fetching a real snapshot from Backpack."""
    print("\n" + "=" * 60)
    print("  TEST 2: Backpack REST API Snapshot")
    print("=" * 60)
    
    import httpx
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.backpack.exchange/api/v1/depth",
            params={"symbol": "SOL_USDC_PERP"}
        )
        
        if response.status_code == 200:
            data = response.json()
            bids = data.get("bids", [])[:5]
            asks = data.get("asks", [])[:5]
            
            print(f"✅ Snapshot received!")
            print(f"   Total Bids: {len(data.get('bids', []))}")
            print(f"   Total Asks: {len(data.get('asks', []))}")
            print(f"\n   Top 5 Bids:")
            for price, qty in bids:
                print(f"      {price} @ {qty}")
            print(f"\n   Top 5 Asks:")
            for price, qty in asks:
                print(f"      {price} @ {qty}")
            
            return True
        else:
            print(f"❌ Failed: {response.status_code}")
            return False


async def test_backpack_websocket():
    """Test WebSocket connection to Backpack."""
    print("\n" + "=" * 60)
    print("  TEST 3: Backpack WebSocket Stream (10 seconds)")
    print("=" * 60)
    
    import websockets
    import orjson
    from src.core.orderbook import Orderbook
    
    book = Orderbook(exchange="backpack", symbol="SOL_USDC_PERP")
    update_count = 0
    
    try:
        async with websockets.connect("wss://ws.backpack.exchange") as ws:
            print("✅ Connected to WebSocket")
            
            # Subscribe to depth stream
            subscribe_msg = {
                "method": "SUBSCRIBE",
                "params": ["depth.SOL_USDC_PERP"]
            }
            await ws.send(orjson.dumps(subscribe_msg).decode())
            print("✅ Subscribed to depth.SOL_USDC_PERP")
            
            # Get initial snapshot
            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.backpack.exchange/api/v1/depth",
                    params={"symbol": "SOL_USDC_PERP"}
                )
                data = response.json()
                book.apply_snapshot(
                    data.get("bids", []),
                    data.get("asks", []),
                    data.get("lastUpdateId", 0)
                )
            
            # Listen for updates
            start_time = asyncio.get_event_loop().time()
            
            async for message in ws:
                data = orjson.loads(message)
                
                if "stream" in data and data["stream"].startswith("depth."):
                    payload = data.get("data", {})
                    if payload.get("e") == "depth":
                        bids = payload.get("b", [])
                        asks = payload.get("a", [])
                        first_id = payload.get("U", 0)
                        last_id = payload.get("u", 0)
                        
                        book.apply_delta(bids, asks, first_id, last_id)
                        update_count += 1
                        
                        # Print every 10 updates
                        if update_count % 10 == 0:
                            best_bid = book.get_best_bid()
                            best_ask = book.get_best_ask()
                            spread = book.get_spread()
                            
                            bid_str = str(best_bid[0]) if best_bid else "N/A"
                            ask_str = str(best_ask[0]) if best_ask else "N/A"
                            spread_str = f"{float(spread[1]):.4f}%" if spread else "N/A"
                            
                            print(f"   [{update_count:4d}] Bid: {bid_str} | Ask: {ask_str} | Spread: {spread_str}")
                
                # Stop after 10 seconds
                if asyncio.get_event_loop().time() - start_time > 10:
                    break
            
            print(f"\n✅ Received {update_count} updates in 10 seconds")
            print(f"   Final book state: {book}")
            return True
            
    except Exception as e:
        import traceback
        print(f"❌ WebSocket error: {e}")
        traceback.print_exc()
        return False


async def test_arbitrage_engine():
    """Test the arbitrage engine with mock data."""
    print("\n" + "=" * 60)
    print("  TEST 4: Arbitrage Engine Spread Calculation")
    print("=" * 60)
    
    from src.core.orderbook import Orderbook
    from src.engine.arbitrage_engine import ArbitrageEngine
    
    engine = ArbitrageEngine()
    
    # Create mock orderbooks with arbitrage opportunity
    bp_book = Orderbook(exchange="backpack", symbol="SOL_USDC_PERP")
    px_book = Orderbook(exchange="paradex", symbol="SOL-USD-PERP")
    
    # Backpack: Ask 160.50 (we can buy here)
    bp_book.apply_snapshot(
        bids=[["160.40", "10.0"], ["160.35", "20.0"]],
        asks=[["160.50", "15.0"], ["160.55", "25.0"]],
        update_id=1
    )
    
    # Paradex: Bid 160.80 (we can sell here) - 0.187% spread!
    px_book.apply_snapshot(
        bids=[["160.80", "12.0"], ["160.75", "18.0"]],
        asks=[["160.85", "10.0"], ["160.90", "20.0"]],
        update_id=1
    )
    
    engine.set_orderbooks(bp_book, px_book)
    
    spreads = engine.calculate_spreads()
    print(f"✅ Spread Calculation:")
    print(f"   Backpack Ask: {spreads['bp_ask']}")
    print(f"   Paradex Bid: {spreads['px_bid']}")
    print(f"   BP → Paradex Spread: {spreads['bp_to_paradex']:+.4f}%")
    print(f"   Paradex → BP Spread: {spreads['paradex_to_bp']:+.4f}%")
    print(f"   Best Direction: {spreads['best_direction']}")
    print(f"   Profit Threshold: {spreads['threshold']}%")
    
    # Detect opportunity
    opportunity = engine.detect_opportunity()
    if opportunity:
        print(f"\n🎯 OPPORTUNITY DETECTED:")
        print(f"   Direction: {opportunity.direction}")
        print(f"   Net Spread: {opportunity.spread_percent:.4f}%")
        print(f"   Buy @ {opportunity.buy_price} on {opportunity.buy_exchange}")
        print(f"   Sell @ {opportunity.sell_price} on {opportunity.sell_exchange}")
        print(f"   Profit/Unit: ${opportunity.profit_per_unit:.4f}")
    else:
        print(f"\n   No opportunity above threshold")
    
    return True


async def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("  ORDERBOOK VERIFICATION TEST")
    print("  Backpack × Paradex Arbitrage Bot")
    print("=" * 60)
    
    results = []
    
    # Test 1: Orderbook class
    results.append(("Orderbook Class", await test_orderbook_class()))
    
    # Test 2: REST API snapshot
    results.append(("Backpack REST API", await test_backpack_snapshot()))
    
    # Test 3: WebSocket stream
    results.append(("Backpack WebSocket", await test_backpack_websocket()))
    
    # Test 4: Arbitrage engine
    results.append(("Arbitrage Engine", await test_arbitrage_engine()))
    
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
