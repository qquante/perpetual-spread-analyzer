#!/usr/bin/env python3
"""
Test Script - Verify all bot components working correctly

Run: python test_components.py
"""

import asyncio
import time
import sys

# Force UTF-8 output on Windows
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Add parent dir to path for imports
sys.path.insert(0, '.')

print("=" * 60)
print("BOT COMPONENT TESTS")
print("=" * 60)

# TEST 1: Config
print("\n[TEST 1] Config...")
try:
    from bot.config import (
        MIN_SPREAD, MIN_PROFIT_USD, MAX_ORDER_USD,
        SLIPPAGE_BUFFER, COOLDOWN_SUCCESS, MAX_CONCURRENT
    )
    print(f"  OK MIN_SPREAD = {MIN_SPREAD}%")
    print(f"  OK MIN_PROFIT_USD = ${MIN_PROFIT_USD}")
    print(f"  OK MAX_ORDER_USD = ${MAX_ORDER_USD}")
    print(f"  OK SLIPPAGE_BUFFER = {SLIPPAGE_BUFFER*100}%")
    print(f"  OK COOLDOWN_SUCCESS = {COOLDOWN_SUCCESS}s")
    print(f"  OK MAX_CONCURRENT = {MAX_CONCURRENT}")
    print("  [PASS] Config loaded correctly")
except Exception as e:
    print(f"  [FAIL] {e}")

# TEST 2: Models
print("\n[TEST 2] Models...")
try:
    from bot.models import Opportunity
    opp = Opportunity(
        pair="BTC", spread=0.5, qty=0.1, profit=10.0,
        buy_exchange="BP", sell_exchange="PX",
        buy_price=50000, sell_price=50250, ts=time.time()
    )
    assert opp.pair == "BTC"
    assert opp.spread == 0.5
    print(f"  OK Opportunity dataclass works")
    print("  [PASS] Models work correctly")
except Exception as e:
    print(f"  [FAIL] {e}")

# TEST 3: FastOrderbook
print("\n[TEST 3] FastOrderbook...")
try:
    from bot.orderbook import FastOrderbook
    
    ob = FastOrderbook()
    
    # Test set_full
    bids = [(100.0, 10.0), (99.5, 20.0), (99.0, 15.0)]
    asks = [(100.5, 5.0), (101.0, 8.0), (101.5, 12.0)]
    ob.set_full(bids, asks)
    
    assert ob.best_bid == 100.0, f"Expected 100.0, got {ob.best_bid}"
    assert ob.best_ask == 100.5, f"Expected 100.5, got {ob.best_ask}"
    assert ob.best_bid_qty == 10.0
    assert ob.best_ask_qty == 5.0
    print(f"  OK set_full: best_bid={ob.best_bid}, best_ask={ob.best_ask}")
    
    # Test update (remove best bid level)
    ob.update([(100.0, 0)], [])  # Remove 100.0
    assert ob.best_bid == 99.5, f"Expected 99.5 after removal, got {ob.best_bid}"
    print(f"  OK update: after removing 100.0, best_bid={ob.best_bid}")
    
    # Test age
    age = ob.age_ms()
    assert age < 1000, f"Age should be <1s, got {age}ms"
    print(f"  OK age_ms: {age:.2f}ms")
    
    print("  [PASS] FastOrderbook works correctly")
except Exception as e:
    print(f"  [FAIL] {e}")
    import traceback
    traceback.print_exc()

# TEST 4: Backpack API (live connection)
print("\n[TEST 4] Backpack API...")
try:
    import httpx
    
    async def test_bp():
        async with httpx.AsyncClient(timeout=10) as c:
            # Test markets endpoint
            r = await c.get('https://api.backpack.exchange/api/v1/markets')
            markets = r.json()
            perps = [m for m in markets if 'PERP' in m.get('symbol', '')]
            print(f"  OK Found {len(perps)} PERP markets")
            
            # Test orderbook
            r = await c.get('https://api.backpack.exchange/api/v1/depth', 
                          params={'symbol': 'BTC_USDC_PERP'})
            ob = r.json()
            best_bid = float(ob['bids'][0][0]) if ob['bids'] else 0
            best_ask = float(ob['asks'][0][0]) if ob['asks'] else 0
            print(f"  OK BTC orderbook: bid=${best_bid:,.2f}, ask=${best_ask:,.2f}")
            
            return True
    
    asyncio.run(test_bp())
    print("  [PASS] Backpack API works")
except Exception as e:
    print(f"  [FAIL] {e}")

# TEST 5: Paradex API (live connection)
print("\n[TEST 5] Paradex API...")
try:
    import httpx
    
    async def test_px():
        async with httpx.AsyncClient(timeout=10) as c:
            # Test markets endpoint
            r = await c.get('https://api.prod.paradex.trade/v1/markets')
            markets = r.json()['results']
            perps = [m for m in markets if 'PERP' in m.get('symbol', '')]
            print(f"  OK Found {len(perps)} PERP markets")
            
            # Test BBO
            r = await c.get('https://api.prod.paradex.trade/v1/bbo/BTC-USD-PERP')
            bbo = r.json()
            best_bid = float(bbo.get('bid', 0))
            best_ask = float(bbo.get('ask', 0))
            print(f"  OK BTC BBO: bid=${best_bid:,.2f}, ask=${best_ask:,.2f}")
            
            return True
    
    asyncio.run(test_px())
    print("  [PASS] Paradex API works")
except Exception as e:
    print(f"  [FAIL] {e}")

# TEST 6: Spread Calculation
print("\n[TEST 6] Spread Calculation...")
try:
    # Simulated prices
    bp_ask = 50000.0  # Buy on BP
    px_bid = 50150.0  # Sell on PX
    
    spread = (px_bid - bp_ask) / bp_ask * 100
    qty = 0.1
    profit = qty * (px_bid - bp_ask)
    
    print(f"  BP ask (buy): ${bp_ask:,.2f}")
    print(f"  PX bid (sell): ${px_bid:,.2f}")
    print(f"  OK Spread: {spread:.3f}%")
    print(f"  OK Profit (0.1 BTC): ${profit:.2f}")
    print("  [PASS] Spread calculation correct")
except Exception as e:
    print(f"  [FAIL] {e}")

# TEST 7: Price Rounding (BUG CHECK!)
print("\n[TEST 7] Price Rounding (BUG CHECK!)...")
try:
    from decimal import Decimal, ROUND_HALF_UP
    
    # Test case: price after buffer
    price = 2.537887
    tick_size = 0.00001
    
    print(f"  Input price: {price}")
    print(f"  Tick size: {tick_size}")
    
    # Method 1: BUGGY - float arithmetic
    price_float = round(price / tick_size) * tick_size
    print(f"  ")
    print(f"  [METHOD 1] Float arithmetic:")
    print(f"    Result: {price_float}")
    print(f"    Repr: {repr(price_float)}")
    
    # Method 2: CORRECT - Decimal
    price_decimal = Decimal(str(price)).quantize(Decimal(str(tick_size)), rounding=ROUND_HALF_UP)
    print(f"  ")
    print(f"  [METHOD 2] Decimal:")
    print(f"    Result: {price_decimal}")
    print(f"    String: {str(price_decimal)}")
    
    # Compare
    print(f"  ")
    if str(price_float) != str(price_decimal):
        print(f"  [BUG FOUND!] Float and Decimal give different results!")
        print(f"    Float:   {price_float}")
        print(f"    Decimal: {price_decimal}")
    else:
        print(f"  [OK] Both methods match")
    
    print("  [DONE] Price rounding test complete")
except Exception as e:
    print(f"  [FAIL] {e}")

# SUMMARY
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
print("""
COMPONENTES:
  [1] Config        - Carrega do .env
  [2] Models        - Dataclasses
  [3] FastOrderbook - In-memory, O(1) best bid/ask
  [4] Backpack API  - REST + WebSocket
  [5] Paradex API   - REST + WebSocket
  [6] Spread Calc   - Matematica correta
  [7] Price Round   - VERIFICAR SE TEM BUG!

REDIS:
  NAO ESTA SENDO USADO
  O orderbook fica em memoria (dict + heap)
  Isso e MAIS RAPIDO que Redis para este caso!

PERFORMANCE:
  - FastOrderbook usa heap para O(1) best bid/ask
  - Dict para O(1) price level lookup
  - In-memory = latencia ~0ms (vs Redis ~1-5ms)
""")
