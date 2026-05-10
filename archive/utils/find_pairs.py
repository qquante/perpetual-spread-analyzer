"""Fetch common PERP pairs between Backpack and Paradex."""
import httpx
import asyncio

async def main():
    async with httpx.AsyncClient() as c:
        # Backpack
        r1 = await c.get('https://api.backpack.exchange/api/v1/markets')
        bp_markets = r1.json()
        bp_perps = [m['symbol'] for m in bp_markets if 'PERP' in m['symbol']]
        
        # Paradex
        r2 = await c.get('https://api.prod.paradex.trade/v1/markets')
        px_markets = r2.json()['results']
        px_perps = [m['symbol'] for m in px_markets if 'PERP' in m['symbol']]
        
        print(f"Backpack PERP markets ({len(bp_perps)}):")
        for p in bp_perps:
            print(f"  {p}")
        
        print(f"\nParadex PERP markets ({len(px_perps)}):")
        for p in px_perps[:20]:
            print(f"  {p}")
        if len(px_perps) > 20:
            print(f"  ... and {len(px_perps) - 20} more")
        
        # Find common base assets
        print("\n" + "=" * 50)
        print("COMMON PAIRS (for arbitrage):")
        print("=" * 50)
        
        common = []
        for bp in bp_perps:
            base = bp.split('_')[0]  # e.g., SOL from SOL_USDC_PERP
            px_match = f"{base}-USD-PERP"
            if px_match in px_perps:
                common.append((bp, px_match))
                print(f"  {bp} <-> {px_match}")
        
        print(f"\nTotal common pairs: {len(common)}")
        return common

if __name__ == "__main__":
    asyncio.run(main())
