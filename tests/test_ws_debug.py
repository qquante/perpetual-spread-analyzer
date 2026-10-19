"""
Paradex WebSocket Debug - Test multiple channel formats.
"""

import asyncio
import logging
import websockets
import orjson

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s', datefmt='%H:%M:%S')

# URLs to try
URLS = [
    "wss://ws.api.prod.paradex.trade/v1",
    "wss://ws.api.prod.paradex.trade/v1/",
    "wss://ws.api.prod.paradex.trade/v1?",
]

# Channel formats to try
CHANNELS = [
    "order_book.SOL-USD-PERP.snapshot@15@100ms",
    "order_book.SOL-USD-PERP.snapshot@15@50ms",
    "order_book.SOL-USD-PERP.snapshot@15@100ms[0_01]",
    "order_book.SOL-USD-PERP.snapshot@15@100ms[1]",
]


async def test_subscription(url: str, channel: str) -> bool:
    """Test a single URL + channel combination."""
    try:
        async with websockets.connect(url, close_timeout=3) as ws:
            subscribe_msg = {
                "jsonrpc": "2.0",
                "method": "subscribe",
                "params": {"channel": channel},
                "id": 1
            }
            await ws.send(orjson.dumps(subscribe_msg).decode())
            
            # Wait for response
            try:
                response = await asyncio.wait_for(ws.recv(), timeout=3)
                data = orjson.loads(response)
                
                if "error" in data:
                    print(f"   ❌ {data['error']}")
                    return False
                elif "result" in data:
                    print(f"   ✅ SUCCESS! Result: {data['result']}")
                    return True
                elif "params" in data:
                    print(f"   ✅ SUCCESS! Got data immediately")
                    return True
                else:
                    print(f"   ⚠️ Unknown response: {data}")
                    return False
                    
            except asyncio.TimeoutError:
                print("   ⏰ Timeout waiting for response")
                return False
                
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False


async def main():
    print("=" * 60)
    print("  PARADEX WEBSOCKET DEBUG")
    print("  Testing URL + Channel combinations")
    print("=" * 60)
    
    found = False
    
    for url in URLS:
        for channel in CHANNELS:
            print(f"\n🔍 URL: {url}")
            print(f"   Channel: {channel}")
            
            success = await test_subscription(url, channel)
            
            if success:
                found = True
                print(f"\n🎉 FOUND WORKING COMBINATION!")
                print(f"   URL: {url}")
                print(f"   Channel: {channel}")
                break
        
        if found:
            break
    
    if not found:
        print("\n❌ No working combination found")
        print("\n🔍 Trying BBO channel instead...")
        
        # Try BBO as fallback
        bbo_channels = [
            "bbo.SOL-USD-PERP.100ms",
            "bbo.SOL-USD-PERP",
            "bbo.SOL-USD-PERP.snapshot@100ms",
        ]
        
        for url in URLS:
            for channel in bbo_channels:
                print(f"\n🔍 URL: {url}")
                print(f"   Channel: {channel}")
                
                success = await test_subscription(url, channel)
                if success:
                    print(f"\n🎉 BBO WORKS!")
                    break


if __name__ == "__main__":
    asyncio.run(main())
