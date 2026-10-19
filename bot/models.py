"""
Data Models - Dataclasses for the arbitrage bot
"""

from dataclasses import dataclass


@dataclass
class Opportunity:
    """Represents an arbitrage opportunity between two exchanges."""
    pair: str
    spread: float
    qty: float
    profit: float
    buy_exchange: str   # "BP" or "PX"
    sell_exchange: str  # "BP" or "PX"
    buy_price: float
    sell_price: float
    ts: float  # timestamp
