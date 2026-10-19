#!/usr/bin/env python3
"""
DEEP ORDERBOOK DEBUG - Shows EVERY operation in detail

ISSUES FOUND:
1. Backpack: set_full() gave best_bid=88208 but REST showed best_bid=88182
2. Paradex: bid > ask (negative spread) - IMPOSSIBLE

This test will show exactly what's happening.
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

SYMBOL_BP = "BTC_USDC_PERP"
SYMBOL_PX = "BTC-USD-PERP"


def debug_orderbook(ob: FastOrderbook, label: str):
    """Print detailed orderbook state."""
    print(f"\n  === {label} ===")
    print(f"  bid_heap size: {len(ob.bid_heap)}")
    print(f"  ask_heap size: {len(ob.ask_heap)}")
    print(f"  bids dict size: {len(ob.bids)}")
    print(f"  asks dict size: {len(ob.asks)}")
    print(f"  best_bid: {ob.best_bid} qty={ob.best_bid_qty}")
    print(f"  best_ask: {ob.best_ask} qty={ob.best_ask_qty}")
    
    if ob.best_bid > 0 and ob.best_ask > 0:
        spread = (ob.best_ask - ob.best_bid) / ob.best_bid * 100
        print(f"  spread: {spread:.4f}%")
        if spread < 0:
            print(f"  [BUG!] NEGATIVE SPREAD - bid > ask!")
    
    # Show top 10 bids and asks from dict
    if ob.bids:
        sorted_bids = sorted(ob.bids.items(), key=lambda x: -x[0])[:10]
        print(f"  Top 10 bids (from dict):")
        for i, (p, q) in enumerate(sorted_bids):
            print(f"    [{i}] price={p} qty={q}")
    if ob.asks:
        sorted_asks = sorted(ob.asks.items(), key=lambda x: x[0])[:10]
        print(f"  Top 10 asks (from dict):")
        for i, (p, q) in enumerate(sorted_asks):
            print(f"    [{i}] price={p} qty={q}")


async def test_backpack_detailed():
    """Detailed Backpack test."""
    print("\n" + "=" * 70)
    print("BACKPACK DETAILED DEBUG")
    print("=" * 70)
    
    ob = FastOrderbook()
    
    # Step 1: Get REST and analyze
    print("\n[STEP 1] REST Snapshot Analysis")
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get('https://api.backpack.exchange/api/v1/depth', 
                       params={'symbol': SYMBOL_BP})
        data = r.json()
        
        raw_bids = data.get('bids', [])[:10]
        raw_asks = data.get('asks', [])[:10]
        
        print(f"\n  RAW bids from REST (top 10):")
        for i, (p, q) in enumerate(raw_bids):
            print(f"    [{i}] price={p} qty={q}")
        
        print(f"\n  RAW asks from REST (top 10):")
        for i, (p, q) in enumerate(raw_asks):
            print(f"    [{i}] price={p} qty={q}")
        
        # Convert to tuples
        all_bids = data.get('bids', [])[:20]
        all_asks = data.get('asks', [])[:20]
        
        print(f"\n  Converting to tuples...")
        bid_tuples = [(float(p), float(q)) for p, q in all_bids]
        ask_tuples = [(float(p), float(q)) for p, q in all_asks]
        
        print(f"  Bid tuples (top 5): {bid_tuples[:5]}")
        print(f"  Ask tuples (top 5): {ask_tuples[:5]}")
        
        # Find expected best
        if bid_tuples:
            expected_best_bid = max(bid_tuples, key=lambda x: x[0])
            print(f"\n  Expected best bid (max price): {expected_best_bid}")
        if ask_tuples:
            expected_best_ask = min(ask_tuples, key=lambda x: x[0])
            print(f"  Expected best ask (min price): {expected_best_ask}")
        
        # Apply set_full
        print(f"\n  Calling ob.set_full()...")
        ob.set_full(bid_tuples, ask_tuples)
        
        debug_orderbook(ob, "After set_full")
        
        # Check if best matches
        if bid_tuples:
            if ob.best_bid != expected_best_bid[0]:
                print(f"\n  [BUG!] best_bid mismatch!")
                print(f"    Expected: {expected_best_bid[0]}")
                print(f"    Got: {ob.best_bid}")
            else:
                print(f"\n  [OK] best_bid matches expected")
    
    # Step 2: WebSocket update analysis
    print("\n" + "-" * 70)
    print("[STEP 2] WebSocket Update Analysis (30 updates)")
    
    update_count = 0
    
    try:
        async with websockets.connect("wss://ws.backpack.exchange", ping_interval=20) as ws:
            await ws.send(orjson.dumps({
                "method": "SUBSCRIBE",
                "params": [f"depth.{SYMBOL_BP}"]
            }).decode())
            
            async for msg in ws:
                if update_count >= 30:
                    break
                    
                data = orjson.loads(msg)
                
                if 'stream' in data and 'depth' in data['stream']:
                    p = data.get('data', {})
                    raw_bids = p.get('b', [])
                    raw_asks = p.get('a', [])
                    
                    if raw_bids or raw_asks:
                        update_count += 1
                        
                        print(f"\n  --- Update #{update_count} ---")
                        
                        for price, qty in raw_bids[:10]:
                            qty_f = float(qty)
                            action = "DELETE" if qty_f == 0 else ("INSERT/UPDATE" if qty_f > 0 else "???")
                            print(f"    BID {action}: price={price} qty={qty}")
                        
                        for price, qty in raw_asks[:10]:
                            qty_f = float(qty)
                            action = "DELETE" if qty_f == 0 else ("INSERT/UPDATE" if qty_f > 0 else "???")
                            print(f"    ASK {action}: price={price} qty={qty}")
                        
                        # Apply update
                        bid_tuples = [(float(a), float(b)) for a, b in raw_bids]
                        ask_tuples = [(float(a), float(b)) for a, b in raw_asks]
                        
                        old_best_bid = ob.best_bid
                        old_best_ask = ob.best_ask
                        
                        ob.update(bid_tuples, ask_tuples)
                        
                        if ob.best_bid != old_best_bid or ob.best_ask != old_best_ask:
                            print(f"    BEST CHANGED: bid {old_best_bid}->{ob.best_bid}, ask {old_best_ask}->{ob.best_ask}")
                        
                        # Check for negative spread
                        if ob.best_bid > 0 and ob.best_ask > 0 and ob.best_bid >= ob.best_ask:
                            print(f"    [BUG!] bid >= ask: {ob.best_bid} >= {ob.best_ask}")
                            
    except Exception as e:
        print(f"  WebSocket error: {e}")
    
    debug_orderbook(ob, "Final State")


async def test_paradex_detailed():
    """Detailed Paradex test."""
    print("\n" + "=" * 70)
    print("PARADEX DETAILED DEBUG")
    print("=" * 70)
    
    ob = FastOrderbook()
    
    # Step 1: REST BBO
    print("\n[STEP 1] REST BBO")
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f'https://api.prod.paradex.trade/v1/bbo/{SYMBOL_PX}')
        bbo = r.json()
        print(f"  RAW: {bbo}")
        print(f"  Expected: bid={bbo.get('bid')} ask={bbo.get('ask')}")
    
    # Step 2: WebSocket analysis
    print("\n" + "-" * 70)
    print("[STEP 2] WebSocket Update Analysis (30 updates)")
    
    update_count = 0
    
    try:
        async with websockets.connect("wss://ws.api.prod.paradex.trade/v1", ping_interval=20) as ws:
            await ws.send(orjson.dumps({
                "jsonrpc": "2.0",
                "method": "subscribe",
                "params": {"channel": f"order_book.{SYMBOL_PX}.snapshot@15@50ms"},
                "id": 1
            }).decode())
            
            async for msg in ws:
                if update_count >= 30:
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
                        
                        type_label = "SNAPSHOT" if update_type == "s" else "DELTA"
                        print(f"\n  --- Update #{update_count} [{type_label}] ---")
                        
                        # Show inserts
                        for e in inserts[:10]:
                            side = e.get("side")
                            price = e.get("price")
                            size = e.get("size")
                            print(f"    INSERT {side}: price={price} size={size}")
                        
                        # Show updates
                        for e in updates[:10]:
                            side = e.get("side")
                            price = e.get("price")
                            size = e.get("size")
                            print(f"    UPDATE {side}: price={price} size={size}")
                        
                        # Show deletes
                        for e in deletes[:10]:
                            side = e.get("side")
                            price = e.get("price")
                            print(f"    DELETE {side}: price={price}")
                        
                        # Build updates
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
                                bids.append((price, 0))  # DELETE = qty 0
                            else:
                                asks.append((price, 0))
                        
                        old_best_bid = ob.best_bid
                        old_best_ask = ob.best_ask
                        
                        # Apply
                        if update_type == "s":
                            # PROBLEM: Paradex snapshots should REPLACE, not add!
                            # But we're calling set_full only on first snapshot
                            # Subsequent "snapshots" are being treated as updates
                            print(f"    Applying as SNAPSHOT (set_full)")
                            ob.set_full(bids, asks)
                        else:
                            print(f"    Applying as DELTA (update)")
                            ob.update(bids, asks)
                        
                        print(f"    Result: bid={ob.best_bid} ask={ob.best_ask}")
                        
                        # Check for crossed book
                        if ob.best_bid > 0 and ob.best_ask > 0:
                            spread = (ob.best_ask - ob.best_bid)
                            if spread < 0:
                                print(f"    [BUG!] CROSSED BOOK! bid={ob.best_bid} > ask={ob.best_ask}")
                                print(f"    This means we're not handling deletes correctly!")
                            
    except Exception as e:
        print(f"  WebSocket error: {e}")
    
    debug_orderbook(ob, "Final State")
    
    # Additional analysis
    print("\n  === ANALYSIS ===")
    if ob.best_bid > 0 and ob.best_ask > 0:
        if ob.best_bid >= ob.best_ask:
            print("  [ROOT CAUSE] Paradex sends 'snapshot' type on EVERY message")
            print("  We should call set_full() on EACH snapshot, not just first")
            print("  This clears old stale bids/asks and prevents crossed book")


async def main():
    print("=" * 70)
    print("DEEP ORDERBOOK DEBUG")
    print("=" * 70)
    
    await test_backpack_detailed()
    await test_paradex_detailed()
    
    print("\n" + "=" * 70)
    print("DEBUG COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
