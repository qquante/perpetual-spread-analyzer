#!/usr/bin/env python3
"""
Arbitrage Bot Entry Point v6.0

Modular architecture:
    bot/
    ├── config.py          # Configuration
    ├── models.py          # Data models
    ├── orderbook.py       # FastOrderbook
    ├── exchanges/
    │   ├── backpack.py    # Backpack client
    │   └── paradex.py     # Paradex client
    ├── engine/
    │   ├── detector.py    # Opportunity detection
    │   └── executor.py    # Order execution
    └── main.py            # ArbitrageBot orchestrator
"""

import asyncio
from bot import ArbitrageBot


async def main():
    bot = ArbitrageBot()
    try:
        await bot.run()
    except KeyboardInterrupt:
        bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
