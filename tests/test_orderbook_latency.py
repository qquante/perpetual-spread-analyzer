"""
Debug: Compare cached orderbook prices vs real-time BBO from API.
This will show if the orderbook is stale.
"""
import asyncio
import time
import httpx
import websockets
import orjson

async def test_orderbook_latency():
    print("=" * 70)
    print("ORDERBOOK LATENCY TEST")
    print("=" * 70)
    print("Testing: FARTCOIN-USD-PERP on Paradex")
    print()
    
    # 1. Connect to WebSocket and subscribe
    print("1. Connecting to Paradex WebSocket...")
    
    cached_bid = None
    cached_ask = None
    cached_ts = None
    
    async with websockets.connect("wss://ws.api.prod.paradex.trade/v1") as ws:
        # Subscribe to FARTCOIN with 50ms updates
        await ws.send(orjson.dumps({
            "jsonrpc": "2.0",
            "method": "subscribe",
            "params": {"channel": "order_book.FARTCOIN-USD-PERP.snapshot@15@50ms"},
            "id": 1
        }).decode())
        
        print("   Subscribed. Waiting for updates...")
        
        # Wait for a few updates
        update_count = 0
        async for msg in ws:
            data = orjson.loads(msg)
            
            if "params" in data and data.get("method") == "subscription":
                bd = data["params"].get("data", {})
                
                # Find best bid and ask from inserts/updates
                bids = []
                asks = []
                
                for e in bd.get("inserts", []) + bd.get("updates", []):
                    if e.get("side") == "BUY":
                        bids.append(float(e["price"]))
                    else:
                        asks.append(float(e["price"]))
                
                if bids:
                    cached_bid = max(bids)
                if asks:
                    cached_ask = min(asks)
                cached_ts = time.time()
                
                update_count += 1
                if update_count >= 5:
                    print(f"   Got {update_count} updates. Cached bid={cached_bid} ask={cached_ask}")
                    break
        
        # 2. Now get real BBO via REST API
        print()
        print("2. Getting real BBO via REST API...")
        
        async with httpx.AsyncClient(timeout=5) as c:
            t0 = time.time()
            r = await c.get("https://api.prod.paradex.trade/v1/bbo/FARTCOIN-USD-PERP")
            api_latency = (time.time() - t0) * 1000
            bbo = r.json()
        
        real_bid = float(bbo.get("bid", 0))
        real_ask = float(bbo.get("ask", 0))
        real_ts = bbo.get("last_updated_at", 0)
        
        print(f"   Real BBO: bid={real_bid} ask={real_ask}")
        print(f"   API latency: {api_latency:.1f}ms")
        
        # 3. Compare
        print()
        print("3. COMPARISON:")
        print("-" * 50)
        print(f"   Cached bid: {cached_bid}")
        print(f"   Real bid:   {real_bid}")
        print(f"   Diff:       {abs(cached_bid - real_bid) if cached_bid and real_bid else 'N/A'}")
        print()
        print(f"   Cached ask: {cached_ask}")
        print(f"   Real ask:   {real_ask}")
        print(f"   Diff:       {abs(cached_ask - real_ask) if cached_ask and real_ask else 'N/A'}")
        
        if cached_bid and real_bid:
            bid_diff_pct = abs(cached_bid - real_bid) / real_bid * 100
            ask_diff_pct = abs(cached_ask - real_ask) / real_ask * 100 if cached_ask and real_ask else 0
            
            print()
            print(f"   Bid diff %: {bid_diff_pct:.4f}%")
            print(f"   Ask diff %: {ask_diff_pct:.4f}%")
            
            if bid_diff_pct > 0.1 or ask_diff_pct > 0.1:
                print()
                print("   ⚠️  ORDERBOOK IS STALE! Diff > 0.1%")
            else:
                print()
                print("   ✅ Orderbook looks accurate")
        
        # 4. Continuous monitoring
        print()
        print("4. Monitoring updates for 5 seconds...")
        
        start = time.time()
        count = 0
        while time.time() - start < 5:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=1)
                data = orjson.loads(msg)
                if "params" in data and data.get("method") == "subscription":
                    count += 1
            except asyncio.TimeoutError:
                pass
        
        print(f"   Received {count} updates in 5 seconds ({count/5:.1f}/sec)")
        print(f"   Expected: ~100/sec at 50ms intervals")
        
        if count < 50:
            print("   ⚠️  UPDATES ARE SLOW!")
        else:
            print("   ✅ Update rate looks good")

    print()
    print("=" * 70)
    print("TEST COMPLETE")


if __name__ == "__main__":
    asyncio.run(test_orderbook_latency())
