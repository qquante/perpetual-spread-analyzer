"""
Test Backpack Exchange API - Auth and Trading.
Uses ED25519 API keys.

Env vars:
    BACKPACK_API_KEY=base64...
    BACKPACK_API_SECRET=base64...
"""

import os
import time
import base64
import json
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

API_KEY = os.getenv("BACKPACK_API_KEY", "")
API_SECRET = os.getenv("BACKPACK_API_SECRET", "")

if not API_KEY or not API_SECRET:
    print("[ERROR] Set BACKPACK_API_KEY and BACKPACK_API_SECRET")
    exit(1)

print(f"[AUTH] API Key: {API_KEY[:20]}...")


def sign_request(instruction: str, params: dict = None) -> dict:
    """Sign a Backpack API request using ED25519."""
    timestamp = int(time.time() * 1000)
    window = 5000
    
    # Build sorted params
    sorted_params = []
    if params:
        for k in sorted(params.keys()):
            v = params[k]
            if isinstance(v, bool):
                v = str(v).lower()
            sorted_params.append(f"{k}={v}")
    
    # Build signing string: instruction=X&param1=val1&...&timestamp=T&window=W
    parts = [f"instruction={instruction}"]
    parts.extend(sorted_params)
    parts.append(f"timestamp={timestamp}")
    parts.append(f"window={window}")
    
    signing_string = "&".join(parts)
    
    # Load private key from base64
    try:
        private_key_bytes = base64.b64decode(API_SECRET)
        private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    except Exception as e:
        print(f"[ERROR] Failed to load private key: {e}")
        exit(1)
    
    # Sign
    signature = private_key.sign(signing_string.encode())
    signature_b64 = base64.b64encode(signature).decode()
    
    return {
        "X-API-Key": API_KEY,
        "X-Signature": signature_b64,
        "X-Timestamp": str(timestamp),
        "X-Window": str(window),
        "Content-Type": "application/json; charset=utf-8",
    }


def test_public_endpoints():
    """Test public endpoints (no auth needed)."""
    print("\n[PUBLIC] Testing public endpoints...")
    
    # Markets
    print("  [TEST] /api/v1/markets...")
    r = httpx.get("https://api.backpack.exchange/api/v1/markets")
    markets = r.json()
    perps = [m for m in markets if "PERP" in m.get("symbol", "")]
    print(f"    Markets: {len(markets)} total, {len(perps)} perps")
    
    # Ticker
    print("  [TEST] /api/v1/ticker (BTC_USDC_PERP)...")
    r = httpx.get("https://api.backpack.exchange/api/v1/ticker", params={"symbol": "BTC_USDC_PERP"})
    ticker = r.json()
    print(f"    Last: {ticker.get('lastPrice')} | Volume: {ticker.get('volume')}")
    
    # Depth
    print("  [TEST] /api/v1/depth (BTC_USDC_PERP)...")
    r = httpx.get("https://api.backpack.exchange/api/v1/depth", params={"symbol": "BTC_USDC_PERP"})
    depth = r.json()
    print(f"    Bids: {len(depth.get('bids', []))} | Asks: {len(depth.get('asks', []))}")
    if depth.get("bids") and depth.get("asks"):
        print(f"    BBO: {depth['bids'][0][0]} / {depth['asks'][0][0]}")
    
    print("  [OK] Public endpoints working")


def test_private_endpoints():
    """Test private endpoints (auth required)."""
    print("\n[PRIVATE] Testing authenticated endpoints...")
    
    # Account balance
    print("  [TEST] /api/v1/capital...")
    headers = sign_request("balanceQuery")
    r = httpx.get("https://api.backpack.exchange/api/v1/capital", headers=headers)
    
    if r.status_code != 200:
        print(f"    [ERROR] Status {r.status_code}: {r.text}")
        return False
    
    balances = r.json()
    if isinstance(balances, dict):
        print(f"    Balances: {len(balances)} assets")
        for symbol, data in list(balances.items())[:5]:
            avail = data.get("available", 0) if isinstance(data, dict) else 0
            if float(avail) > 0:
                print(f"      {symbol}: {avail} available")
    
    # Open orders
    print("  [TEST] /api/v1/orders (open)...")
    headers = sign_request("orderQueryAll")
    r = httpx.get("https://api.backpack.exchange/api/v1/orders", headers=headers)
    
    if r.status_code != 200:
        print(f"    [ERROR] Status {r.status_code}: {r.text}")
        return False
    
    orders = r.json()
    print(f"    Open orders: {len(orders)}")
    
    print("  [OK] Private endpoints working")
    return True


def test_order_placement():
    """Test order placement (POST_ONLY far from market)."""
    print("\n[ORDER] Testing order placement...")
    
    # Get current price
    r = httpx.get("https://api.backpack.exchange/api/v1/ticker", params={"symbol": "BTC_USDC_PERP"})
    ticker = r.json()
    current_price = float(ticker.get("lastPrice", 0))
    print(f"  Current BTC price: ${current_price:,.2f}")
    
    # Place order 10% below market (won't execute but within limits)
    test_price = str(round(current_price * 0.9, 1))
    test_qty = "0.0001"
    
    print(f"  Placing POST_ONLY BUY order...")
    print(f"    Symbol: BTC_USDC_PERP")
    print(f"    Size: {test_qty}")
    print(f"    Price: ${test_price} (10% below market)")
    
    order_params = {
        "symbol": "BTC_USDC_PERP",
        "side": "Bid",
        "orderType": "Limit",
        "quantity": test_qty,
        "price": test_price,
        "postOnly": True,
    }
    
    headers = sign_request("orderExecute", order_params)
    r = httpx.post(
        "https://api.backpack.exchange/api/v1/order",
        headers=headers,
        json=order_params,
    )
    
    print(f"    Response: {r.status_code}")
    
    if r.status_code not in [200, 201]:
        print(f"    [ERROR] {r.text[:200]}")
        return False
    
    result = r.json()
    order_id = result.get("id", "")
    status = result.get("status", "")
    print(f"    [OK] Order ID: {order_id}")
    print(f"    [OK] Status: {status}")
    
    # Cancel immediately
    if order_id:
        print(f"  Cancelling order {order_id}...")
        cancel_params = {"symbol": "BTC_USDC_PERP", "orderId": order_id}
        headers = sign_request("orderCancel", cancel_params)
        r = httpx.request(
            "DELETE",
            "https://api.backpack.exchange/api/v1/order",
            headers=headers,
            json=cancel_params,
        )
        if r.status_code == 200:
            print(f"    [OK] Order cancelled")
        else:
            print(f"    [WARN] Cancel: {r.status_code} {r.text[:100]}")
    
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("BACKPACK EXCHANGE TEST")
    print("=" * 60)
    
    test_public_endpoints()
    
    if test_private_endpoints():
        test_order_placement()
    
    print("\n[DONE] Test complete!")
