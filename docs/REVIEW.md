# Private preparation review — Perpetual Spread Analyzer

## Scope and evidence

This review combines file inspection, manifest/README analysis, static parsing and selected offline tests. It is not a complete manual line-by-line audit of every source file, penetration test or production certification.

- Source files by language: {"Python": 36}.
- Dependency/entrypoint manifests: `requirements.txt`
- Main inspection locations: `bot/orderbook.py`, `bot/main.py`, `clients/paradex_trading.py`, `arb_executor.py`, `requirements.txt`.
- Original source folders remain unchanged; this is an isolated preparation copy.
- Descriptive repository name and relevant GitHub topics were selected from implementation evidence.

## Findings

This contains real order/signing paths. Scripts named test may contact exchanges or place orders; they were not executed. No profitability, latency or execution guarantee is established.

The previous root README is archived under docs/legacy/ when one existed. Historical statuses and benchmark numbers in archived documents are not new measurements. The new README separates capabilities, setup, validation and unfinished work.

## Code cleanup policy

Credential fallbacks were removed only in identified configuration files. Local secrets, sessions, logs, downloaded SDK copies and generated output were excluded where identified. No blanket removal of comments, imports, dependencies or logic was performed. Explanations, invariants, safety controls and license notices remain.

Unused-code removal requires reference/call analysis plus relevant tests; it is not inferred from file names or lack of execution in this review.

## Validation result

Static parsing checked 35 Python files and 0 strict JSON files. Python syntax errors: 0; JSON errors: 0. The selected offline suite passed.

No live transaction, order, mining/cloud deployment, Telegram send or model inference is part of the validation reported here. Package installation where required used disabled lifecycle scripts. Full runtime/build validation is pending unless explicitly recorded above.

## History

No recoverable original Git history was verified for this snapshot. Import and preparation commits use their actual current dates; filesystem timestamps are not treated as development dates.

## Remaining work

- Add a strict paper-only entrypoint with recorded order books.
- Classify live scripts separately from offline unit tests.
- Review order sizing, partial fills and shutdown/recovery behavior.

Before any public release, complete content/asset attribution, dependency and secret review for the final chosen scope. Private publication is for review and does not declare every project production-ready.

## Maintainer

[Douglas de Oliveira Silva on LinkedIn](https://www.linkedin.com/in/douglas-silva-data-analyst/) · [GitHub](https://github.com/qquante)

## Additional static diagnostics

Ruff fatal Python diagnostics: **0**. No finding was reported by this selected rule set; this is not a full code audit. See [structured diagnostics](STATIC_REVIEW.json).
