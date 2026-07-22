"""LLM failover status: which endpoint each model node is currently being
served from, and since when.

Written like the heartbeat — best-effort, whole-file, atomic replace — so a
reader (the CLI, the public feed) always sees a complete status, never a
half-written one. Kept separate from the token ledger (llm-usage.jsonl),
which is an append-only history of calls; this is current state — is the
forecaster on its primary endpoint or riding the fallback right now.
"""

from __future__ import annotations

import json
from pathlib import Path

from .usage import now


class FailoverLog:
    def __init__(self, home: Path | str):
        self.path = Path(home) / "failover.json"

    def record(self, node: str, active: str, model: str, reason: str = "") -> None:
        """Called once per transition (never per call) by CompletionClient's
        status_sink — a node's entry is absent or "primary" when healthy."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = json.loads(self.path.read_text()) if self.path.exists() else {}
        except (OSError, json.JSONDecodeError):
            data = {}
        data[node] = {"active": active, "model": model, "reason": reason, "since": now()}
        try:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(self.path)  # atomic: a reader never sees a half-written status
        except OSError:
            pass


def read(home: Path | str) -> dict:
    """{node: {active, model, reason, since}, …} — {} if nothing has ever failed over."""
    path = Path(home) / "failover.json"
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
