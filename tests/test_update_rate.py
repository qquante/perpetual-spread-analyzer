"""
Debug: Test the actual update rate per market from Paradex WebSocket.
"""
import asyncio
import time
import websockets
import orjson
from collections import defaultdict

async def test_update_rate():
    print("=" * 70)
    print("PARADEX UPDATE RATE TEST (per market)")
    print("=" * 70)
    
    markets = ["BTC-USD-PERP", "ETH-USD-PERP", "FARTCOIN-USD-PERP"]
    updates_per_market = defaultdict(int)
    
    async with websockets.connect("wss://ws.api.prod.paradex.trade/v1") as ws:
        # Subscribe to 3 markets
        for i, market in enumerate(markets):
            await ws.send(orjson.dumps({
                "jsonrpc": "2.0",
                "method": "subscribe",
                "params": {"channel": f"order_book.{market}.snapshot@15@50ms"},
                "id": i + 1
            }).decode())
            print(f"Subscribed to {market} @ 50ms")
        
        print()
        print("Counting updates for 10 seconds...")
        
        start = time.time()
        while time.time() - start < 10:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=1)
                data = orjson.loads(msg)
                if "params" in data and data.get("method") == "subscription":
                    ch = data["params"].get("channel", "")
                    if "order_book." in ch:
                        market = ch.split(".")[1]
                        updates_per_market[market] += 1
            except asyncio.TimeoutError:
                pass
        
        print()
        print("Results:")
        print("-" * 50)
        for market, count in updates_per_market.items():
            rate = count / 10
            expected = 20  # 50ms = 20 updates/sec
            print(f"   {market}: {count} updates ({rate:.1f}/sec, expected ~{expected}/sec)")
        
        total = sum(updates_per_market.values())
        print(f"\n   TOTAL: {total} updates ({total/10:.1f}/sec)")
        print()
        
        # Check if it's per-market or total
        if total / 10 > 50:
            print("   ✅ Updates are per-market (separate streams)")
        else:
            print("   ⚠️  Updates might be throttled or batched")

    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(test_update_rate())
