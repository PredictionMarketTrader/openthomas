#!/usr/bin/env python3
"""Export the trade journal as a fine-tuning dataset (JSONL).

One row per settled market: the first forecast made (no hindsight), the market
context at forecast time, and the settled outcome. Rows are ordered by time so
downstream training can do a temporal split (train on past, validate on the
most recent slice) — never split randomly.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

QUERY = """
SELECT f.ts, f.market_id, f.platform, f.question, f.category, f.p_raw,
       f.confidence, f.base_rate, f.reasoning, f.model,
       s.outcome, s.pnl
FROM settlements s
JOIN forecasts f ON f.id = (
  SELECT id FROM forecasts WHERE market_id = s.market_id ORDER BY ts LIMIT 1)
ORDER BY f.ts
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", default=str(Path.home() / ".openthomas" / "journal.db"))
    parser.add_argument("--out", default="data/forecasts.jsonl")
    args = parser.parse_args()

    db = sqlite3.connect(args.journal)
    db.row_factory = sqlite3.Row
    rows = db.execute(QUERY).fetchall()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for r in rows:
            f.write(json.dumps({
                "ts": r["ts"],
                "question": r["question"],
                "category": r["category"],
                "platform": r["platform"],
                "forecast": r["p_raw"],
                "confidence": r["confidence"],
                "base_rate": r["base_rate"],
                "reasoning": r["reasoning"],
                "model": r["model"],
                "outcome": 1 if r["outcome"] == "yes" else 0,
                "pnl": r["pnl"],
            }, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} settled forecasts to {out}")
    if len(rows) < 500:
        print("note: <500 samples — prefer the built-in Platt calibration over fine-tuning.")


if __name__ == "__main__":
    main()
