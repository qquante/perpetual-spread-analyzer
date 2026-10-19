"""
Debug: Compare the BUY price we send vs what Paradex orderbook shows.
This tests if our price is actually executable.
"""
import os
import asyncio
import time

if not os.getenv("PARADEX_L2_ADDRESS"):
    print("[ERROR] Set PARADEX_L2_ADDRESS and PARADEX_L2_PRIVATE_KEY")
    exit(1)

from clients.paradex_trading import ParadexClient

def test_price_match():
    print("=" * 70)
    print("PARADEX PRICE MATCH TEST")
    print("=" * 70)
    
    client = ParadexClient()
    market = "FARTCOIN-USD-PERP"
    
    # 1. Get current BBO
    print(f"\n1. Getting BBO for {market}...")
    bbo = client.get_bbo(market)
    bid = float(bbo.get("bid", 0))
    ask = float(bbo.get("ask", 0))
    print(f"   Bid: {bid}")
    print(f"   Ask: {ask}")
    
    # 2. Get market info for tick size
    print(f"\n2. Getting market info...")
    markets = client.get_markets()
    market_info = next((m for m in markets.get("results", []) if m.get("symbol") == market), None)
    if market_info:
        tick_size = market_info.get("price_tick_size", "?")
        step_size = market_info.get("order_size_increment", "?")
        print(f"   Price tick size: {tick_size}")
        print(f"   Order size increment: {step_size}")
    
    # 3. Test BUY order at ASK price (should fill)
    print(f"\n3. Testing BUY @ ask price ({ask})...")
    
    # Use minimum size
    min_size = float(market_info.get("order_size_increment", 1)) if market_info else 1
    test_size = max(min_size, 10)  # At least 10
    
    result = client.place_limit_order(
        market=market,
        side="BUY",
        size=test_size,
        price=ask,  # Using exact ASK price
        instruction="IOC",
    )
    
    print(f"\n4. Order result:")
    print(f"   Status: {result.get('status')}")
    print(f"   Size: {result.get('size')}")
    print(f"   Remaining: {result.get('remaining_size')}")
    print(f"   Avg fill: {result.get('avg_fill_price')}")
    
    # Wait and check fills
    time.sleep(2)
    
    print(f"\n5. Checking fills...")
    fills = client.get_fills(market, limit=5)
    recent = fills.get("results", [])
    if recent:
        for f in recent[:3]:
            print(f"   {f.get('side')} {f.get('size')} @ {f.get('price')} ({f.get('fill_type')})")
    else:
        print("   No recent fills")
    
    # 6. Test BUY order ABOVE ask (should definitely fill)
    print(f"\n6. Testing BUY @ ask * 1.01 ({ask * 1.01:.6f})...")
    
    result2 = client.place_limit_order(
        market=market,
        side="BUY",
        size=test_size,
        price=ask * 1.01,
        instruction="IOC",
    )
    
    print(f"\n7. Order result:")
    print(f"   Status: {result2.get('status')}")
    print(f"   Remaining: {result2.get('remaining_size')}")
    print(f"   Avg fill: {result2.get('avg_fill_price')}")
    
    time.sleep(2)
    fills2 = client.get_fills(market, limit=5)
    recent2 = fills2.get("results", [])
    if recent2:
        print(f"\n8. Latest fills:")
        for f in recent2[:3]:
            print(f"   {f.get('side')} {f.get('size')} @ {f.get('price')} ({f.get('fill_type')})")
    
    print("\n" + "=" * 70)
    print("CONCLUSION")
    print("=" * 70)
    
    filled1 = result.get('remaining_size', result.get('size', '?'))
    filled2 = result2.get('remaining_size', result2.get('size', '?'))
    
    if str(filled1) == "0" or str(filled2) == "0":
        print("✅ At least one order filled! Price format is correct.")
    else:
        print("⚠️  No orders filled. There may be a price/size format issue.")
        print("   Or the market is too thin.")


if __name__ == "__main__":
    test_price_match()
