"""
Debug: Test Paradex order execution to understand the flow.
"""
import os
import time

# Ensure env vars are set
if not os.getenv("PARADEX_L2_ADDRESS"):
    print("[ERROR] Set PARADEX_L2_ADDRESS and PARADEX_L2_PRIVATE_KEY")
    exit(1)

from clients.paradex_trading import ParadexClient

def test_paradex_order():
    print("=" * 60)
    print("PARADEX ORDER DEBUG TEST")
    print("=" * 60)
    
    # Initialize client
    client = ParadexClient()
    
    # Get current BBO for PUMP
    market = "PUMP-USD-PERP"
    print(f"\n1. Getting BBO for {market}...")
    bbo = client.get_bbo(market)
    print(f"   BBO: {bbo}")
    
    bid = float(bbo.get("bid", 0))
    ask = float(bbo.get("ask", 0))
    print(f"   Bid: {bid}")
    print(f"   Ask: {ask}")
    
    # Test 1: SELL order at bid price (should fill immediately)
    print(f"\n2. Placing SELL order at BID price ({bid})...")
    print("   This should fill immediately with IOC")
    
    result = client.place_limit_order(
        market=market,
        side="SELL",
        size=100,  # Minimum qty
        price=bid,
        instruction="IOC",
    )
    
    print(f"\n3. Order result:")
    print(f"   ID: {result.get('id', 'N/A')}")
    print(f"   Status: {result.get('status', 'N/A')}")
    print(f"   Size: {result.get('size', 'N/A')}")
    print(f"   Remaining: {result.get('remaining_size', 'N/A')}")
    print(f"   Avg Fill: {result.get('avg_fill_price', 'N/A')}")
    print(f"   Full response: {result}")
    
    # Wait a bit
    print("\n4. Waiting 2 seconds...")
    time.sleep(2)
    
    # Check order status
    print("\n5. Checking order status...")
    order_id = result.get('id', '')
    if order_id:
        # Get open orders to see if it's still there
        open_orders = client.get_open_orders(market)
        print(f"   Open orders: {open_orders}")
        
        # Get fills
        fills = client.get_fills(market, limit=5)
        print(f"   Recent fills: {fills}")
    
    print("\n" + "=" * 60)
    print("DEBUG COMPLETE")


if __name__ == "__main__":
    test_paradex_order()
