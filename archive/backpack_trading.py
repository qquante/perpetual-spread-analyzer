"""
Backpack Exchange Trading Client
Complete integration with ED25519 authentication and order management.

REQUIREMENTS:
    pip install cryptography httpx websockets

SETUP:
    1. Generate ED25519 keypair from Backpack Portfolio Settings
    2. Set environment variables:
        BACKPACK_API_KEY=<base64 public key>
        BACKPACK_API_SECRET=<base64 private key>
"""

import os
import asyncio
import time
import base64
import json
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass
from urllib.parse import urlencode
from rich.console import Console

# HTTP and WebSocket
import httpx
import websockets
import orjson

# Cryptography for ED25519 signing
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

console = Console()


@dataclass
class Order:
    """Order response from Backpack."""
    id: str
    client_id: str
    symbol: str
    side: str
    order_type: str
    price: str
    quantity: str
    status: str
    executed_quantity: str = "0"
    created_at: str = ""


class BackpackTradingClient:
    """
    Backpack Exchange trading client with full ED25519 authentication.
    
    Features:
    - ED25519 signature generation
    - Order placement (limit, market)
    - Order cancellation
    - Balance & position queries
    - WebSocket order updates
    """
    
    BASE_URL = "https://api.backpack.exchange"
    WS_URL = "wss://ws.backpack.exchange"
    
    FEE = 0.0002  # 0.02% taker fee
    
    def __init__(self, api_key: str = None, api_secret: str = None):
        """
        Initialize Backpack trading client.
        
        Args:
            api_key: Base64-encoded ED25519 public key (verifying key)
            api_secret: Base64-encoded ED25519 private key (signing key)
        """
        self.api_key = api_key or os.getenv("BACKPACK_API_KEY", "")
        self.api_secret = api_secret or os.getenv("BACKPACK_API_SECRET", "")
        
        self._private_key: Optional[Ed25519PrivateKey] = None
        self._running = False
        self._ws = None
        self._on_order_update: Optional[Callable] = None
        self._on_fill: Optional[Callable] = None
    
    def _load_private_key(self):
        """Load ED25519 private key from base64 secret."""
        if not self.api_secret:
            raise ValueError("API secret not set")
        
        # Decode base64 private key
        private_key_bytes = base64.b64decode(self.api_secret)
        
        # ED25519 private key is 32 bytes (seed)
        if len(private_key_bytes) == 32:
            self._private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
        elif len(private_key_bytes) == 64:
            # Some formats include public key appended (64 bytes = 32 private + 32 public)
            self._private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes[:32])
        else:
            raise ValueError(f"Invalid private key length: {len(private_key_bytes)} bytes")
    
    def _sign(self, message: str) -> str:
        """
        Sign a message with ED25519 private key.
        
        Returns:
            Base64-encoded signature
        """
        if not self._private_key:
            self._load_private_key()
        
        signature = self._private_key.sign(message.encode('utf-8'))
        return base64.b64encode(signature).decode('utf-8')
    
    def _build_signature_string(self, instruction: str, params: dict, timestamp: int, window: int) -> str:
        """
        Build the signature string for Backpack API.
        
        Format: instruction + sorted_params + &timestamp=X&window=Y
        """
        # Sort params alphabetically by key
        sorted_params = sorted(params.items())
        param_string = urlencode(sorted_params) if sorted_params else ""
        
        # Build full signature string
        if param_string:
            sig_string = f"instruction={instruction}&{param_string}&timestamp={timestamp}&window={window}"
        else:
            sig_string = f"instruction={instruction}&timestamp={timestamp}&window={window}"
        
        return sig_string
    
    def _get_headers(self, instruction: str, params: dict = None) -> dict:
        """
        Generate authenticated headers for a request.
        
        Args:
            instruction: API instruction type (orderExecute, balanceQuery, etc.)
            params: Request parameters (body for POST, query for GET)
        
        Returns:
            Headers dict with X-API-Key, X-Signature, X-Timestamp, X-Window
        """
        timestamp = int(time.time() * 1000)
        window = 5000
        
        params = params or {}
        sig_string = self._build_signature_string(instruction, params, timestamp, window)
        signature = self._sign(sig_string)
        
        return {
            "X-API-Key": self.api_key,
            "X-Signature": signature,
            "X-Timestamp": str(timestamp),
            "X-Window": str(window),
            "Content-Type": "application/json; charset=utf-8",
        }
    
    async def get_balances(self) -> dict:
        """Get account balances."""
        headers = self._get_headers("balanceQuery")
        
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{self.BASE_URL}/api/v1/capital",
                headers=headers
            )
            r.raise_for_status()
            return r.json()
    
    async def get_open_orders(self, symbol: str = None) -> List[dict]:
        """Get open orders."""
        params = {}
        if symbol:
            params["symbol"] = symbol
        
        headers = self._get_headers("orderQueryAll", params)
        
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{self.BASE_URL}/api/v1/orders",
                headers=headers,
                params=params
            )
            r.raise_for_status()
            return r.json()
    
    async def place_order(
        self,
        symbol: str,
        side: str,  # "Bid" (buy) or "Ask" (sell)
        order_type: str,  # "Limit" or "Market"
        quantity: float,
        price: float = None,
        time_in_force: str = "GTC",  # GTC, IOC, FOK
        client_id: str = None,
        self_trade_prevention: str = "RejectTaker",
    ) -> dict:
        """
        Place an order on Backpack Exchange.
        
        Args:
            symbol: Trading pair (e.g. "SOL_USDC_PERP")
            side: "Bid" for buy, "Ask" for sell
            order_type: "Limit" or "Market"
            quantity: Order size
            price: Price (required for limit orders)
            time_in_force: "GTC", "IOC", or "FOK"
            client_id: Optional client order ID
            self_trade_prevention: "RejectTaker", "RejectMaker", "RejectBoth"
        
        Returns:
            Order response from Backpack
        """
        # Normalize side
        if side.upper() == "BUY":
            side = "Bid"
        elif side.upper() == "SELL":
            side = "Ask"
        
        # Build order params
        order_params = {
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "quantity": str(quantity),
            "timeInForce": time_in_force,
            "selfTradePrevention": self_trade_prevention,
        }
        
        if price is not None:
            order_params["price"] = str(price)
        
        if client_id:
            order_params["clientId"] = str(client_id)
        
        # Get headers with signature
        headers = self._get_headers("orderExecute", order_params)
        
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{self.BASE_URL}/api/v1/order",
                headers=headers,
                json=order_params
            )
            
            if r.status_code in [200, 201]:
                data = r.json()
                console.print(f"[green]✓ Order placed: {data.get('id')} | {data.get('status')}[/]")
                
                if self._on_order_update:
                    self._on_order_update(data)
                
                return data
            else:
                console.print(f"[red]✗ Order failed: {r.status_code} {r.text}[/]")
                raise Exception(f"Order failed: {r.text}")
    
    async def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        time_in_force: str = "GTC",
    ) -> dict:
        """Place a limit order (convenience method)."""
        return await self.place_order(
            symbol=symbol,
            side=side,
            order_type="Limit",
            quantity=quantity,
            price=price,
            time_in_force=time_in_force,
        )
    
    async def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
    ) -> dict:
        """Place a market order (convenience method)."""
        return await self.place_order(
            symbol=symbol,
            side=side,
            order_type="Market",
            quantity=quantity,
        )
    
    async def cancel_order(self, symbol: str, order_id: str = None, client_id: str = None) -> dict:
        """
        Cancel an order.
        
        Args:
            symbol: Trading pair
            order_id: Backpack order ID
            client_id: Client order ID (alternative to order_id)
        """
        params = {"symbol": symbol}
        
        if order_id:
            params["orderId"] = order_id
        elif client_id:
            params["clientId"] = client_id
        else:
            raise ValueError("order_id or client_id required")
        
        headers = self._get_headers("orderCancel", params)
        
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.delete(
                f"{self.BASE_URL}/api/v1/order",
                headers=headers,
                params=params
            )
            
            if r.status_code in [200, 204]:
                console.print(f"[yellow]✗ Order cancelled[/]")
                return {"status": "cancelled"}
            else:
                console.print(f"[red]Cancel failed: {r.text}[/]")
                raise Exception(f"Cancel failed: {r.text}")
    
    async def cancel_all_orders(self, symbol: str) -> dict:
        """Cancel all orders for a symbol."""
        params = {"symbol": symbol}
        headers = self._get_headers("orderCancelAll", params)
        
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.delete(
                f"{self.BASE_URL}/api/v1/orders",
                headers=headers,
                params=params
            )
            
            if r.status_code in [200, 204]:
                console.print(f"[yellow]✗ All orders cancelled for {symbol}[/]")
                return {"status": "cancelled"}
            else:
                raise Exception(f"Cancel all failed: {r.text}")
    
    async def get_order_status(self, symbol: str, order_id: str = None, client_id: str = None) -> dict:
        """Get status of a specific order."""
        params = {"symbol": symbol}
        
        if order_id:
            params["orderId"] = order_id
        elif client_id:
            params["clientId"] = client_id
        
        headers = self._get_headers("orderQuery", params)
        
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{self.BASE_URL}/api/v1/order",
                headers=headers,
                params=params
            )
            r.raise_for_status()
            return r.json()
    
    async def wait_for_fill(self, symbol: str, order_id: str, timeout: float = 5.0) -> bool:
        """
        Wait for an order to be filled.
        
        Returns:
            True if filled, False if timeout/cancelled
        """
        start = time.time()
        
        while time.time() - start < timeout:
            try:
                status = await self.get_order_status(symbol, order_id=order_id)
                
                order_status = status.get("status", "")
                executed = float(status.get("executedQuantity", "0"))
                quantity = float(status.get("quantity", "1"))
                
                if order_status == "Filled" or executed >= quantity:
                    avg_price = status.get("executedQuoteQuantity", "0")
                    console.print(f"[bold green]✓ FILLED | Executed: {executed}[/]")
                    
                    if self._on_fill:
                        self._on_fill(status)
                    return True
                
                if order_status in ["Cancelled", "Expired"]:
                    console.print(f"[red]✗ {order_status}[/]")
                    return False
                
                await asyncio.sleep(0.1)
            except Exception as e:
                console.print(f"[dim]Status check error: {e}[/]")
                await asyncio.sleep(0.2)
        
        console.print(f"[yellow]⏱ Timeout waiting for fill[/]")
        return False
    
    def _get_ws_signature(self) -> list:
        """Generate signature for WebSocket authentication."""
        timestamp = int(time.time() * 1000)
        window = 5000
        
        sig_string = f"instruction=subscribe&timestamp={timestamp}&window={window}"
        signature = self._sign(sig_string)
        
        return [self.api_key, signature, str(timestamp), str(window)]
    
    async def start_order_stream(self, symbol: str = None):
        """
        Start WebSocket stream for order updates.
        
        Args:
            symbol: Optional symbol filter (e.g. "SOL_USDC_PERP")
        """
        self._running = True
        
        try:
            async with websockets.connect(self.WS_URL) as ws:
                self._ws = ws
                
                # Subscribe to order updates (authenticated)
                channel = f"account.orderUpdate.{symbol}" if symbol else "account.orderUpdate"
                
                subscribe_msg = {
                    "method": "SUBSCRIBE",
                    "params": [channel],
                    "signature": self._get_ws_signature()
                }
                
                await ws.send(json.dumps(subscribe_msg))
                console.print(f"[green]✓ Subscribed to {channel}[/]")
                
                async for msg in ws:
                    if not self._running:
                        break
                    
                    try:
                        data = orjson.loads(msg)
                        
                        if "stream" in data and "orderUpdate" in data["stream"]:
                            order_data = data.get("data", {})
                            self._handle_order_update(order_data)
                    except Exception as e:
                        console.print(f"[dim]WS parse error: {e}[/]")
        
        except Exception as e:
            console.print(f"[red]WebSocket error: {e}[/]")
    
    def _handle_order_update(self, data: dict):
        """Handle order update from WebSocket."""
        order_id = data.get("i")  # Order ID
        status = data.get("X")  # Status
        executed = data.get("z", "0")  # Executed quantity
        trade_id = data.get("t")  # Trade ID (fill)
        
        console.print(f"[cyan]Order Update: {order_id} | {status} | Executed: {executed}[/]")
        
        if self._on_order_update:
            self._on_order_update(data)
        
        # Check if this is a fill
        if trade_id and float(executed) > 0:
            console.print(f"[bold green]✓ FILL: Trade {trade_id}[/]")
            if self._on_fill:
                self._on_fill(data)
    
    def on_order_update(self, callback: Callable):
        """Register callback for order updates."""
        self._on_order_update = callback
    
    def on_fill(self, callback: Callable):
        """Register callback for fills."""
        self._on_fill = callback
    
    def stop(self):
        """Stop WebSocket stream."""
        self._running = False


async def demo():
    """Demo of the Backpack trading client."""
    console.print("[bold]BACKPACK EXCHANGE TRADING CLIENT DEMO[/]")
    console.print()
    
    # Check environment variables
    api_key = os.getenv("BACKPACK_API_KEY")
    api_secret = os.getenv("BACKPACK_API_SECRET")
    
    if not api_key or not api_secret:
        console.print("[yellow]Set environment variables:[/]")
        console.print("  BACKPACK_API_KEY=<base64 public key>")
        console.print("  BACKPACK_API_SECRET=<base64 private key>")
        console.print()
        console.print("[dim]Get keys from: Portfolio Settings > API Keys[/]")
        return
    
    # Initialize client
    client = BackpackTradingClient(api_key, api_secret)
    
    try:
        # Get balances
        console.print("Fetching balances...")
        balances = await client.get_balances()
        console.print(f"Balances: {balances}")
        
        # Get open orders
        console.print("\nFetching open orders...")
        orders = await client.get_open_orders()
        console.print(f"Open orders: {len(orders)}")
        
        # Example: Place a small test order (commented out for safety)
        # response = await client.place_limit_order(
        #     symbol="SOL_USDC_PERP",
        #     side="Bid",
        #     quantity=0.1,
        #     price=10.0,  # Far from market - won't fill
        #     time_in_force="GTC",
        # )
        # await client.cancel_order("SOL_USDC_PERP", order_id=response["id"])
        
    except Exception as e:
        console.print(f"[red]Error: {e}[/]")


if __name__ == "__main__":
    asyncio.run(demo())
