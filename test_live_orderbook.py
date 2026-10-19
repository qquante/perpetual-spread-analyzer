#!/usr/bin/env python3
"""
Live Orderbook Test - Shows RAW data and verifies orderbook construction

This script:
1. Connects to Backpack and Paradex WebSockets
2. Shows RAW messages arriving
3. Shows how orderbook is being built
4. Compares with REST API snapshot

Run: python test_live_orderbook.py
"""

import asyncio
import sys
import io
import time
import httpx
import websockets
import orjson

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')

from bot.orderbook import FastOrderbook

# Test only BTC
SYMBOL_BP = "BTC_USDC_PERP"
SYMBOL_PX = "BTC-USD-PERP"


async def test_backpack():
    """Test Backpack orderbook with RAW data."""
    print("\n" + "=" * 60)
    print("BACKPACK ORDERBOOK TEST")
    print("=" * 60)
    
    ob = FastOrderbook()
    
    # Step 1: Get REST snapshot
    print("\n[1] REST Snapshot...")
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get('https://api.backpack.exchange/api/v1/depth', 
                       params={'symbol': SYMBOL_BP})
        data = r.json()
        
        rest_bids = [(float(p), float(q)) for p, q in data.get('bids', [])[:5]]
        rest_asks = [(float(p), float(q)) for p, q in data.get('asks', [])[:5]]
        
        print(f"  RAW bids (top 5): {data.get('bids', [])[:5]}")
        print(f"  RAW asks (top 5): {data.get('asks', [])[:5]}")
        
        # Build orderbook from REST
        all_bids = [(float(p), float(q)) for p, q in data.get('bids', [])[:20]]
        all_asks = [(float(p), float(q)) for p, q in data.get('asks', [])[:20]]
        ob.set_full(all_bids, all_asks)
        
        print(f"\n  Orderbook after set_full:")
        print(f"    best_bid: {ob.best_bid} qty={ob.best_bid_qty}")
        print(f"    best_ask: {ob.best_ask} qty={ob.best_ask_qty}")
        print(f"    spread: {((ob.best_ask - ob.best_bid) / ob.best_bid * 100):.4f}%")
    
    # Step 2: WebSocket updates
    print("\n[2] WebSocket Updates (5 seconds)...")
    update_count = 0
    
    try:
        async with websockets.connect("wss://ws.backpack.exchange", ping_interval=20) as ws:
            await ws.send(orjson.dumps({
                "method": "SUBSCRIBE",
                "params": [f"depth.{SYMBOL_BP}"]
            }).decode())
            
            end_time = time.time() + 5
            
            async for msg in ws:
                if time.time() > end_time:
                    break
                    
                data = orjson.loads(msg)
                
                if 'stream' in data and 'depth' in data['stream']:
                    p = data.get('data', {})
                    bids = p.get('b', [])
                    asks = p.get('a', [])
                    
                    if bids or asks:
                        update_count += 1
                        if update_count <= 3:  # Show first 3 updates RAW
                            print(f"\n  RAW Update #{update_count}:")
                            print(f"    bids: {bids[:3]}")
                            print(f"    asks: {asks[:3]}")
                        
                        # Apply update
                        bid_tuples = [(float(a), float(b)) for a, b in bids]
                        ask_tuples = [(float(a), float(b)) for a, b in asks]
                        ob.update(bid_tuples, ask_tuples)
                        
                        if update_count <= 3:
                            print(f"    After update: best_bid={ob.best_bid} best_ask={ob.best_ask}")
    except Exception as e:
        print(f"  WebSocket error: {e}")
    
    print(f"\n  Total updates in 5s: {update_count}")
    print(f"  Final orderbook:")
    print(f"    best_bid: {ob.best_bid} qty={ob.best_bid_qty}")
    print(f"    best_ask: {ob.best_ask} qty={ob.best_ask_qty}")
    print(f"    spread: {((ob.best_ask - ob.best_bid) / ob.best_bid * 100):.4f}%")
    print(f"    bid levels: {len(ob.bids)}")
    print(f"    ask levels: {len(ob.asks)}")
    
    # Step 3: Verify against fresh REST
    print("\n[3] Verification against fresh REST...")
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get('https://api.backpack.exchange/api/v1/depth', 
                       params={'symbol': SYMBOL_BP})
        data = r.json()
        
        rest_best_bid = float(data['bids'][0][0]) if data['bids'] else 0
        rest_best_ask = float(data['asks'][0][0]) if data['asks'] else 0
        
        bid_diff = abs(ob.best_bid - rest_best_bid)
        ask_diff = abs(ob.best_ask - rest_best_ask)
        
        print(f"  WS  best_bid: {ob.best_bid}")
        print(f"  REST best_bid: {rest_best_bid}")
        print(f"  Difference: {bid_diff} ({bid_diff/rest_best_bid*100:.6f}%)")
        
        print(f"  WS  best_ask: {ob.best_ask}")
        print(f"  REST best_ask: {rest_best_ask}")
        print(f"  Difference: {ask_diff} ({ask_diff/rest_best_ask*100:.6f}%)")
        
        if bid_diff < 100 and ask_diff < 100:  # Within $100
            print("  [PASS] Orderbook is accurate!")
        else:
            print("  [WARN] Orderbook may be stale or have issues")


async def test_paradex():
    """Test Paradex orderbook with RAW data."""
    print("\n" + "=" * 60)
    print("PARADEX ORDERBOOK TEST")
    print("=" * 60)
    
    ob = FastOrderbook()
    
    # Step 1: Get REST BBO
    print("\n[1] REST BBO...")
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f'https://api.prod.paradex.trade/v1/bbo/{SYMBOL_PX}')
        bbo = r.json()
        
        print(f"  RAW BBO: {bbo}")
        rest_bid = float(bbo.get('bid', 0))
        rest_ask = float(bbo.get('ask', 0))
        print(f"  best_bid: {rest_bid}")
        print(f"  best_ask: {rest_ask}")
    
    # Step 2: WebSocket updates
    print("\n[2] WebSocket Updates (5 seconds)...")
    update_count = 0
    snapshot_received = False
    
    try:
        async with websockets.connect("wss://ws.api.prod.paradex.trade/v1", ping_interval=20) as ws:
            await ws.send(orjson.dumps({
                "jsonrpc": "2.0",
                "method": "subscribe",
                "params": {"channel": f"order_book.{SYMBOL_PX}.snapshot@15@50ms"},
                "id": 1
            }).decode())
            
            end_time = time.time() + 5
            
            async for msg in ws:
                if time.time() > end_time:
                    break
                    
                data = orjson.loads(msg)
                
                if "params" in data and data.get("method") == "subscription":
                    bd = data["params"].get("data", {})
                    update_type = bd.get("update_type", "d")
                    
                    inserts = bd.get("inserts", [])
                    updates = bd.get("updates", [])
                    deletes = bd.get("deletes", [])
                    
                    if inserts or updates or deletes:
                        update_count += 1
                        
                        if update_count <= 3:  # Show first 3 RAW
                            print(f"\n  RAW Update #{update_count} (type={update_type}):")
                            if inserts:
                                print(f"    inserts: {inserts[:3]}")
                            if updates:
                                print(f"    updates: {updates[:3]}")
                            if deletes:
                                print(f"    deletes: {deletes[:3]}")
                        
                        # Build orderbook
                        bids = []
                        asks = []
                        
                        for e in inserts:
                            price = float(e["price"])
                            size = float(e["size"])
                            if e.get("side") == "BUY":
                                bids.append((price, size))
                            else:
                                asks.append((price, size))
                        
                        for e in updates:
                            price = float(e["price"])
                            size = float(e["size"])
                            if e.get("side") == "BUY":
                                bids.append((price, size))
                            else:
                                asks.append((price, size))
                        
                        for e in deletes:
                            price = float(e["price"])
                            if e.get("side") == "BUY":
                                bids.append((price, 0))
                            else:
                                asks.append((price, 0))
                        
                        if update_type == "s" and not snapshot_received:
                            ob.set_full(bids, asks)
                            snapshot_received = True
                            print(f"    [SNAPSHOT] Loaded {len(bids)} bids, {len(asks)} asks")
                        else:
                            ob.update(bids, asks)
                        
                        if update_count <= 3:
                            print(f"    After: best_bid={ob.best_bid} best_ask={ob.best_ask}")
                            
    except Exception as e:
        print(f"  WebSocket error: {e}")
    
    print(f"\n  Total updates in 5s: {update_count}")
    print(f"  Final orderbook:")
    print(f"    best_bid: {ob.best_bid} qty={ob.best_bid_qty}")
    print(f"    best_ask: {ob.best_ask} qty={ob.best_ask_qty}")
    if ob.best_bid > 0:
        print(f"    spread: {((ob.best_ask - ob.best_bid) / ob.best_bid * 100):.4f}%")
    print(f"    bid levels: {len(ob.bids)}")
    print(f"    ask levels: {len(ob.asks)}")
    
    # Step 3: Verify
    print("\n[3] Verification against fresh REST...")
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f'https://api.prod.paradex.trade/v1/bbo/{SYMBOL_PX}')
        bbo = r.json()
        
        rest_bid = float(bbo.get('bid', 0))
        rest_ask = float(bbo.get('ask', 0))
        
        if ob.best_bid > 0:
            bid_diff = abs(ob.best_bid - rest_bid)
            ask_diff = abs(ob.best_ask - rest_ask)
            
            print(f"  WS  best_bid: {ob.best_bid}")
            print(f"  REST best_bid: {rest_bid}")
            print(f"  Difference: {bid_diff} ({bid_diff/rest_bid*100:.6f}%)")
            
            print(f"  WS  best_ask: {ob.best_ask}")
            print(f"  REST best_ask: {rest_ask}")
            print(f"  Difference: {ask_diff} ({ask_diff/rest_ask*100:.6f}%)")
            
            if bid_diff < 100 and ask_diff < 100:
                print("  [PASS] Orderbook is accurate!")
            else:
                print("  [WARN] Orderbook may be stale or have issues")
        else:
            print("  [WARN] WS orderbook empty, could not compare")


async def main():
    print("=" * 60)
    print("LIVE ORDERBOOK TEST - RAW DATA")
    print("=" * 60)
    
    await test_backpack()
    await test_paradex()
    
    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
