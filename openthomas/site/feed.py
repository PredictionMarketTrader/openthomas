"""The public feed: what openthomas.com publishes about the running agent.

Build in public, trade in public. The agent's private state is a journal, a
lineage archive, and a token ledger; this module projects those into one
JSON document a static site can render — the positions we hold, the edges we
think we've found and why, what the evolution loop is currently trying to
improve, and what the compute cost.

Two rules govern what lands here:

1. **Whitelist, never dump.** Every field is named explicitly below. The
   journal holds prompt inputs, news text, and venue identifiers that have no
   business on a public page, and a `SELECT *` reaching the feed would be a
   leak, not a bug in rendering.
2. **Never guess.** Account value comes from the last recorded cycle, not from
   a fresh mark-to-market — publishing must not depend on venue APIs being up,
   and a stale-but-true number beats a fresh-but-invented one. `as_of` says
   when that number was real.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import Settings
from ..forecast.calibration import brier_score
from ..improve.genome import GenerationStore, display_params
from ..memory import board as board_store
from ..memory import heartbeat
from ..memory.journal import Journal
from ..memory.usage import UsageLedger, summarize
from ..report.vital import max_drawdown
from ..weather.geo import locate
from ..weather.temps import global_grid

SCHEMA_VERSION = 2


def _downsample(curve: list[tuple[str, float]], limit: int) -> list[list]:
    """Keep the shape and both endpoints; the site plots a sparkline, not a tape."""
    if len(curve) <= limit:
        keep = curve
    else:
        stride = len(curve) / limit
        idx = sorted({int(i * stride) for i in range(limit)} | {len(curve) - 1})
        keep = [curve[i] for i in idx]
    return [[ts, round(v, 2)] for ts, v in keep]


def _thesis(row: dict, settings: Settings, status: str) -> dict:
    """One market view, stripped to what a reader needs to judge it later.

    `mid` is the market's price when we formed the view; freezing it here is
    the point — a claimed edge is only checkable against the price that was
    actually on offer at the time.
    """
    mid = row.get("mid")
    p = row["p_calibrated"]
    edge = None if mid is None else p - mid
    reasoning = (row.get("reasoning") or "").strip()
    cap = settings.site.max_reasoning_chars
    return {
        "ts": row["ts"],
        "status": status,  # held | pending | passed
        "platform": row["platform"],
        "question": row["question"],
        "category": row["category"] or "",
        "side": None if edge is None else ("yes" if edge > 0 else "no"),
        "p_model": round(p, 4),
        "p_raw": round(row["p_raw"], 4),
        "p_market": None if mid is None else round(mid, 4),
        "edge": None if edge is None else round(edge, 4),
        "confidence": round(row["confidence"], 3),
        "base_rate": row["base_rate"],
        "why": row.get("market_gap_reason") or "",
        "invalidation": row.get("invalidation") or "",
        "reasoning": reasoning[:cap] + ("…" if len(reasoning) > cap else ""),
        "model": row.get("model") or "",
        # Where the weather is — for the globe. Derived from the market, never
        # the returned id, so no venue handle leaks.
        "loc": locate(row.get("market_id"), row["platform"], row["question"]),
    }


def _theses(journal: Journal, settings: Settings) -> list[dict]:
    """Live market views: what we hold, and what currently clears the edge bar.

    A forecast is `pending` only while it is still actionable — inside the
    staleness window and not yet traded or settled. Yesterday's untaken edge is
    not a bet we are "about to place", and publishing it as one would be a
    claim we never made.
    """
    held = {p.market_id for p in journal.positions()}
    traded, settled = journal.traded_market_ids(), journal.settled_market_ids()
    fresh = datetime.now(timezone.utc) - timedelta(hours=24)

    out = []
    for row in journal.recent_forecasts(limit=200):
        mid, mkt = row.get("mid"), row["market_id"]
        if mkt in held:
            status = "held"
        elif mkt in settled or mkt in traded:
            continue  # closed book: it belongs in the track record, not the outlook
        elif (mid is not None
              and abs(row["p_calibrated"] - mid) >= settings.risk.min_edge
              and datetime.fromisoformat(row["ts"]) >= fresh):
            status = "pending"
        else:
            continue
        out.append(_thesis(row, settings, status))

    rank = {"held": 0, "pending": 1}
    out.sort(key=lambda t: (rank[t["status"]], -abs(t["edge"] or 0)))
    return out[: settings.site.max_theses]


def _board(journal: Journal, settings: Settings) -> dict:
    """Every weather market the agent is watching, placed on the globe with our
    view attached: which we forecast, hold, or have already won or lost.

    Prefers the live snapshot the trading loop writes each cycle (the whole
    book, with real prices); falls back to the markets we have forecast when no
    snapshot exists yet, so the globe is never empty. Market ids are the join
    key and stay server-side — only place, price, and our published view ship.
    Plain markets (no view of ours) carry price only; ours carry the analysis.
    """
    snap = board_store.read(settings.home) or {}
    rows = snap.get("markets")
    from_snapshot = rows is not None
    if not rows:  # no snapshot yet: plot the markets we have opinions on
        seen: set[str] = set()
        rows = []
        for r in journal.recent_forecasts(limit=400):
            if r["market_id"] in seen:
                continue
            seen.add(r["market_id"])
            rows.append({"id": r["market_id"], "platform": r["platform"],
                         "question": r["question"], "category": r["category"],
                         "yes_bid": None, "yes_ask": None, "mid": r.get("mid"),
                         "volume_24h": None, "close_time": None})

    positions = {p.market_id: p for p in journal.positions()}
    settled = {s["market_id"]: s for s in journal.recent_settlements(limit=200)}
    latest: dict[str, dict] = {}
    for r in journal.recent_forecasts(limit=400):  # DESC ts → first seen is newest
        latest.setdefault(r["market_id"], r)

    fresh = datetime.now(timezone.utc) - timedelta(hours=24)
    cap = settings.site.max_reasoning_chars
    out = []
    for m in rows:
        loc = locate(m.get("id"), m["platform"], m["question"])
        if loc is None:
            continue  # can't place it → no pin
        mid = m.get("mid")
        if mid is None and m.get("yes_bid") is not None and m.get("yes_ask") is not None:
            mid = (m["yes_bid"] + m["yes_ask"]) / 2
        f, s, pos = latest.get(m["id"]), settled.get(m["id"]), positions.get(m["id"])
        p_model = round(f["p_calibrated"], 4) if f else None
        edge = None if (p_model is None or mid is None) else round(p_model - mid, 4)

        if s is not None:
            state = "won" if s["pnl"] >= 0 else "lost"
        elif pos is not None:
            state = "held"
        elif (f and edge is not None and abs(edge) >= settings.risk.min_edge
              and datetime.fromisoformat(f["ts"]) >= fresh):
            state = "pending"
        else:
            state = "market"

        entry = {
            "loc": loc, "place": loc["place"], "platform": m["platform"],
            "question": m["question"], "state": state,
            "mid": None if mid is None else round(mid, 4),
            "yes_bid": m.get("yes_bid"), "yes_ask": m.get("yes_ask"),
            "volume": m.get("volume_24h"),
            "close": m.get("close_time"),
        }
        if state != "market":  # our view — kept lean for plain book markets
            reasoning = (f.get("reasoning") if f else "") or ""
            entry.update({
                "p_model": p_model, "edge": edge,
                "side": None if edge is None else ("yes" if edge > 0 else "no"),
                "why": (f.get("market_gap_reason") if f else "") or "",
                "reasoning": reasoning[:cap] + ("…" if len(reasoning) > cap else ""),
                "outcome": s["outcome"] if s else None,
                "pnl": None if s is None else round(s["pnl"], 2),
            })
        out.append(entry)

    rank = {"held": 0, "pending": 1, "won": 2, "lost": 3, "market": 4}
    out.sort(key=lambda e: (rank[e["state"]], -abs(e.get("edge") or 0)))
    return {"from_snapshot": from_snapshot, "as_of": snap.get("ts"),
            "markets": out[: settings.site.max_board]}


def _performance(journal: Journal, settings: Settings) -> dict:
    curve = journal.equity_curve()
    stats = journal.settlement_stats()
    pairs = journal.forecast_outcome_pairs()
    value = curve[-1][1] if curve else settings.bankroll
    return {
        "as_of": curve[-1][0] if curve else None,
        "account_value": round(value, 2),
        "bankroll": settings.bankroll,
        "return_pct": round(value / settings.bankroll - 1, 6) if settings.bankroll else 0.0,
        "peak_value": round(journal.peak_value(), 2),
        "max_drawdown": round(max_drawdown(curve), 6),
        "cycles": len(curve),
        "settled_trades": stats["n"],
        "realized_pnl": round(stats["pnl"], 2),
        "win_rate": round(stats["win_rate"], 4),
        "avg_win": round(stats["avg_win"], 2),
        "avg_loss": round(stats["avg_loss"], 2),
        "brier": round(brier_score(pairs), 4) if pairs else None,
        "forecasts_scored": len(pairs),
        "equity_curve": _downsample(curve, settings.site.max_curve_points),
    }


def _track_record(journal: Journal) -> list[dict]:
    return [
        {"ts": s["ts"], "platform": s["platform"], "question": s["question"],
         "category": s["category"] or "", "outcome": s["outcome"],
         "pnl": round(s["pnl"], 2), "cost_basis": round(s["cost_basis"], 2),
         "loc": locate(s["market_id"], s["platform"], s["question"])}
        for s in journal.recent_settlements(limit=25)
    ]


def _rsi(settings: Settings) -> dict:
    """What the evolution loop is trying to improve, and what it has proven.

    An empty lineage is the honest answer before the first meta-cycle: the
    operator's config is in force and nothing has been promoted past it.
    """
    gens = GenerationStore(settings.home).all()
    active = next((g for g in gens if g.status == "active"), None)
    log_path = settings.home / "improve-log.jsonl"
    meta_cycles = []
    if log_path.exists():
        for line in log_path.read_text().splitlines()[-20:]:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            meta_cycles.append({
                "ts": entry.get("ts"), "operator": entry.get("operator"),
                "replay_rows": entry.get("rows"), "promoted": entry.get("promoted"),
                "rollback": entry.get("rollback") or "", "reason": entry.get("reason") or "",
                "candidates": [
                    {"proposer": c.get("proposer"), "verdict": c.get("verdict"),
                     "params": display_params(c.get("params") or {}),
                     "pnl_in": (c.get("held_in") or {}).get("total_pnl"),
                     "pnl_out": (c.get("held_out") or {}).get("total_pnl"),
                     "brier": (c.get("held_in") or {}).get("brier")}
                    for c in entry.get("candidates") or []
                ],
            })
    return {
        "active_generation": None if active is None else {
            "id": active.id, "parent": active.parent, "operator": active.operator,
            "proposer": active.proposer, "created": active.created,
            "rationale": active.rationale, "evidence": active.evidence,
            "scores": active.scores, "params": display_params(active.params),
        },
        "generations": [
            {"id": g.id, "parent": g.parent, "status": g.status, "operator": g.operator,
             "proposer": g.proposer, "created": g.created,
             "rationale": g.rationale or g.note}
            for g in gens
        ],
        "meta_cycles": list(reversed(meta_cycles)),
    }


def _status(settings: Settings) -> dict:
    """Is the loop alive, and when did it last act? Straight from the heartbeat.

    `last_cycle` is the honest liveness signal — the site decides "live" from
    how recent it is, rather than this builder asserting a state it cannot see.
    An empty beat is the truthful answer before the loop's first cycle.
    """
    beat = heartbeat.read(settings.home) or {}
    return {
        "run_started": beat.get("run_started"),
        "last_cycle": beat.get("last_cycle"),
        "cycles_this_run": beat.get("cycles_this_run") or 0,
    }


def _compute(settings: Settings, journal: Journal, status: dict) -> dict:
    """Token spend — all-time, and just this run — with the facts that keep a
    small number from lying.

    `ledger_started` dates the accounting: the agent forecast for weeks before
    it counted tokens, and a reader seeing "0 tokens" deserves to know whether
    that means "cheap" or "not measured yet". `session` is spend since the
    current process started (see status.run_started), so a reader can watch
    what this run costs without doing the subtraction. `forecasts_recorded` is
    the journal's own count, which predates the ledger.
    """
    rows = UsageLedger(settings.home).read()
    started = status.get("run_started")
    session_rows = [r for r in rows if started and r.ts >= started]
    return {
        **summarize(rows),
        "session": {**summarize(session_rows), "since": started},
        "ledger_started": min((r.ts for r in rows), default=None),
        "forecasts_recorded": journal.forecast_count(),
    }


def build_feed(settings: Settings, journal: Journal | None = None) -> dict:
    journal = journal or Journal(settings.db_path)
    status = _status(settings)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "agent": {
            "mode": settings.mode,
            "focus": settings.focus,
            "platforms": settings.platforms,
            "goal": settings.goal,
            "risk_profile": settings.risk.name,
            "forecaster": {
                "label": settings.site.model_label or settings.forecaster.model,
                "url": settings.site.model_url,
            },
            "cycle_minutes": settings.cycle_minutes,
            "min_edge": settings.risk.min_edge,
            "kelly_fraction": settings.risk.kelly_fraction,
            "market_prior_weight": settings.risk.market_prior_weight,
        },
        "performance": _performance(journal, settings),
        "temperature": global_grid(settings.home),
        "board": _board(journal, settings),
        "positions": [
            {"platform": p.platform, "question": p.question, "category": p.category,
             "side": p.side.value, "qty": p.qty, "avg_cost": round(p.avg_cost, 4),
             "cost_basis": round(p.cost_basis, 2),
             "loc": locate(p.market_id, p.platform, p.question)}
            for p in journal.positions()
        ],
        "theses": _theses(journal, settings),
        "track_record": _track_record(journal),
        "rsi": _rsi(settings),
        "compute": _compute(settings, journal, status),
        "links": {
            "github": settings.site.github,
            "huggingface": settings.site.huggingface,
            "x": f"https://x.com/{settings.site.x_handle}" if settings.site.x_handle else "",
        },
    }


def publish(settings: Settings, out_dir: str | Path) -> Path:
    """Write feed.json atomically — the site reads this file while we rewrite it."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "feed.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(build_feed(settings), indent=1, ensure_ascii=False) + "\n")
    tmp.replace(path)
    return path
