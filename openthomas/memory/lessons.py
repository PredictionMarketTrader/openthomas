"""Lesson distillation: turn settled trades into memory the forecaster reads.

Two layers:
1. Hard stats (always): per-category win rate / PnL, calibration summary —
   computed from the journal, no LLM required.
2. Narrative lessons (optional): an LLM reflection pass over recent
   settlements that writes short, falsifiable rules to the lessons file.

The rendered lessons are injected into every forecast prompt, closing the
learn-from-outcomes loop that Prediction Arena showed matters more than
research volume.
"""

from __future__ import annotations

from pathlib import Path

from ..forecast.calibration import brier_score
from .journal import Journal

REFLECT_PROMPT = """You are reviewing your own recent prediction-market settlements as \
OpenThomas. Extract at most 3 SHORT lessons (one line each) that would have changed a \
decision. Rules must be specific and falsifiable ("In 'weather' my YES forecasts run 10 \
points hot — shade down"), never generic ("do better research"). If the sample is too \
small to support a rule, output fewer lessons or none.

Recent settlements (question | category | my p | outcome | pnl):
{rows}

Existing lessons (revise or keep):
{existing}

Output only the updated lesson list, one per line, max 8 lines total."""


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
    def __init__(self, lessons_dir: Path):
        self.path = Path(lessons_dir) / "lessons.md"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> str:
        return self.path.read_text() if self.path.exists() else ""

    def render_for_prompt(self, journal: Journal) -> str:
        parts = [p for p in (stats_block(journal), self.read().strip()) if p]
        return "\n".join(parts)

    def reflect(self, journal: Journal, complete_fn) -> str:
        """`complete_fn(system, user) -> str` — reuse the forecast engine's client."""
        recent = journal.recent_settlements(30)
        if len(recent) < 5:
            return self.read()
        rows = "\n".join(
            f"{s['question'][:70]} | {s['category']} | outcome={s['outcome']} | pnl=${s['pnl']:+.2f}"
            for s in recent
        )
        updated = complete_fn(
            "You distill trading lessons. Be terse and specific.",
            REFLECT_PROMPT.format(rows=rows, existing=self.read() or "(none)"),
        )
        self.path.write_text(updated.strip() + "\n")
        return updated
