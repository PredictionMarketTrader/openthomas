"""LLM-in-replay: re-run the forecast stage over archived guidance data.

Scores what a prompt template (and anchor delta) would have done on settled
weather markets: rebuild the decision-time prompt from as-of data (guidance
consensus, station stats, snapshot quotes — never the outcome), get the
model's probability, clip it to baseline ± delta exactly as live does, then
push it through the FROZEN champion decision rule. The kernel gate samples
the rows and owns the PnL/Brier math (kernel/gate.py::score_forecast);
this module mirrors the live pipeline — it is the thing being tested.

Fidelity limits (documented, not hidden): replay has no news brief, no
intraday observations, no NWS discussion, and no calibration layer — it
measures the prompt's skill at adjusting the statistical baseline from
guidance alone, the dominant live pathway for weather markets.

Model outputs are cached by (model, exact built prompt) — so a cache entry
can never answer for a prompt the model didn't actually see: template edits
AND revisions to a row's archived guidance both miss. The anchor clip is
applied AFTER the cache, so delta-only mutations re-score from cache with
zero model calls, and repeated meta-cycles only pay for new rows. The cache
compacts to this run's touched entries after each real meta-cycle, so dead
candidate templates don't accumulate forever.
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from ..config import ModelConfig
from ..forecast.engine import PROMPT, SYSTEM, ForecastEngine, fill_template
from ..llm import CompletionClient
from ..weather.desk import baseline_line, settles_line, strike_line
from ..weather.replay import ReplayRow, decide
from ..weather.stations import STATIONS

REPLAY_WORKERS = 8


def render_data(row: ReplayRow) -> str:
    """The prompt data block, rebuilt from what was durably archived. The
    shared prose comes from weather/desk.py so it reads exactly like live;
    what live has and replay lacks (obs, per-model values, NWS discussion)
    is a documented fidelity limit, not a wording drift."""
    station = STATIONS.get(row.station)
    lines = [settles_line(station, row.kind, row.day)] if station else []
    if row.strike_desc:
        lines.append(strike_line(row.kind, row.strike_desc))
    if row.mean is not None:
        lines.append(f"Model guidance consensus for the {row.kind}: "
                     f"{row.mean:.1f} ± {row.spread:.1f} °F (NWP ensemble, day-ahead).")
        lines.append(baseline_line(row.p_model, row.mean, row.bias, row.sigma,
                                   with_obs=False))
    else:
        lines.append(f"Statistical baseline: P(YES) = {row.p_model:.2f}.")
    return "\n".join(lines)


def build_prompt(template: str, row: ReplayRow) -> str:
    return fill_template(
        template,
        question=row.question or row.ticker,
        rules="(not archived at decision time — treat the headline literally)",
        category="climate/weather",
        bid=f"{row.yes_bid:.2f}", ask=f"{row.yes_ask:.2f}", close=row.day,
        data=render_data(row),
    )


class ForecastReplayer:
    """Callable-factory: strategy(template, delta) -> rows -> (trades, pairs)."""

    def __init__(self, config: ModelConfig, cache_path: Path,
                 decision_params: dict, fee_fn, usage_sink=None):
        # Temperature 0 and a single sample: replay wants the template's
        # central tendency, reproducibly, not sampling noise.
        self.config = config.model_copy(update={"temperature": 0.0, "ensemble_size": 1})
        self.client = CompletionClient(self.config, usage_sink=usage_sink, node="replay")
        self.decision_params = decision_params
        self.fee_fn = fee_fn
        self.cache_path = cache_path
        self._cache: dict[str, float] = {}
        self._touched: set[str] = set()
        if cache_path.exists():
            for line in cache_path.read_text().splitlines():
                try:
                    entry = json.loads(line)
                    self._cache[entry["k"]] = entry["p"]
                except (json.JSONDecodeError, KeyError):
                    continue

    def _key(self, prompt: str) -> str:
        # Keyed on the exact prompt, not (template, ticker): if a row's
        # archived guidance is ever revised, the prompt changes and the stale
        # entry can no longer answer for it.
        return hashlib.sha1(f"{self.config.model}|{prompt}".encode()).hexdigest()[:16]

    def _forecast_many(self, template: str, rows: list[ReplayRow]) -> dict[str, float]:
        """ticker -> raw model probability (pre-clip), via cache then model."""
        out: dict[str, float] = {}
        misses: list[tuple[str, ReplayRow, str]] = []
        for row in rows:
            prompt = build_prompt(template, row)
            key = self._key(prompt)
            self._touched.add(key)
            p = self._cache.get(key)
            if p is not None:
                out[row.ticker] = p
            else:
                misses.append((key, row, prompt))

        def one(miss: tuple[str, ReplayRow, str]) -> tuple[str, ReplayRow, float | None]:
            key, row, prompt = miss
            try:
                parsed = ForecastEngine._parse(self.client.complete(SYSTEM, prompt))
                return key, row, (float(parsed["probability"]) if parsed else None)
            except Exception:
                return key, row, None

        if misses:
            with ThreadPoolExecutor(max_workers=REPLAY_WORKERS) as pool:
                results = list(pool.map(one, misses))
            # Transient failures are simply not cached — retried next cycle.
            successes = [(key, row, p) for key, row, p in results if p is not None]
            if successes:
                with self.cache_path.open("a") as f:
                    for key, row, p in successes:
                        out[row.ticker] = p
                        self._cache[key] = p
                        f.write(json.dumps({"k": key, "p": p}) + "\n")
        return out

    def compact(self) -> None:
        """Rewrite the cache to only this run's touched entries. Everything
        else belongs to discarded candidate templates or aged-out rows and
        would otherwise accumulate forever."""
        if not self._touched:
            return
        live = {k: self._cache[k] for k in self._touched if k in self._cache}
        self.cache_path.write_text(
            "".join(json.dumps({"k": k, "p": p}) + "\n" for k, p in live.items()))
        self._cache = live

    def strategy(self, template: str | None, anchor_delta: float):
        template = template or PROMPT  # None = the built-in default

        def run(rows: list[ReplayRow]):
            p_llm = self._forecast_many(template, rows)
            adjusted, pairs = [], []
            for row in rows:
                p = p_llm.get(row.ticker)
                if p is None:
                    # Model gave nothing for this row: fall back to the
                    # baseline so every candidate is scored on the same rows.
                    p = row.p_model
                else:
                    # The live anchor clip: the LLM adjusts the baseline,
                    # it doesn't replace it.
                    p = min(max(p, row.p_model - anchor_delta),
                            row.p_model + anchor_delta)
                pairs.append((p, 1 if row.outcome_yes else 0))
                adjusted.append(replace(row, p_model=p))
            trades = decide(
                adjusted, self.fee_fn,
                min_edge=self.decision_params["risk.min_edge"],
                market_prior_weight=self.decision_params["risk.market_prior_weight"],
            )
            return trades, pairs
        return run
