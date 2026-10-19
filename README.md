# Perpetual Spread Analyzer

An experimental perpetual-market toolkit with order-book handling, spread analysis and exchange execution integrations.

![review](https://img.shields.io/badge/review-private-374151) ![primary](https://img.shields.io/badge/primary-Python-2563EB) [![LinkedIn](https://img.shields.io/badge/LinkedIn-Douglas%20Silva-0A66C2?logo=linkedin&logoColor=white)](https://www.linkedin.com/in/douglas-silva-data-analyst/)

[Overview](#overview) · [Architecture](#architecture) · [Setup](#setup) · [Validation](#validation) · [Roadmap](#roadmap) · [Review notes](docs/REVIEW.md)

## Overview

- Order-book modules normalize and compare exchange observations.
- Spread and execution components separate signal and order handling.
- Exchange-specific scripts support protocol investigation and debugging.

**Technology:** Python · httpx · websockets · sortedcontainers · cryptography · Paradex SDK

**Persistence and data:** Order-book and execution state are primarily held in application memory; no database dependency is declared in the root requirements.

## Architecture

```text
Exchange WebSocket books → normalized order-book state → spread/risk logic → optional execution adapter
```

| Location | Review focus |
| --- | --- |
| [`bot/orderbook.py`](bot/orderbook.py) | Source/configuration inspected for scope and structure |
| [`bot/main.py`](bot/main.py) | Source/configuration inspected for scope and structure |
| [`clients/paradex_trading.py`](clients/paradex_trading.py) | Source/configuration inspected for scope and structure |
| [`arb_executor.py`](arb_executor.py) | Source/configuration inspected for scope and structure |
| [`requirements.txt`](requirements.txt) | Source/configuration inspected for scope and structure |

Internal package and module names are retained where changing them would affect imports or compatibility. This descriptive repository name does not imply a completed package/API migration.

## Setup

Inspect requirements.txt and exchange clients before installation. Do not use the old quick-start trading command as an offline demonstration.

Start from an isolated environment. Use `.env.example` when present and inspect the configuration loader: an example file is not necessarily loaded automatically. Runtime databases, real account sessions and private credentials are not bundled. Live integration prerequisites are separate from local tests.

## Validation

Static parsing checked 35 Python files and 0 strict JSON files. Python syntax errors: 0; JSON errors: 0. The selected offline suite passed.

```console
python -m unittest discover -s tests -p test_offline_rounding.py -v
```

These results apply to the selected checks on the preparation copy. They do not establish deployment readiness, full coverage, live provider availability or measured performance. CI definitions, when present under `docs/ci-templates/`, are inactive review templates; no remote CI badge is claimed.

## Limits and current status

This contains real order/signing paths. Scripts named test may contact exchanges or place orders; they were not executed. No profitability, latency or execution guarantee is established.

This private snapshot is for code and portfolio review. Generated runtime material and unreviewed binary assets may be excluded. [Review notes](docs/REVIEW.md) record preparation scope, findings and outstanding validation.

## Roadmap

- [ ] Add a strict paper-only entrypoint with recorded order books.
- [ ] Classify live scripts separately from offline unit tests.
- [ ] Review order sizing, partial fills and shutdown/recovery behavior.

## History and attribution

No recoverable original Git history was verified for this snapshot. Import and preparation commits use their actual current dates; filesystem timestamps are not treated as development dates.

No open-source license is granted by this preparation. A license and attribution review is required before public redistribution.

## Maintainer and feedback

Douglas de Oliveira Silva · [GitHub](https://github.com/qquante) · [LinkedIn](https://www.linkedin.com/in/douglas-silva-data-analyst/)

For this private review, report reproducible issues with a minimal fixture and the exact check output. Keep account data, credentials and generated sensitive reports out of issues and commits. Existing contribution guidelines apply where present.

**Topics:** `python`, `websocket`, `market-data`, `orderbook`, `quantitative-research`, `trading-tools`
