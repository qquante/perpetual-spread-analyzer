# Perpetual Spread Analyzer

![Language: Python](https://img.shields.io/badge/Language-Python-3776AB?style=flat-square&logo=python) ![Feed: WebSocket](https://img.shields.io/badge/Feed-WebSocket-555555?style=flat-square) [![License: MIT](https://img.shields.io/badge/License-MIT-555555?style=flat-square)](LICENSE)

An experimental cross-exchange perpetual-market toolkit combining order-book handling, spread detection and execution integrations for Backpack and Paradex.

The detector considers both buy/sell directions and estimates the opportunity after configured fees. It caps size by displayed liquidity and an order-notional limit, while tracking cooldown and concurrent execution state.

## How an opportunity is selected

In [detector.py](bot/engine/detector.py), a candidate must pass spread and estimated net-profit thresholds. When both directions qualify, the detector can retain the larger estimate. Existing open-position state also influences whether a new execution is attempted.

The rough research relationship is:

```text
estimated net = quantity × (sell price − buy price) − configured exchange fees
```

Displayed size and an estimated spread are not executable liquidity guarantees. Fees, slippage, stale books and asymmetric fills can invalidate a quoted opportunity.

## Offline rounding regression

```console
python -m unittest tests.test_offline_rounding -v
```

Run from the repository root. This focused suite replaces the SDK boundary in memory. Files named `test_live_*`, onboarding, WebSocket and order tests are manual/provider-facing diagnostics and are not a safe unattended unit-test batch.

## Code boundaries

| Area | Source |
| --- | --- |
| Book representation | [bot/orderbook.py](bot/orderbook.py) |
| Opportunity selection | [bot/engine/detector.py](bot/engine/detector.py) |
| Execution coordination | [bot/engine/executor.py](bot/engine/executor.py) |
| Provider integration | [bot/exchanges](bot/exchanges), [clients/paradex_trading.py](clients/paradex_trading.py) |

Python dependencies include HTTP/WebSocket clients, sorted book containers, cryptography and the Paradex SDK. Configuration and keys are local operator inputs. Launching the bot can place real orders; no live profit or current exchange compatibility is established by the offline rounding check.

**Technology:** Python · httpx · websockets · sortedcontainers · cryptography · Paradex SDK

Project-authored source: [MIT](LICENSE). Third-party components retain their own notices and terms.
