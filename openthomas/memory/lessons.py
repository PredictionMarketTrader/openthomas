"""Structured playbook: the agent's learned trading rules.

Two layers:
1. Hard stats (always): per-category win rate / PnL, calibration summary —
   computed from the journal, no LLM required.
2. Rules (curated): independent entries with provenance, scope, and a
   post-adoption track record. The reflection LLM proposes *operations*
   (add / revise / deprecate) — never a rewrite — so a good old rule can't
   be silently lost and every deprecation carries a reason. Code enforces
   the caps and computes each rule's evidence; the LLM supplies judgment.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ..forecast.calibration import brier_score
from .journal import Journal

MAX_ACTIVE_RULES = 8
MAX_OPS_PER_REFLECTION = 4
MAX_ADDS_PER_REFLECTION = 2

REFLECT_PROMPT = """You maintain the playbook of OpenThomas, a prediction-market \
trading agent. Review the track record and propose changes as OPERATIONS on the \
existing rules — you cannot rewrite the playbook wholesale.

Hard stats:
{stats}

Active rules with post-adoption track records:
{rules}

Recent settlements (question | category | pnl):
{rows}

Rules must be one line, specific, and falsifiable ("Miami model consensus runs 2°F \
cold — shade highs up"), never generic ("do better research"). Deprecate rules whose \
track record is negative or whose edge has decayed; revise rules that are directionally \
right but miscalibrated. If the evidence is thin, output no operations at all.

Respond with ONLY JSON:
{{"ops": [{{"op": "add" | "revise" | "deprecate", "id": <int, required for revise/deprecate>, \
"text": "<the rule, for add/revise>", "scope": "<substring identifying affected markets, \
e.g. 'miami' or 'climate'>", "reason": "<one line of evidence>"}}]}}
At most {max_ops} operations, at most {max_adds} adds."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stats_block(journal: Journal) -> str:
    stats = journal.settlement_stats()
    if not stats["n"]:
        return ""
    pairs = journal.forecast_outcome_pairs()
    lines = [
        f"Track record: {stats['n']} settled, win rate {stats['win_rate']:.0%}, "
        f"avg win ${stats['avg_win']:.2f} vs avg loss ${stats['avg_loss']:.2f}, "
        f"Brier {brier_score(pairs):.3f} (0.25 = coin flip).",
    ]
    for c in journal.category_stats():
        if c["n"] >= 5:
            lines.append(
                f"- {c['category'] or 'uncategorized'}: {c['n']} settled, "
                f"win rate {c['win_rate']:.0%}, pnl ${c['pnl']:+.2f}"
            )
    return "\n".join(lines)


class LessonBook:
    """Keeps its historical name; the storage is now a structured playbook."""

    def __init__(self, lessons_dir: Path):
        self.path = Path(lessons_dir) / "playbook.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # --- storage ---------------------------------------------------------------
    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {"next_id": 1, "rules": []}

    def _save(self, book: dict) -> None:
        self.path.write_text(json.dumps(book, indent=1) + "\n")

    def active_rules(self) -> list[dict]:
        return [r for r in self._load()["rules"] if r["status"] == "active"]

    # --- prompt rendering ---------------------------------------------------------
    def read(self) -> str:
        return "\n".join(f"- {r['text']}" for r in self.active_rules())

    def render_for_prompt(self, journal: Journal) -> str:
        parts = [stats_block(journal)]
        rules = self.active_rules()
        if rules:
            parts.append("Playbook rules (learned from settled trades):")
            parts += [f"R{r['id']} [{r['scope']}]: {r['text']}" for r in rules]
        return "\n".join(p for p in parts if p)

    # --- reflection --------------------------------------------------------------
    def _rules_with_track(self, journal: Journal) -> str:
        lines = []
        for r in self.active_rules():
            perf = journal.scope_performance(r["scope"], r["created"])
            age = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(r["created"])).days
            note = ""
            if perf["n"] >= 10 and perf["pnl"] < 0:
                note = " ⚠ NEGATIVE since adoption — consider deprecating"
            elif age > 45 and perf["n"] == 0:
                note = " ⚠ stale: no scoped settlements in 45+ days"
            lines.append(
                f"R{r['id']} [{r['scope']}, {age}d old, since adoption: "
                f"{perf['n']} settled ${perf['pnl']:+.2f}]: {r['text']}{note}"
            )
        return "\n".join(lines) or "(no rules yet)"

    @staticmethod
    def _parse_ops(text: str) -> list[dict]:
        match = re.search(r"\{.*\}", text or "", re.DOTALL)
        if not match:
            return []
        try:
            ops = json.loads(match.group()).get("ops", [])
            return ops if isinstance(ops, list) else []
        except (json.JSONDecodeError, AttributeError):
            return []

    def apply_ops(self, ops: list[dict]) -> list[str]:
        """Validate and apply curator operations; returns an audit trail."""
        book = self._load()
        by_id = {r["id"]: r for r in book["rules"]}
        applied: list[str] = []
        adds = 0
        for op in ops[:MAX_OPS_PER_REFLECTION]:
            kind = op.get("op")
            text = (op.get("text") or "").strip()
            if kind == "add" and text:
                active = sum(1 for r in book["rules"] if r["status"] == "active")
                if adds >= MAX_ADDS_PER_REFLECTION or active >= MAX_ACTIVE_RULES:
                    continue
                book["rules"].append({
                    "id": book["next_id"], "text": text,
                    "scope": (op.get("scope") or "").strip().lower(),
                    "reason": op.get("reason", ""), "status": "active",
                    "created": _now(),
                })
                applied.append(f"add R{book['next_id']}: {text}")
                book["next_id"] += 1
                adds += 1
            elif kind == "revise" and text and op.get("id") in by_id:
                rule = by_id[op["id"]]
                if rule["status"] != "active":
                    continue
                rule["text"] = text
                if op.get("scope"):
                    rule["scope"] = op["scope"].strip().lower()
                rule["revised"] = _now()
                applied.append(f"revise R{rule['id']}: {text}")
            elif kind == "deprecate" and op.get("id") in by_id:
                rule = by_id[op["id"]]
                if rule["status"] != "active":
                    continue
                rule["status"] = "deprecated"
                rule["deprecated"] = _now()
                rule["deprecate_reason"] = op.get("reason", "")
                applied.append(f"deprecate R{rule['id']}: {op.get('reason', '')}")
        if applied:
            self._save(book)
        return applied

    def reflect(self, journal: Journal, complete_fn) -> str:
        """`complete_fn(system, user) -> str` — the reflector node's client."""
        recent = journal.recent_settlements(30)
        if len(recent) < 5:
            return self.render_for_prompt(journal)
        rows = "\n".join(
            f"{s['question'][:70]} | {s['category']} | ${s['pnl']:+.2f}" for s in recent
        )
        response = complete_fn(
            "You curate trading playbook rules. Be terse, specific, evidence-driven.",
            REFLECT_PROMPT.format(
                stats=stats_block(journal) or "(none yet)",
                rules=self._rules_with_track(journal), rows=rows,
                max_ops=MAX_OPS_PER_REFLECTION, max_adds=MAX_ADDS_PER_REFLECTION,
            ),
        )
        self.apply_ops(self._parse_ops(response))
        return self.render_for_prompt(journal)
