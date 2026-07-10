"""LLM token ledger: what every model call cost, appended as it happens.

Kept out of journal.db on purpose. The journal is trading ground truth with a
schema the gate reasons about; token spend is telemetry that several processes
(trading loop, meta-cycle, replay) write concurrently and nothing reads inside
a transaction. An append-only JSONL takes concurrent O_APPEND writes from
separate processes without a lock, the same way `weather-verification.jsonl`
and `improve-log.jsonl` already do.

`prompt_tokens`/`completion_tokens` are None when a provider does not report
usage — the subscription CLIs (`claude-cli`, `codex-cli`) bill a flat rate and
return no counts. Summaries surface those calls separately rather than
counting them as zero, so "tokens spent on training" never reads lower than it
was.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class Usage:
    """One completion. `node` is the caller: forecast | reflect | propose | replay."""

    ts: str
    node: str
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_tokens: int | None = None

    @property
    def total_tokens(self) -> int | None:
        if self.prompt_tokens is None and self.completion_tokens is None:
            return None
        return (self.prompt_tokens or 0) + (self.completion_tokens or 0)


class UsageLedger:
    """Append-only token ledger at ~/.openthomas/llm-usage.jsonl.

    Recording must never break a trading cycle or a meta-cycle: a full disk or
    a read-only home costs us telemetry, not a forecast. Every write is
    swallowed.
    """

    def __init__(self, home: Path | str):
        self.path = Path(home) / "llm-usage.jsonl"

    def record(self, usage: Usage) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as fh:
                fh.write(json.dumps(asdict(usage)) + "\n")
        except OSError:
            pass

    def read(self) -> list[Usage]:
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text().splitlines():
            try:
                rows.append(Usage(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue  # a torn final line from a crashed write; skip it
        return rows


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def summarize(rows: list[Usage]) -> dict:
    """Totals plus the three cuts the public feed shows: node, model, day.

    `calls_without_usage` is the honest footnote — calls a provider billed but
    never counted.
    """
    def bucket() -> dict:
        return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                "total_tokens": 0, "calls_without_usage": 0}

    total = bucket()
    by_node: dict[str, dict] = defaultdict(bucket)
    by_model: dict[str, dict] = defaultdict(bucket)
    by_day: dict[str, dict] = defaultdict(bucket)

    for r in rows:
        targets = (total, by_node[r.node], by_model[r.model], by_day[r.ts[:10]])
        for t in targets:
            t["calls"] += 1
            if r.total_tokens is None:
                t["calls_without_usage"] += 1
            else:
                t["prompt_tokens"] += r.prompt_tokens or 0
                t["completion_tokens"] += r.completion_tokens or 0
                t["total_tokens"] += r.total_tokens

    def rank(d: dict[str, dict], key: str) -> list[dict]:
        return sorted(({key: k, **v} for k, v in d.items()),
                      key=lambda x: -x["total_tokens"])

    return {
        "total": total,
        "by_node": rank(by_node, "node"),
        "by_model": rank(by_model, "model"),
        "by_day": sorted(({"day": k, **v} for k, v in by_day.items()),
                         key=lambda x: x["day"]),
    }
