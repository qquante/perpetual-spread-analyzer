"""
Debug test: Try to place an order on Backpack for PUMP to see exact error.
"""
import os
import time
import base64
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

API_KEY = os.getenv("BACKPACK_API_KEY", "")
API_SECRET = os.getenv("BACKPACK_API_SECRET", "")

if not API_KEY or not API_SECRET:
    print("[ERROR] Set BACKPACK_API_KEY and BACKPACK_API_SECRET")
    exit(1)


def sign_request(instruction: str, params: dict = None) -> dict:
    timestamp = int(time.time() * 1000)
    window = 5000
    
    sorted_params = []
    if params:
        for k in sorted(params.keys()):
            v = params[k]
            if isinstance(v, bool):
                v = str(v).lower()
            sorted_params.append(f"{k}={v}")
    
    parts = [f"instruction={instruction}"]
    parts.extend(sorted_params)
    parts.append(f"timestamp={timestamp}")
    parts.append(f"window={window}")
    
    signing_string = "&".join(parts)
    
    private_key_bytes = base64.b64decode(API_SECRET)
    private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    signature = private_key.sign(signing_string.encode())
    signature_b64 = base64.b64encode(signature).decode()
    
    return {
        "X-API-Key": API_KEY,
        "X-Signature": signature_b64,
        "X-Timestamp": str(timestamp),
        "X-Window": str(window),
        "Content-Type": "application/json; charset=utf-8",
    }


def test_order(symbol: str, side: str, qty: str, price: str):
    """Test placing an order with specific params."""
    
    order_params = {
        "symbol": symbol,
        "side": side,
        "orderType": "Limit",
        "quantity": qty,
        "price": price,
        "timeInForce": "IOC",
    }
    
    print(f"\n{'='*60}")
    print(f"Testing order: {symbol} {side}")
    print(f"  quantity: {qty}")
    print(f"  price: {price}")
    print(f"  Full params: {order_params}")
    print(f"{'='*60}")
    
    headers = sign_request("orderExecute", order_params)
    
    r = httpx.post(
        "https://api.backpack.exchange/api/v1/order",
        headers=headers,
        json=order_params,
        timeout=10,
    )
    
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text}")
    
    return r.status_code, r.json() if r.status_code == 200 else r.text


if __name__ == "__main__":
    print("BACKPACK ORDER DEBUG TEST")
    print("=" * 60)
    
    # Get market info for PUMP
    print("\n1. Getting PUMP market info...")
    r = httpx.get("https://api.backpack.exchange/api/v1/markets")
    markets = r.json()
    pump = next((m for m in markets if m["symbol"] == "PUMP_USDC_PERP"), None)
    
    if pump:
        print(f"   Symbol: {pump['symbol']}")
        filters = pump.get("filters", {})
        price_f = filters.get("price", {})
        qty_f = filters.get("quantity", {})
        print(f"   Price filter: {price_f}")
        print(f"   Qty filter: {qty_f}")
    
    # Get current price
    print("\n2. Getting current PUMP price...")
    r = httpx.get("https://api.backpack.exchange/api/v1/ticker", 
                  params={"symbol": "PUMP_USDC_PERP"})
    ticker = r.json()
    last_price = float(ticker.get("lastPrice", 0))
    print(f"   Last price: {last_price}")
    
    # Try different price formats
    print("\n3. Testing different order formats...")
    
    # Test 1: Raw price as float
    test_price = last_price * 0.9  # 10% below market
    print(f"\nTest 1: Raw price = {test_price}")
    test_order("PUMP_USDC_PERP", "Bid", "100", str(test_price))
    
    # Test 2: Rounded to 6 decimals
    test_price_6 = round(test_price, 6)
    print(f"\nTest 2: Rounded 6 decimals = {test_price_6}")
    test_order("PUMP_USDC_PERP", "Bid", "100", str(test_price_6))
    
    # Test 3: Rounded to tick size (0.000001)
    tick_size = 0.000001
    test_price_tick = round(test_price / tick_size) * tick_size
    print(f"\nTest 3: Rounded to tick size = {test_price_tick:.6f}")
    test_order("PUMP_USDC_PERP", "Bid", "100", f"{test_price_tick:.6f}")
    
    # Test 4: Using exact format from market
    print(f"\nTest 4: Using f-string with 6 decimals")
    test_order("PUMP_USDC_PERP", "Bid", "100", f"{test_price:.6f}")
    
    print("\n" + "=" * 60)
    print("DEBUG COMPLETE")
