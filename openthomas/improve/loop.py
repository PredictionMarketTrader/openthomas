"""The evolution loop (slow loop): OpenThomas updating OpenThomas.

One meta-cycle = rollback-check → mine → propose → gate → promote → log,
parameterized by mutation operator (docs/RSI.md):

- "decision" (daily-ish): decision-rule numerics, scored by the pure replay.
- "forecast" (weekly-ish): the forecast prompt template and anchor delta,
  scored by LLM-in-replay on the local model — expensive, so rarer, sampled
  by the kernel, and cached.

The trading loop (fast loop) never waits on it, and it can never kill
trading: every promotion is bounds-clamped, gate-approved, journaled to
improve-log.jsonl, and reversible via lineage rollback.

Division of labor: this module (agent plane) decides what to TRY; the kernel
decides what COUNTS. All file writes happen in this deterministic code after
the gate has ruled — the proposer LLM only ever returns JSON suggestions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Callable

from ..config import Settings
from ..kernel import gate
from ..kernel.bounds import params_for
from ..llm import CompletionClient
from ..markets.kalshi import KalshiConnector
from ..memory.journal import Journal
from ..memory.usage import UsageLedger
from ..weather.replay import collect_all, decide
from ..weather.verification import VerificationStore
from .forecast_replay import ForecastReplayer
from .genome import (BASELINE_ID, Generation, GenerationStore, apply_params,
                     display_params, params_from_settings)
from .proposer import dedupe, llm_candidates, mine_evidence, random_candidates

REPLAY_DAYS = 45


@dataclass(frozen=True)
class OperatorSpec:
    """Everything operator-specific in one record, so adding an operator is
    one entry here (plus its proposer prompt) — not edits scattered across
    cadence dicts, kv keys, and scorer/promotion-rule branches that can
    silently desynchronize."""

    llm_proposals: int
    random_proposals: int
    cadence_hours: int
    last_run_key: str
    beats: Callable[[gate.Score, gate.Score], tuple[bool, str]]


OPERATORS = {
    # Decision numerics are cheap to score — bigger population, daily-ish.
    "decision": OperatorSpec(3, 2, 20, "improve_last_ts", gate.beats),
    # Forecast scoring pays a model call per sampled row — smaller
    # population, weekly-ish, and a Brier veto on top of the PnL rule.
    "forecast": OperatorSpec(2, 1, 156, "improve_forecast_last_ts",
                             gate.beats_forecast),
}


@dataclass
class MetaReport:
    operator: str = "decision"
    rows: int = 0
    rollback: str = ""
    candidates: list[dict] = field(default_factory=list)
    promoted: int | None = None
    reason: str = ""

    def as_dict(self) -> dict:
        return {"ts": _now(), "operator": self.operator, "rows": self.rows,
                "rollback": self.rollback, "candidates": self.candidates,
                "promoted": self.promoted, "reason": self.reason}


def improve_due(journal: Journal, operator: str = "decision") -> bool:
    spec = OPERATORS[operator]
    last = journal.get_kv(spec.last_run_key)
    if not last:
        return True
    age = datetime.now(timezone.utc) - datetime.fromisoformat(last)
    return age > timedelta(hours=spec.cadence_hours)


class Improver:
    def __init__(self, settings: Settings, journal: Journal | None = None,
                 complete_fn=None):
        self.s = settings
        self.journal = journal or Journal(settings.db_path)
        self.store = GenerationStore(settings.home)
        self.usage = UsageLedger(settings.home)
        self._complete = complete_fn
        self._replayer: ForecastReplayer | None = None
        self.fee = KalshiConnector().fee
        self.log_path = settings.home / "improve-log.jsonl"

    def complete(self, system: str, user: str) -> str:
        if self._complete is None:
            self._complete = CompletionClient(
                self.s.reflector or self.s.forecaster,
                usage_sink=self.usage.record, node="propose").complete
        return self._complete(system, user)

    def _strategy(self, params: dict):
        return partial(decide, fee_fn=self.fee,
                       min_edge=params["risk.min_edge"],
                       market_prior_weight=params["risk.market_prior_weight"])

    def _make_scorer(self, operator: str, champion_params: dict):
        """(rows_in, rows_out, params) -> Score, per operator.

        Forecast scoring pins the decision rule to the champion's: candidates
        differ only in what this operator may mutate, so the comparison is
        controlled and the gate's verdict attributes wins to the mutation.
        """
        self._replayer = None
        if operator == "decision":
            return lambda rows_in, rows_out, params: gate.score(
                rows_in, rows_out, params, self._strategy(params))
        self._replayer = ForecastReplayer(
            self.s.forecaster, self.s.home / "llm-replay-cache.jsonl",
            decision_params={k: champion_params[k] for k in params_for("decision")},
            fee_fn=self.fee, usage_sink=self.usage.record,
        )
        return lambda rows_in, rows_out, params: gate.score_forecast(
            rows_in, rows_out, params,
            self._replayer.strategy(params.get("forecast_prompt"),
                                    params["weather_anchor_delta"]))

    # --- the meta-cycle ----------------------------------------------------------
    def meta_cycle(self, days: int = REPLAY_DAYS, dry_run: bool = False,
                   operator: str = "decision") -> MetaReport:
        if operator not in OPERATORS:
            raise ValueError(f"unknown operator {operator!r}; use one of {list(OPERATORS)}")
        report = MetaReport(operator=operator)
        champion_params = params_from_settings(self.s)
        if not dry_run:  # dry run means zero writes, including the seed
            self.store.ensure_baseline(champion_params)

        store = VerificationStore(self.s.home / "weather-verification.jsonl")
        rows = collect_all(store, days)
        report.rows = len(rows)
        rows_in, rows_out = gate.split_rows(rows)
        if not rows_in:
            report.reason = "insufficient replay data — run `openthomas hindcast`?"
            self._finish(report, dry_run)
            return report

        spec = OPERATORS[operator]
        scorer = self._make_scorer(operator, champion_params)
        champion_score = scorer(rows_in, rows_out, champion_params)

        # 1. Out-of-sample regret: has the fresh window turned against the
        # active promotion? Checked before proposing anything new. Adjacent
        # generations differ only in the promoting operator's params (the
        # proposer restricts mutations), so each cadence audits its own kind
        # and the parent's full params ARE the controlled comparison.
        active = self.store.active()
        if (active and active.id != BASELINE_ID and active.parent is not None
                and active.operator == operator):
            parent = self.store.get(active.parent)
            if parent:
                parent_params = {**champion_params, **parent.params}
                parent_score = scorer(rows_in, rows_out, parent_params)
                roll, why = gate.should_rollback(champion_score, parent_score)
                if roll:
                    report.rollback = why
                    if not dry_run:
                        self.store.rollback(why)
                        # Apply exactly what was scored: the merge also fills
                        # genome keys a pre-migration parent never recorded.
                        apply_params(self.s, parent_params)
                    champion_params = parent_params
                    champion_score = parent_score

        # 2. Propose: directed mutations from evidence + random archive mutations.
        evidence = mine_evidence(self.journal)
        champion_summary = (f"held-in ${champion_score.pnl_in:+.2f} "
                            f"({champion_score.held_in.get('n', 0)} trades), "
                            f"held-out ${champion_score.pnl_out:+.2f} "
                            f"({champion_score.held_out.get('n', 0)} trades)")
        if champion_score.brier is not None:
            champion_summary += f", Brier {champion_score.brier:.4f}"
        candidates: list[dict] = []
        try:
            candidates += llm_candidates(self.complete, champion_params, evidence,
                                         champion_summary, spec.llm_proposals, operator)
        except Exception as e:  # a dead LLM endpoint must not stop evolution
            report.reason = f"llm proposer unavailable ({e}); random mutations only. "
        archive = [g.params for g in self.store.all()] or [champion_params]
        candidates += random_candidates(champion_params, archive, spec.random_proposals,
                                        operator=operator)
        candidates = dedupe(candidates, champion_params)

        # 3. Gate every candidate; keep the best qualifying by held-in PnL.
        best: tuple[dict, gate.Score, str] | None = None
        for cand in candidates:
            sc = scorer(rows_in, rows_out, cand["params"])
            ok, why = spec.beats(sc, champion_score)
            report.candidates.append({
                "params": display_params(cand["params"]), "proposer": cand["proposer"],
                "rationale": cand["rationale"], "held_in": sc.held_in,
                "held_out": sc.held_out, "verdict": "pass" if ok else why,
            })
            if ok and (best is None or sc.pnl_in > best[1].pnl_in):
                best = (cand, sc, why)

        # 4. Promote through the store; the running loop picks it up in place.
        if best:
            cand, sc, why = best
            report.reason += f"promote: {why}"
            if not dry_run:
                active = self.store.active()
                gen = self.store.add(Generation(
                    id=-1, parent=active.id if active else BASELINE_ID,
                    params=cand["params"], proposer=cand["proposer"],
                    operator=operator, rationale=cand["rationale"],
                    evidence=evidence[:600], scores=sc.as_dict(),
                ))
                self.store.promote(gen.id, note=why)
                apply_params(self.s, cand["params"])
                report.promoted = gen.id
        elif not report.rollback:
            report.reason += "no candidate cleared the gate; champion holds"

        if self._replayer is not None and not dry_run:
            self._replayer.compact()
        self._finish(report, dry_run)
        return report

    def _finish(self, report: MetaReport, dry_run: bool) -> None:
        if dry_run:
            return
        self.journal.set_kv(OPERATORS[report.operator].last_run_key, _now())
        with self.log_path.open("a") as f:
            f.write(json.dumps(report.as_dict()) + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
