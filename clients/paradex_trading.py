"""
Paradex Trading Client v4
Uses paradex-py SDK with L2 (Subkey) authentication.

ENVIRONMENT VARIABLES (L2 recommended):
    PARADEX_L2_ADDRESS=0x...      (Paradex Address from web interface)
    PARADEX_L2_PRIVATE_KEY=0x...  (Copy Paradex Private Key)

ENVIRONMENT VARIABLES (L1 fallback):
    PARADEX_L1_ADDRESS=0x...
    PARADEX_L1_PRIVATE_KEY=0x...
"""

import os
from decimal import Decimal
from datetime import datetime

from paradex_py import Paradex
from paradex_py.api.api_client import ParadexApiClient
from paradex_py.account.account import ParadexAccount
from paradex_py.common.order import Order, OrderSide, OrderType
from paradex_py.environment import PROD, TESTNET


class ParadexClient:
    """
    Paradex Trading Client using official SDK with L2 Subkey auth.
    """
    
    FEE = 0.0002  # 0.02% taker fee
    
    def __init__(
        self,
        l2_address: str = None,
        l2_private_key: str = None,
        l1_address: str = None,
        testnet: bool = False,
    ):
        # Get credentials from env
        self.l2_address = l2_address or os.getenv("PARADEX_L2_ADDRESS", "")
        self.l2_private_key = l2_private_key or os.getenv("PARADEX_L2_PRIVATE_KEY", "")
        self.l1_address = l1_address or os.getenv("PARADEX_L1_ADDRESS", "")
        
        if not self.l2_address or not self.l2_private_key:
            raise ValueError("PARADEX_L2_ADDRESS and PARADEX_L2_PRIVATE_KEY required")
        
        # Convert private key to int
        if isinstance(self.l2_private_key, str):
            if self.l2_private_key.startswith("0x"):
                self.l2_private_key = int(self.l2_private_key, 16)
            else:
                self.l2_private_key = int(self.l2_private_key, 16)
        
        self.env = TESTNET if testnet else PROD
        
        print(f"[PARADEX] Connecting to {'testnet' if testnet else 'mainnet'}...")
        
        # Initialize API client without auto auth
        self.api = ParadexApiClient(env=self.env, auto_auth=False)
        self.config = self.api.fetch_system_config()
        
        # Create account with L2 credentials
        self.account = ParadexAccount(
            config=self.config,
            l1_address=self.l1_address or "0x0000000000000000000000000000000000000000",
            l2_private_key=self.l2_private_key,
        )
        
        # Authenticate
        self.api.account = self.account
        self.api.auth()
        
        print(f"[PARADEX] Connected. L2: {hex(self.account.l2_address)[:16]}...")
    
    # ========================================================================
    # ACCOUNT
    # ========================================================================
    
    def get_account_summary(self):
        """Get account summary."""
        return self.api.fetch_account_summary()
    
    def get_balances(self) -> dict:
        """Get account balances."""
        return self.api.fetch_balances()
    
    def get_positions(self) -> dict:
        """Get open positions."""
        return self.api.fetch_positions()
    
    # ========================================================================
    # MARKET DATA
    # ========================================================================
    
    def get_markets(self) -> dict:
        """Get all markets."""
        return self.api.fetch_markets()
    
    def get_bbo(self, market: str) -> dict:
        """Get best bid/offer for a market."""
        return self.api.fetch_bbo(market=market)
    
    def get_orderbook(self, market: str, depth: int = 20) -> dict:
        """Get orderbook for a market."""
        return self.api.fetch_orderbook(market=market, params={"depth": depth})
    
    # ========================================================================
    # ORDERS
    # ========================================================================
    
    def place_limit_order(
        self,
        market: str,
        side: str,  # "BUY" or "SELL"
        size: float,
        price: float,
        instruction: str = "GTC",  # GTC, POST_ONLY, IOC
        reduce_only: bool = False,
        client_id: str = None,
    ) -> dict:
        """Place a limit order."""
        if client_id is None:
            client_id = f"arb_{int(datetime.now().timestamp() * 1000)}"
        
        order = Order(
            market=market,
            order_type=OrderType.Limit,
            order_side=OrderSide.Buy if side.upper() == "BUY" else OrderSide.Sell,
            size=Decimal(str(size)),
            limit_price=Decimal(f"{price:.5f}"),  # Force 5 decimals for tick_size
            client_id=client_id,
            instruction=instruction,
            reduce_only=reduce_only,
        )
        
        response = self.api.submit_order(order=order)
        
        order_id = response.get("id", "")
        status = response.get("status", "")
        print(f"[ORDER] {market} {side} {size} @ {price} -> {order_id[:20]}... ({status})")
        
        return response
    
    def place_market_order(
        self,
        market: str,
        side: str,
        size: float,
        reduce_only: bool = False,
    ) -> dict:
        """Place a market order (IOC at aggressive price)."""
        bbo = self.get_bbo(market)
        
        if side.upper() == "BUY":
            price = float(bbo.get("ask", 0)) * 1.01
        else:
            price = float(bbo.get("bid", 0)) * 0.99
        
        return self.place_limit_order(
            market=market,
            side=side,
            size=size,
            price=round(price, 2),
            instruction="IOC",
            reduce_only=reduce_only,
        )
    
    def get_open_orders(self, market: str = None) -> dict:
        """Get open orders."""
        params = {"market": market} if market else None
        return self.api.fetch_orders(params=params)
    
    def cancel_order(self, order_id: str) -> None:
        """Cancel an order."""
        self.api.cancel_order(order_id=order_id)
        print(f"[CANCEL] {order_id[:20]}...")
    
    def cancel_all_orders(self, market: str = None) -> None:
        """Cancel all orders."""
        params = {"market": market} if market else None
        self.api.cancel_all_orders(params=params)
        print(f"[CANCEL ALL] {market or 'all'}")
    
    def get_fills(self, market: str = None, limit: int = 20) -> dict:
        """Get recent fills."""
        params = {"page_size": limit}
        if market:
            params["market"] = market
        return self.api.fetch_fills(params=params)


# ============================================================================
# TEST
# ============================================================================

def test_paradex():
    """Test ParadexClient."""
    print("=" * 60)
    print("PARADEX CLIENT TEST")
    print("=" * 60)
    
    try:
        client = ParadexClient()
        
        # Account
        print("\n[TEST] Account Summary...")
        summary = client.get_account_summary()
        print(f"  Value: ${summary.account_value}")
        print(f"  Free Collateral: ${summary.free_collateral}")
        
        # BBO
        print("\n[TEST] ETH-USD-PERP BBO...")
        bbo = client.get_bbo("ETH-USD-PERP")
        print(f"  Bid: {bbo.get('bid')} | Ask: {bbo.get('ask')}")
        
        # Open orders
        print("\n[TEST] Open Orders...")
        orders = client.get_open_orders()
        print(f"  Count: {len(orders.get('results', []))}")
        
        print("\n[DONE] All tests passed!")
        
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_paradex()
