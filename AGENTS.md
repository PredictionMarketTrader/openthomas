# OpenThomas — guide for coding agents

Autonomous AI trading agent for prediction markets (Polymarket, Kalshi).
Python 3.10+, package in `openthomas/`, tests in `tests/` (pytest), deps via
`pip install -e ".[dev]"`, lint with `ruff check`.

## Map

- `openthomas/agent/loop.py` — the trading cycle orchestrator
- `openthomas/markets/` — connectors (`base.py` = unified model; `paper.py` =
  simulated fills at bid/ask on real data)
- `openthomas/forecast/` — LLM ensemble engine + Platt calibration
- `openthomas/research/news.py` — keyless news retrieval (GDELT, Google News RSS)
- `openthomas/edge/scanner.py` — pre-LLM filters + cross-platform arb detection
- `openthomas/risk/engine.py` — Kelly sizing, caps, drawdown kill-switch
- `openthomas/memory/` — SQLite journal + lesson distillation
- `openthomas/cli.py` — typer CLI (`init/scan/run/report/vital`)
- `docs/DESIGN.md` — architecture rationale; `docs/EDGE.md` — strategy basis

## Hard rules

- `openthomas/risk/` must stay deterministic: no LLM calls, no learned
  parameters, full unit coverage. The model proposes; the risk engine disposes.
- Paper mode stays the default; live trading keeps requiring both
  `mode: live` in config AND the `--live` flag.
- Prices are probabilities in [0,1] for the YES side everywhere; Kalshi's
  dollar-string fields are converted at the connector boundary.
- Mark-to-market uses bid (liquidation value), never mid.
- Don't touch `openthomas/risk/` and connector order paths in the same PR.

## Verify

`pytest -q` must pass; `openthomas scan` (no keys needed) is the live
end-to-end smoke test against real venue APIs.
