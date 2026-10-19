#!/usr/bin/env python3
"""
Test orderbook operations: INSERT, UPDATE, DELETE
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')

from bot.orderbook import FastOrderbook
import time

print("=" * 60)
print("ORDERBOOK OPERATIONS TEST")
print("=" * 60)

ob = FastOrderbook()

# ============================================================================
# TEST 1: INSERT (set_full)
# ============================================================================
print("\n[TEST 1] INSERT via set_full...")
bids = [(100.0, 10.0), (99.5, 20.0), (99.0, 15.0)]
asks = [(100.5, 5.0), (101.0, 8.0), (101.5, 12.0)]
ob.set_full(bids, asks)

print(f"  Bid levels: {len(ob.bids)}")
print(f"  Ask levels: {len(ob.asks)}")
print(f"  Best bid: {ob.best_bid} qty={ob.best_bid_qty}")
print(f"  Best ask: {ob.best_ask} qty={ob.best_ask_qty}")

assert len(ob.bids) == 3, f"Expected 3 bids, got {len(ob.bids)}"
assert len(ob.asks) == 3, f"Expected 3 asks, got {len(ob.asks)}"
assert ob.best_bid == 100.0
assert ob.best_ask == 100.5
print("  [PASS] INSERT works!")

# ============================================================================
# TEST 2: UPDATE (change quantity at existing price)
# ============================================================================
print("\n[TEST 2] UPDATE (change qty at price 100.0)...")
print(f"  Before: price=100.0, qty={ob.bids[100.0]}")

ob.update([(100.0, 50.0)], [])  # Change qty from 10 to 50

print(f"  After:  price=100.0, qty={ob.bids[100.0]}")
assert ob.bids[100.0] == 50.0, f"Expected qty=50, got {ob.bids[100.0]}"
assert ob.best_bid_qty == 50.0, f"best_bid_qty should be 50, got {ob.best_bid_qty}"
print("  [PASS] UPDATE works!")

# ============================================================================
# TEST 3: INSERT NEW LEVEL via update
# ============================================================================
print("\n[TEST 3] INSERT NEW LEVEL (add price 100.25)...")
print(f"  Bid levels before: {len(ob.bids)}")

ob.update([(100.25, 30.0)], [])  # Add new level

print(f"  Bid levels after: {len(ob.bids)}")
print(f"  New best bid: {ob.best_bid} qty={ob.best_bid_qty}")

assert len(ob.bids) == 4, f"Expected 4 bids, got {len(ob.bids)}"
assert ob.best_bid == 100.25, f"Best bid should be 100.25, got {ob.best_bid}"
assert 100.25 in ob.bids
print("  [PASS] INSERT NEW LEVEL works!")

# ============================================================================
# TEST 4: DELETE (qty=0 removes level)
# ============================================================================
print("\n[TEST 4] DELETE (remove price 100.25 with qty=0)...")
print(f"  Bid levels before: {len(ob.bids)}")

ob.update([(100.25, 0)], [])  # Delete level

print(f"  Bid levels after: {len(ob.bids)}")
print(f"  Best bid after delete: {ob.best_bid}")

assert len(ob.bids) == 3, f"Expected 3 bids after delete, got {len(ob.bids)}"
assert 100.25 not in ob.bids, "100.25 should be deleted"
assert ob.best_bid == 100.0, f"Best bid should be back to 100.0, got {ob.best_bid}"
print("  [PASS] DELETE works!")

# ============================================================================
# TEST 5: DELETE BEST BID
# ============================================================================
print("\n[TEST 5] DELETE BEST BID (remove 100.0)...")
print(f"  Best bid before: {ob.best_bid}")

ob.update([(100.0, 0)], [])

print(f"  Best bid after: {ob.best_bid}")

assert ob.best_bid == 99.5, f"Next best bid should be 99.5, got {ob.best_bid}"
assert 100.0 not in ob.bids
print("  [PASS] DELETE BEST BID works!")

# ============================================================================
# TEST 6: PERFORMANCE
# ============================================================================
print("\n[TEST 6] PERFORMANCE...")

# Reset orderbook
ob = FastOrderbook()

# Simulate 1000 levels
levels = [(float(i), float(i*10)) for i in range(1000, 2000)]
ob.set_full(levels[:500], levels[500:])

# Time 10000 updates
t0 = time.perf_counter()
for i in range(10000):
    ob.update([(1500.0, float(i))], [])
t1 = time.perf_counter()

total_time = (t1 - t0) * 1000  # ms
per_update = total_time / 10000 * 1000  # microseconds

print(f"  10,000 updates in {total_time:.2f}ms")
print(f"  Per update: {per_update:.2f} microseconds")
print("  [PASS] Performance test complete!")

# ============================================================================
# SUMMARY
# ============================================================================
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
print(f"""
OPERACOES TESTADAS:
  [1] INSERT (set_full)     - OK
  [2] UPDATE (qty change)   - OK
  [3] INSERT NEW LEVEL      - OK
  [4] DELETE (qty=0)        - OK
  [5] DELETE BEST BID       - OK
  [6] PERFORMANCE           - {per_update:.2f}us por update

CONCLUSAO:
  Orderbook esta funcionando corretamente!
  Todas as operacoes INSERT/UPDATE/DELETE funcionam.
""")
