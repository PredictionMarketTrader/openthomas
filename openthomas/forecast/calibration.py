"""Calibration: turn the model's raw probabilities into ones you can size with.

Prediction Arena's core finding is that PnL tracks prediction accuracy, and
LLM forecasters carry systematic biases (overconfidence, category-specific
blind spots). We fit Platt scaling — logistic regression on the logit of the
raw forecast — against the user's own settled outcomes, per category once
enough samples exist.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MIN_SAMPLES = 30  # below this, apply a fixed shrink toward 0.5 instead of a fit


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def brier_score(pairs: list[tuple[float, int]]) -> float:
    """Mean squared error of forecasts vs outcomes (0 = perfect, 0.25 = coin flip)."""
    if not pairs:
        return float("nan")
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


@dataclass
class PlattScaler:
    a: float = 1.0
    b: float = 0.0
    n_samples: int = 0

    def apply(self, p_raw: float) -> float:
        return _sigmoid(self.a * _logit(p_raw) + self.b)

    @classmethod
    def fit(cls, pairs: list[tuple[float, int]], lr: float = 0.1, epochs: int = 500) -> "PlattScaler":
        """pairs: (raw forecast, outcome 0/1). Plain gradient descent — data is tiny."""
        if len(pairs) < MIN_SAMPLES:
            # Not enough history: shrink toward ignorance rather than trust a fit.
            return cls(a=0.8, b=0.0, n_samples=len(pairs))
        a, b = 1.0, 0.0
        xs = [_logit(p) for p, _ in pairs]
        ys = [y for _, y in pairs]
        n = len(pairs)
        for _ in range(epochs):
            ga = gb = 0.0
            for x, y in zip(xs, ys):
                err = _sigmoid(a * x + b) - y
                ga += err * x / n
                gb += err / n
            a -= lr * ga
            b -= lr * gb
        return cls(a=a, b=b, n_samples=n)


def calibration_table(pairs: list[tuple[float, int]], bins: int = 10) -> list[dict]:
    """Reliability diagram data: forecast bucket → observed frequency."""
    buckets: list[list[int]] = [[] for _ in range(bins)]
    for p, y in pairs:
        buckets[min(int(p * bins), bins - 1)].append(y)
    return [
        {
            "bucket": f"{i / bins:.1f}–{(i + 1) / bins:.1f}",
            "n": len(b),
            "forecast_mid": (i + 0.5) / bins,
            "observed": (sum(b) / len(b)) if b else None,
        }
        for i, b in enumerate(buckets)
    ]
