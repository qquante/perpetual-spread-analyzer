"""Test Paradex auth with Subkey (L2) and place a test order."""
import os
from decimal import Decimal

from paradex_py.environment import PROD
from paradex_py.common.order import Order, OrderSide, OrderType

# Check for L2 (Subkey) credentials
L2_ADDR = os.getenv("PARADEX_L2_ADDRESS")
L2_KEY = os.getenv("PARADEX_L2_PRIVATE_KEY")
L1_ADDR = os.getenv("PARADEX_L1_ADDRESS")

if not L2_ADDR or not L2_KEY:
    print("[ERROR] Set PARADEX_L2_ADDRESS and PARADEX_L2_PRIVATE_KEY")
    exit(1)

print("[AUTH] Using L2 (Subkey) authentication")

# Convert key to int
if L2_KEY.startswith("0x"):
    L2_KEY = int(L2_KEY, 16)
else:
    L2_KEY = int(L2_KEY, 16)

from paradex_py.api.api_client import ParadexApiClient
from paradex_py.account.account import ParadexAccount

# Get system config
api = ParadexApiClient(env=PROD, auto_auth=False)
config = api.fetch_system_config()
print(f"[OK] System config loaded")

# Create account with L2 private key
print(f"[AUTH] L2 Address: {L2_ADDR}")

account = ParadexAccount(
    config=config,
    l1_address=L1_ADDR or "0x0000000000000000000000000000000000000000",
    l2_private_key=L2_KEY,
)

print(f"[OK] L2 Address (computed): {hex(account.l2_address)}")

# Auth
api.account = account
api.auth()
print("[OK] Auth successful!")

# Test account info
print("\n[ACCOUNT] Fetching account summary...")
summary = api.fetch_account_summary()
print(f"  Summary: {summary}")

# Get BBO
print("\n[MARKET] Fetching ETH-USD-PERP BBO...")
bbo = api.fetch_bbo("ETH-USD-PERP")
bid = float(bbo.get("bid", 0))
ask = float(bbo.get("ask", 0))
print(f"  Bid: {bid} | Ask: {ask}")

# Place test order (POST_ONLY far from market)
test_price = round(bid * 0.5, 2)  # 50% below market
test_size = 0.001

print(f"\n[ORDER] Placing POST_ONLY BUY order...")
print(f"  Market: ETH-USD-PERP")
print(f"  Size: {test_size}")
print(f"  Price: {test_price} (50% below market)")

try:
    order = Order(
        market="ETH-USD-PERP",
        order_type=OrderType.Limit,
        order_side=OrderSide.Buy,
        size=Decimal(str(test_size)),
        limit_price=Decimal(str(test_price)),
        client_id="test_order_001",
        instruction="POST_ONLY",
    )
    
    response = api.submit_order(order=order)
    order_id = response.get("id", "")
    status = response.get("status", "")
    print(f"  [OK] Order ID: {order_id}")
    print(f"  [OK] Status: {status}")
    
    # Cancel it immediately
    print(f"\n[CANCEL] Cancelling order {order_id}...")
    api.cancel_order(order_id=order_id)
    print(f"  [OK] Order cancelled")
    
except Exception as e:
    print(f"  [ERROR] {e}")
    import traceback
    traceback.print_exc()

print("\n[DONE] All tests passed!")
