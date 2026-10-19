"""
Bot Configuration - All settings from environment variables
"""

import os

# Trading thresholds
MIN_SPREAD = float(os.getenv("MIN_SPREAD", "0.15"))
MIN_PROFIT_USD = float(os.getenv("MIN_PROFIT_USD", "0.50"))
MAX_ORDER_USD = float(os.getenv("MAX_ORDER_USD", "1000"))
MAX_POSITION_AGE = 300  # Close positions older than 5 minutes

# Execution mode
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
DEBUG = os.getenv("DEBUG", "true").lower() == "true"

# Slippage buffer: accept slightly worse price to improve fill rate
SLIPPAGE_BUFFER = float(os.getenv("SLIPPAGE_BUFFER", "0.0015"))

# Cooldown and concurrency control
COOLDOWN_SUCCESS = float(os.getenv("COOLDOWN_SUCCESS", "5.0"))
COOLDOWN_FAILURE = float(os.getenv("COOLDOWN_FAILURE", "3.0"))
COOLDOWN_PARTIAL = float(os.getenv("COOLDOWN_PARTIAL", "30.0"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "5"))

# Exchange fees
BP_FEE = 0.0002   # 0.02% Backpack
PX_FEE = 0.0000   # 0% Paradex
