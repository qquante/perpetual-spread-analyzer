"""Quick test for Paradex SDK public endpoints."""
from paradex_py import Paradex
from paradex_py.environment import PROD

print("[TEST] Connecting to Paradex PROD...")
p = Paradex(env=PROD)

print("[TEST] Fetching ETH-USD-PERP BBO...")
bbo = p.api_client.fetch_bbo("ETH-USD-PERP")
print(f"  Bid: {bbo.get('bid')} | Ask: {bbo.get('ask')}")

print("[TEST] Fetching markets...")
markets = p.api_client.fetch_markets()
print(f"  Total markets: {len(markets.get('results', []))}")

print("[OK] All tests passed!")
