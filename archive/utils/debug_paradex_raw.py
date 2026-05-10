"""
Debug - Ver dados RAW do Paradex WebSocket
"""

import asyncio
import websockets
import orjson

async def main():
    print("Conectando ao Paradex WebSocket...")
    
    async with websockets.connect("wss://ws.api.prod.paradex.trade/v1") as ws:
        # Exact format from test_paradex.py that worked
        channel = "order_book.BTC-USD-PERP.snapshot@15@100ms"
        print(f"Trying channel: {channel}")
        
        await ws.send(orjson.dumps({
            "jsonrpc": "2.0",
            "method": "subscribe",
            "params": {"channel": channel},
            "id": 1
        }).decode())
        
        print("Subscrito! Esperando mensagens...\n")
        
        count = 0
        async for msg in ws:
            data = orjson.loads(msg)
            
            print(f"=== MSG {count} ===")
            print(f"Keys: {list(data.keys())}")
            
            if "error" in data:
                print(f"ERROR: {data['error']}")
            
            if "result" in data:
                print(f"Result: {data['result']}")
            
            if "method" in data:
                print(f"Method: {data['method']}")
            
            if "params" in data:
                params = data["params"]
                print(f"Params keys: {list(params.keys())}")
                
                if "channel" in params:
                    print(f"Channel: {params['channel']}")
                
                if "data" in params:
                    book_data = params["data"]
                    print(f"Data keys: {list(book_data.keys())}")
                    
                    if "inserts" in book_data:
                        inserts = book_data["inserts"]
                        print(f"Inserts count: {len(inserts)}")
                        if inserts:
                            print(f"First insert: {inserts[0]}")
                            print(f"Last insert: {inserts[-1]}")
                    
                    if "deletes" in book_data:
                        print(f"Deletes count: {len(book_data['deletes'])}")
            
            print()
            
            count += 1
            if count >= 5:
                break


if __name__ == "__main__":
    asyncio.run(main())
