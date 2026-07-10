"""Kernel policy: which parameters the evolution loop may touch, and how far.

A parameter earns a slot in PARAM_SPACE only when an evaluator can actually
discriminate on it in replay — tuning what you cannot measure is drift, not
improvement. Each spec is tagged with the evaluator that scores it:

- "decision": the pure decision-rule replay (cheap, runs daily).
- "forecast": the LLM-in-replay evaluator (local model over archived
  guidance, runs weekly) — see kernel/gate.py and improve/forecast_replay.py.

The loop explores INSIDE this box; widening the box is an operator decision,
never the loop's.
"""

from __future__ import annotations

import string
from dataclasses import dataclass


@dataclass(frozen=True)
class Bound:
    lo: float
    hi: float
    evaluator: str = "decision"

    def clamp(self, v) -> float | None:
        try:
            return round(max(self.lo, min(self.hi, float(v))), 4)
        except (TypeError, ValueError):
            return None


# Placeholders every prompt template may use; a template using anything else
# fails to format and is rejected. Kept in the kernel: the contract between
# the harness and any evolved prompt is policy, not something evolution edits.
PROMPT_PLACEHOLDERS = ("question", "rules", "category", "bid", "ask", "close",
                       "data", "news", "lessons")


@dataclass(frozen=True)
class TextSpec:
    """A prompt template slot: structure is validated, content is evolved."""

    required: tuple[str, ...]  # placeholders that must appear
    must_contain: tuple[str, ...] = ()  # literal substrings (parser contract)
    max_len: int = 6000
    evaluator: str = "forecast"

    def clamp(self, v) -> str | None:
        """Valid template text, or None (= reject, fall back to champion)."""
        if not isinstance(v, str) or not v.strip() or len(v) > self.max_len:
            return None
        for name in self.required:
            if "{" + name + "}" not in v:
                return None
        for literal in self.must_contain:
            if literal not in v:
                return None
        # Whitelist parse, not trial-format: every replacement field must be a
        # bare known placeholder. This rejects malformed braces AND closes the
        # str.format injection channel — an evolved template with `{question.
        # __class__}`-style attribute/index navigation would otherwise splice
        # Python internals into a live prompt.
        try:
            fields = [f for _, f, _, _ in string.Formatter().parse(v) if f is not None]
        except ValueError:
            return None
        if any(f not in PROMPT_PLACEHOLDERS for f in fields):
            return None
        return v


PARAM_SPACE: dict[str, Bound | TextSpec] = {
    "risk.min_edge": Bound(0.04, 0.15),
    "risk.market_prior_weight": Bound(0.30, 0.80),
    # How far the LLM may move the statistical baseline (the anchor clip).
    "weather_anchor_delta": Bound(0.05, 0.30, evaluator="forecast"),
    # The forecast prompt template itself. The '"probability"' literal must
    # survive any rewrite: the response parser keys on it.
    "forecast_prompt": TextSpec(required=("question", "data"),
                                must_contain=('"probability"',)),
}


def params_for(evaluator: str) -> dict:
    return {k: s for k, s in PARAM_SPACE.items() if s.evaluator == evaluator}


def clamp_params(params: dict) -> dict:
    """Drop unknown keys and invalid values, clamp the rest into bounds.

    A text param carried as None passes through: None is the explicit
    "track the built-in default" state, not an invalid value — dropping it
    would make a rollback from an evolved prompt unable to restore the
    default.
    """
    out: dict = {}
    for key, value in (params or {}).items():
        spec = PARAM_SPACE.get(key)
        if spec is None:
            continue
        if value is None and isinstance(spec, TextSpec):
            out[key] = None
            continue
        clamped = spec.clamp(value)
        if clamped is not None:
            out[key] = clamped
    return out
