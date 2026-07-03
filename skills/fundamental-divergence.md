---
name: fundamental-divergence
description: Default strategy — forecast, blend with the market, trade only strong divergence, hold to settlement.
status: active
---

# Fundamental divergence

Buy the side of a binary market when the calibrated, market-blended forecast
diverges from the price by more than the fee-adjusted edge threshold. Hold to
settlement by default.

## When it applies
- Binary markets passing the scanner filters (liquidity, spread, price band,
  time-to-close)
- The forecast names a *specific* piece of information or bias the crowd is
  missing — "the market seems wrong" does not count

## Rules
1. Probability comes from the ensemble median, Platt-calibrated, then blended
   with the market price (`market_prior_weight`). If the blend kills the edge,
   there was no edge.
2. Only enter when confidence ≥ threshold AND edge ≥ `min_edge` after taker
   fees for this venue+category.
3. Default exit is settlement — early exits systematically underperformed in
   live benchmarks. Exit early only if the entry thesis is invalidated (the
   `invalidation` condition from the forecast fires), not on price moves.
4. Never average down without a fresh forecast.

## Lessons learned
(appended by the reflection pass; edit freely — this file is yours)
