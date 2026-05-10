"""
Configuration module for the arbitrage bot.
Loads settings from environment variables.
"""

import os
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


@dataclass
class BackpackConfig:
    """Backpack Exchange configuration."""
    api_key: str = field(default_factory=lambda: os.getenv("BACKPACK_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("BACKPACK_API_SECRET", ""))
    ws_url: str = "wss://ws.backpack.exchange"
    rest_url: str = "https://api.backpack.exchange"
    symbol: str = field(default_factory=lambda: os.getenv("TRADING_SYMBOL_BACKPACK", "SOL_USDC_PERP"))


@dataclass
class ParadexConfig:
    """Paradex Exchange configuration."""
    l1_address: str = field(default_factory=lambda: os.getenv("PARADEX_L1_ADDRESS", ""))
    l1_private_key: str = field(default_factory=lambda: os.getenv("PARADEX_L1_PRIVATE_KEY", ""))
    symbol: str = field(default_factory=lambda: os.getenv("TRADING_SYMBOL_PARADEX", "SOL-USD-PERP"))
    environment: str = "mainnet"  # or "testnet"


@dataclass
class RedisConfig:
    """Redis configuration."""
    host: str = field(default_factory=lambda: os.getenv("REDIS_HOST", "localhost"))
    port: int = field(default_factory=lambda: int(os.getenv("REDIS_PORT", "6379")))
    db: int = field(default_factory=lambda: int(os.getenv("REDIS_DB", "0")))
    password: Optional[str] = field(default_factory=lambda: os.getenv("REDIS_PASSWORD") or None)


@dataclass
class TradingConfig:
    """Trading parameters."""
    capital_usd: float = field(default_factory=lambda: float(os.getenv("CAPITAL_USD", "100")))
    max_leverage: float = field(default_factory=lambda: float(os.getenv("MAX_LEVERAGE", "10")))
    profit_threshold_percent: float = field(default_factory=lambda: float(os.getenv("PROFIT_THRESHOLD_PERCENT", "0.15")))
    max_position_size: float = field(default_factory=lambda: float(os.getenv("MAX_POSITION_SIZE", "1.0")))
    trade_cooldown_seconds: int = field(default_factory=lambda: int(os.getenv("TRADE_COOLDOWN_SECONDS", "2")))
    paper_trading: bool = field(default_factory=lambda: os.getenv("PAPER_TRADING", "true").lower() == "true")


@dataclass
class Config:
    """Main configuration container."""
    backpack: BackpackConfig = field(default_factory=BackpackConfig)
    paradex: ParadexConfig = field(default_factory=ParadexConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))


# Global config instance
config = Config()
