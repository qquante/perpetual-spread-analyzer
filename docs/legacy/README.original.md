> Historical project notes. Paths, operational status, badges and test claims below are retained as archival reference and have not all been revalidated. Use the root README and docs/REVIEW.md for this review snapshot.

# Backpack × Paradex Arbitrage Bot

Sistema de arbitragem de spread entre exchanges para perpetual futures.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Start Redis (if not running)
redis-server

# Run the bot
python -m src.main
```

## Architecture

```
src/
├── config.py              # Configuration
├── main.py                # Entry point
├── core/
│   ├── redis_client.py    # Redis connection
│   └── orderbook.py       # Orderbook class
├── exchanges/
│   ├── backpack_client.py # Backpack API
│   └── paradex_client.py  # Paradex API
└── engine/
    ├── arbitrage_engine.py
    ├── order_executor.py
    └── risk_manager.py
```

## Strategy: Aggressive Orderbook Taker

The bot uses **IOC (Immediate or Cancel)** orders to aggressively take liquidity:

1. Monitor orderbooks on both exchanges via WebSocket
2. Detect spread opportunities above threshold
3. Execute simultaneous IOC orders on both sides
4. Profit from price difference minus fees
