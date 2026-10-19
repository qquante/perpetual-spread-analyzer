#!/usr/bin/env python3
"""
PARADEX ISOLATED TEST - Raw WebSocket data analysis
"""

import asyncio
import sys
import io
import time
import websockets
import orjson

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')

SYMBOL = "BTC-USD-PERP"


async def test_snapshot_channel():
    """Test the snapshot channel."""
    print("\n" + "=" * 70)
    print("TEST 1: SNAPSHOT CHANNEL")
    print("=" * 70)
    
    async with websockets.connect("wss://ws.api.prod.paradex.trade/v1", ping_interval=20) as ws:
        await ws.send(orjson.dumps({
            "jsonrpc": "2.0",
            "method": "subscribe",
            "params": {"channel": f"order_book.{SYMBOL}.snapshot@15@50ms"},
            "id": 1
        }).decode())
        
        count = 0
        start = time.time()
        
        async for msg in ws:
            if count >= 5 or time.time() - start > 10:
                break
                
            data = orjson.loads(msg)
            
            if "params" in data and data.get("method") == "subscription":
                count += 1
                bd = data["params"].get("data", {})
                
                update_type = bd.get("update_type", "?")
                inserts = bd.get("inserts", [])
                updates = bd.get("updates", [])
                deletes = bd.get("deletes", [])
                
                buy_inserts = [i for i in inserts if i.get("side") == "BUY"]
                sell_inserts = [i for i in inserts if i.get("side") == "SELL"]
                
                print(f"\n--- Message #{count} ---")
                print(f"  update_type: {update_type} ({'SNAPSHOT' if update_type == 's' else 'DELTA'})")
                print(f"  inserts: {len(inserts)} (BUY: {len(buy_inserts)}, SELL: {len(sell_inserts)})")
                print(f"  updates: {len(updates)}")
                print(f"  deletes: {len(deletes)}")
    
    print(f"\n  Done! Received {count} messages in {time.time()-start:.1f}s")


async def test_orderbook_construction():
    """Test proper orderbook construction."""
    print("\n" + "=" * 70)
    print("TEST 2: ORDERBOOK CONSTRUCTION")
    print("=" * 70)
    
    from bot.orderbook import FastOrderbook
    
    ob = FastOrderbook()
    
    async with websockets.connect("wss://ws.api.prod.paradex.trade/v1", ping_interval=20) as ws:
        await ws.send(orjson.dumps({
            "jsonrpc": "2.0",
            "method": "subscribe",
            "params": {"channel": f"order_book.{SYMBOL}.snapshot@15@50ms"},
            "id": 1
        }).decode())
        
        count = 0
        start = time.time()
        errors = 0
        
        async for msg in ws:
            if count >= 20 or time.time() - start > 15:
                break
                
            data = orjson.loads(msg)
            
            if "params" in data and data.get("method") == "subscription":
                count += 1
                bd = data["params"].get("data", {})
                
                inserts = bd.get("inserts", [])
                
                bids = []
                asks = []
                
                for e in inserts:
                    price = float(e["price"])
                    size = float(e["size"])
                    if e.get("side") == "BUY":
                        bids.append((price, size))
                    else:
                        asks.append((price, size))
                
                # ALWAYS use set_full for snapshot channel!
                ob.set_full(bids, asks)
                
                if ob.best_bid > 0 and ob.best_ask > 0:
                    spread = (ob.best_ask - ob.best_bid) / ob.best_bid * 100
                    status = "OK" if spread > 0 else "BUG!"
                    if spread < 0:
                        errors += 1
                    print(f"  #{count}: bid={ob.best_bid:.1f} ask={ob.best_ask:.1f} spread={spread:.4f}% [{status}]")
                else:
                    print(f"  #{count}: bid={ob.best_bid} ask={ob.best_ask} [INCOMPLETE]")
    
    print(f"\n  Done! {count} messages, {errors} errors")


async def main():
    print("=" * 70)
    print("PARADEX ISOLATED TEST")
    print("=" * 70)
    
    try:
        await asyncio.wait_for(test_snapshot_channel(), timeout=15)
    except asyncio.TimeoutError:
        print("  TIMEOUT!")
    
    try:
        await asyncio.wait_for(test_orderbook_construction(), timeout=20)
    except asyncio.TimeoutError:
        print("  TIMEOUT!")
    
    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
