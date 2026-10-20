# Perpetual Spread Analyzer

An experimental perpetual-market toolkit with order-book handling, spread analysis and exchange execution integrations.

## Features

- Order-book modules normalize and compare exchange observations.
- Spread and execution components separate signal and order handling.
- Exchange-specific scripts support protocol investigation and debugging.

## Stack

Python · httpx · websockets · sortedcontainers · cryptography · Paradex SDK

Order-book and execution state are primarily held in application memory; no database dependency is declared in the root requirements.

## Getting started

Inspect requirements.txt and exchange clients before installation. Do not use the old quick-start trading command as an offline demonstration.

## How it works

```text
Exchange WebSocket books → normalized order-book state → spread/risk logic → optional execution adapter
```

## Current limitations

This contains real order/signing paths. Legacy exchange scripts named test may contact exchanges or place orders; those live scripts were not executed. The new rounding tests use an in-memory SDK replacement. No profitability, latency or execution guarantee is established.

## Tests

```console
python -m unittest discover -s tests -p test_offline_rounding.py -v
```

These checks use offline fixtures or controlled backends.

## License

Project-authored source code is licensed under the [MIT License](LICENSE). Third-party components and datasets retain their respective rights and licenses.
