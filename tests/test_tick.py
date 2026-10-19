"""
Test script to understand tick sizes and min prices for each exchange.
This helps us understand the correct way to format orders.
"""
import httpx
import asyncio

async def test_backpack_markets():
    """Get Backpack market specs."""
    print("=" * 70)
    print("BACKPACK MARKET SPECS")
    print("=" * 70)
    
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get("https://api.backpack.exchange/api/v1/markets")
        markets = r.json()
    
    perps = [m for m in markets if "PERP" in m.get("symbol", "")]
    
    print(f"Total PERP markets: {len(perps)}\n")
    
    # Show sample markets with low prices
    print("Sample markets (sorted by price):\n")
    print(f"{'Symbol':<20} {'Last Price':<12} {'Min Price':<12} {'Tick Size':<12} {'Min Qty':<12}")
    print("-" * 70)
    
    # Get tickers for prices
    r2 = await httpx.AsyncClient(timeout=30).get("https://api.backpack.exchange/api/v1/tickers")
    tickers = {t["symbol"]: t for t in r2.json()}
    
    samples = []
    for m in perps:
        sym = m.get("symbol", "")
        tick = tickers.get(sym, {})
        last = float(tick.get("lastPrice", 0) or 0)
        
        filters = m.get("filters", {})
        price_filter = filters.get("price", {})
        qty_filter = filters.get("quantity", {})
        
        samples.append({
            "symbol": sym,
            "lastPrice": last,
            "minPrice": price_filter.get("minPrice", "N/A"),
            "tickSize": price_filter.get("tickSize", "N/A"),
            "minQty": qty_filter.get("minQuantity", "N/A"),
        })
    
    # Sort by price and show lowest
    samples.sort(key=lambda x: x["lastPrice"])
    
    for s in samples[:15]:  # Show 15 lowest priced
        print(f"{s['symbol']:<20} ${s['lastPrice']:<11.6f} {s['minPrice']:<12} {s['tickSize']:<12} {s['minQty']:<12}")
    
    print("\n... and highest priced:\n")
    for s in samples[-5:]:  # Show 5 highest
        print(f"{s['symbol']:<20} ${s['lastPrice']:<11.2f} {s['minPrice']:<12} {s['tickSize']:<12} {s['minQty']:<12}")
    
    return samples


async def test_paradex_markets():
    """Get Paradex market specs."""
    print("\n" + "=" * 70)
    print("PARADEX MARKET SPECS")
    print("=" * 70)
    
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get("https://api.prod.paradex.trade/v1/markets")
        data = r.json()
        markets = data.get("results", [])
    
    perps = [m for m in markets if "PERP" in m.get("symbol", "")]
    
    print(f"Total PERP markets: {len(perps)}\n")
    
    print("Sample markets:\n")
    print(f"{'Symbol':<25} {'Oracle':<12} {'Tick Size':<15} {'Min Size':<12}")
    print("-" * 70)
    
    samples = []
    for m in perps:
        samples.append({
            "symbol": m.get("symbol", ""),
            "oracle_price": m.get("oracle_price", "N/A"),
            "price_tick_size": m.get("price_tick_size", "N/A"),
            "order_size_increment": m.get("order_size_increment", "N/A"),
        })
    
    # Sort by oracle price
    samples.sort(key=lambda x: float(x["oracle_price"]) if x["oracle_price"] != "N/A" else 0)
    
    for s in samples[:15]:  # Show 15 lowest
        oracle = s['oracle_price']
        if oracle == "N/A":
            continue
        print(f"{s['symbol']:<25} ${float(oracle):<11.6f} {s['price_tick_size']:<15} {s['order_size_increment']:<12}")
    
    print("\n... and highest:\n")
    for s in samples[-5:]:
        oracle = s['oracle_price']
        if oracle == "N/A":
            continue
        print(f"{s['symbol']:<25} ${float(oracle):<11.2f} {s['price_tick_size']:<15} {s['order_size_increment']:<12}")
    
    return samples


async def main():
    print("TICK SIZE & MIN PRICE TEST")
    print("Finding the correct order format for each exchange\n")
    
    bp = await test_backpack_markets()
    px = await test_paradex_markets()
    
    print("\n" + "=" * 70)
    print("CONCLUSION")
    print("=" * 70)
    print("""
To correctly format orders:

1. BACKPACK:
   - Use filters.price.tickSize for price precision
   - Use filters.price.minPrice for minimum price
   - Use filters.quantity.minQuantity for minimum quantity
   - Price must be: price >= minPrice AND multiple of tickSize

2. PARADEX:
   - Use price_tick_size for price precision
   - Use order_size_increment for quantity precision
   - Price must be: multiple of price_tick_size

3. SOLUTION:
   - On startup, fetch all market specs from both exchanges
   - Store in a dict: {pair: {bp: {...}, px: {...}}}
   - Before creating opportunity, validate prices against specs
   - Before sending order, round to correct tick size
""")


if __name__ == "__main__":
    asyncio.run(main())
