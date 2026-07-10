"""Mutation operators: how the evolution loop generates candidate genomes.

Every candidate is champion params + a mutation restricted to the invoking
operator's parameter tags — a decision-cycle candidate can never smuggle in
an unscored prompt change, and vice versa. Operators today (docs/RSI.md):

- **llm**: mine evidence from the journal, ask the reflector endpoint (a
  local model — the improvement loop must run on the operator's own compute)
  for directed mutations. The LLM supplies judgment as JSON; it never
  touches files and its output is clamped into kernel bounds.
- **random**: Gaussian jitter on numeric params around a parent drawn from
  the whole archive, not just the champion — directed search exploits,
  random search keeps the population from collapsing onto one hill. Text
  params can't be jittered; they inherit the parent's, which quietly
  re-tests archived prompts against fresh data.
- (planned) **code**: diffs against agent-plane source, same gate.
"""

from __future__ import annotations

import json
import random

from ..forecast.engine import PROMPT as DEFAULT_FORECAST_PROMPT
from ..kernel.bounds import Bound, clamp_params, params_for
from ..llm import extract_json
from ..memory.journal import Journal
from ..memory.lessons import stats_block

DECISION_PROMPT = """You tune the decision-rule parameters of OpenThomas, a \
prediction-market weather-trading agent. The rule: blend the model probability \
with the market price (weight = market_prior_weight on the price), then trade \
only when the blended edge after fees exceeds min_edge.

Current champion parameters:
{params}

Tunable parameters and HARD bounds (values outside are clamped):
{bounds}

Track record (live journal):
{evidence}

Champion on replay (held-in / held-out total PnL over settled weather markets):
{champion}

Propose up to {k} alternative parameter sets worth testing against the champion \
on replay. Move parameters for a stated reason, not for variety's sake; if the \
evidence is thin, propose nothing. Respond with ONLY JSON:
{{"candidates": [{{"params": {{"risk.min_edge": 0.07, \
"risk.market_prior_weight": 0.5}}, "rationale": "<one line of evidence>"}}]}}"""

FORECAST_PROMPT = """You improve the forecast PROMPT TEMPLATE of OpenThomas, a \
prediction-market weather-trading agent. Per market the template is filled with \
placeholders {{question}} {{rules}} {{category}} {{bid}} {{ask}} {{close}} \
{{data}} {{news}} {{lessons}}; the model answering it must return JSON with a \
"probability" field. Its output is then clamped to the statistical baseline \
± weather_anchor_delta (bounds [{dlo}, {dhi}]), so the template's job is to \
extract signal the baseline cannot see — not to re-derive the baseline.

Current champion template:
---
{template}
---
Current weather_anchor_delta: {delta}

Track record (live journal):
{evidence}

Champion on replay (PnL and Brier over settled weather markets):
{champion}

Propose up to {k} candidates. Each may rewrite the template (FULL text, keep \
{{question}} and {{data}}, keep the literal "probability" contract, under \
{max_len} chars) and/or move weather_anchor_delta. Change instructions for a \
stated reason tied to the evidence — e.g. how to weigh guidance spread, when \
to defer to the baseline, what invalidates a deviation. If the evidence is \
thin, propose nothing. Respond with ONLY JSON:
{{"candidates": [{{"params": {{"forecast_prompt": "<full template>", \
"weather_anchor_delta": 0.15}}, "rationale": "<one line of evidence>"}}]}}"""


def mine_evidence(journal: Journal) -> str:
    """Failure-pattern summary from the live journal. No LLM required."""
    parts = [stats_block(journal) or "(no settled trades yet)"]
    recent = journal.recent_settlements(15)
    losers = [s for s in recent if s["pnl"] < 0]
    if losers:
        parts.append("Recent losing settlements:")
        parts += [f"- {s['question'][:70]} (${s['pnl']:+.2f})" for s in losers[:8]]
    return "\n".join(parts)


def _parse_candidates(text: str) -> list[dict]:
    cands = (extract_json(text) or {}).get("candidates", [])
    return cands if isinstance(cands, list) else []


def _restrict(params: dict, operator: str) -> dict:
    """Keep only the invoking operator's parameters — no rider mutations."""
    allowed = params_for(operator)
    return {k: v for k, v in params.items() if k in allowed}


def _build_prompt(operator: str, champion_params: dict, evidence: str,
                  champion_summary: str, k: int) -> str:
    space = params_for(operator)
    if operator == "forecast":
        delta_bound = space["weather_anchor_delta"]
        return FORECAST_PROMPT.format(
            # None = tracking the built-in default; show the LLM real text.
            template=champion_params.get("forecast_prompt") or DEFAULT_FORECAST_PROMPT,
            delta=champion_params.get("weather_anchor_delta"),
            dlo=delta_bound.lo, dhi=delta_bound.hi,
            max_len=space["forecast_prompt"].max_len,
            evidence=evidence, champion=champion_summary, k=k,
        )
    numeric = _restrict(champion_params, operator)
    bounds = "\n".join(f"- {key}: [{b.lo}, {b.hi}]" for key, b in space.items())
    return DECISION_PROMPT.format(params=json.dumps(numeric), bounds=bounds,
                                  evidence=evidence, champion=champion_summary, k=k)


def llm_candidates(complete_fn, champion_params: dict, evidence: str,
                   champion_summary: str, k: int = 3,
                   operator: str = "decision") -> list[dict]:
    """[{params, rationale, proposer}] — full param dicts, clamped, restricted
    to the operator's tags, deduped."""
    response = complete_fn(
        "You improve a trading agent's harness. Be terse, specific, evidence-driven.",
        _build_prompt(operator, champion_params, evidence, champion_summary, k),
    )
    out = []
    for cand in _parse_candidates(response)[:k]:
        if not isinstance(cand, dict):
            continue
        mutation = _restrict(clamp_params(cand.get("params") or {}), operator)
        if not mutation:
            continue
        out.append({"params": {**champion_params, **mutation}, "proposer": "llm",
                    "rationale": str(cand.get("rationale", ""))[:200]})
    return dedupe(out, champion_params)


def random_candidates(champion_params: dict, parents: list[dict], k: int = 2,
                      jitter: float = 0.10, rng: random.Random | None = None,
                      operator: str = "decision") -> list[dict]:
    """Gaussian mutations on numeric params (sigma = `jitter` × bound width);
    text params inherit the sampled parent's (archive retest)."""
    rng = rng or random.Random()
    space = params_for(operator)
    out = []
    for _ in range(k):
        parent = rng.choice(parents)
        mutation: dict = {}
        for key, spec in space.items():
            if isinstance(spec, Bound):
                base = parent.get(key, champion_params.get(key))
                base = float(base) if base is not None else (spec.lo + spec.hi) / 2
                mutation[key] = spec.clamp(rng.gauss(base, jitter * (spec.hi - spec.lo)))
            elif parent.get(key) is not None:
                inherited = spec.clamp(parent[key])
                if inherited is not None:
                    mutation[key] = inherited
        out.append({"params": {**champion_params, **mutation},
                    "proposer": "random", "rationale": "archive mutation"})
    return out


def dedupe(candidates: list[dict], champion_params: dict) -> list[dict]:
    seen = {_key(champion_params)}
    out = []
    for cand in candidates:
        key = _key(cand["params"])
        if key not in seen:
            seen.add(key)
            out.append(cand)
    return out


def _key(params: dict) -> tuple:
    return tuple(sorted(
        (k, round(float(v), 4) if isinstance(v, (int, float)) else hash(v))
        for k, v in params.items()
    ))
