"""
Debug: Check what the FIRST message from Paradex WebSocket contains.
"""
import asyncio
import websockets
import orjson

async def test_first_message():
    print("=" * 70)
    print("PARADEX FIRST MESSAGE TEST")
    print("=" * 70)
    
    market = "FARTCOIN-USD-PERP"
    
    async with websockets.connect("wss://ws.api.prod.paradex.trade/v1") as ws:
        # Subscribe
        await ws.send(orjson.dumps({
            "jsonrpc": "2.0",
            "method": "subscribe",
            "params": {"channel": f"order_book.{market}.snapshot@15@50ms"},
            "id": 1
        }).decode())
        
        print(f"Subscribed to {market}")
        print()
        
        # Get first 3 messages
        for i in range(3):
            msg = await ws.recv()
            data = orjson.loads(msg)
            
            print(f"MESSAGE {i + 1}:")
            print("-" * 50)
            
            if "result" in data:
                print(f"  Type: Subscription ACK")
                print(f"  Result: {data.get('result')}")
            elif "params" in data:
                ch = data["params"].get("channel", "")
                bd = data["params"].get("data", {})
                
                update_type = bd.get("update_type", "?")
                inserts = len(bd.get("inserts", []))
                updates = len(bd.get("updates", []))
                deletes = len(bd.get("deletes", []))
                
                print(f"  Type: {update_type} (s=snapshot, d=delta)")
                print(f"  Inserts: {inserts}")
                print(f"  Updates: {updates}")
                print(f"  Deletes: {deletes}")
                
                if update_type == "s":
                    print()
                    print("  SNAPSHOT DATA:")
                    # Show bids
                    bids = [e for e in bd.get("inserts", []) if e.get("side") == "BUY"]
                    asks = [e for e in bd.get("inserts", []) if e.get("side") == "SELL"]
                    
                    print(f"    Bids: {len(bids)}")
                    if bids:
                        top_bid = max(bids, key=lambda x: float(x["price"]))
                        print(f"    Best bid: {top_bid['price']} x {top_bid['size']}")
                    
                    print(f"    Asks: {len(asks)}")
                    if asks:
                        top_ask = min(asks, key=lambda x: float(x["price"]))
                        print(f"    Best ask: {top_ask['price']} x {top_ask['size']}")
            
            print()
        
        print("=" * 70)


if __name__ == "__main__":
    asyncio.run(test_first_message())
