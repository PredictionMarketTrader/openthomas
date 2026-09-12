"""Offline experiments over a frozen replay dataset (docs/EXPERIMENTS.md).

Everything here is pure: rows in, numbers out. The rows come from
`weather/replay.py::collect_rows` (kernel plane, network) and are frozen to
JSONL once; each experiment re-scores the SAME rows, so results across
experiments and across days are comparable and reproducible from the file's
digest.

The experiments answer the reviewer's questions in order:

- E1 ablation: naked model → + station bias → + market blend, PLUS the
  control the thesis needs — the un-blended model with its edge bar raised
  until it trades as rarely as the blend. If the control also turns
  positive, *selectivity* is the edge; if only the blend does, the edge is a
  better probability (the blend), and the paper's claim changes.
- E2 grid: min_edge × market_prior_weight, so one lucky cell cannot carry
  the story.
- E6 breakdown: per station and per ISO week — is the edge everywhere, or
  one hot station in one hot week?
- E9 bias stability: the learned station bias on the first vs second half
  of the hindcast window, so "persists out-of-sample" is a number.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Callable

from .replay import ReplayRow, ReplayTrade, decide, summarize

FeeFn = Callable[[float, int], float]

EDGE_GRID = (0.02, 0.04, 0.06, 0.08, 0.10, 0.12, 0.15, 0.20)
WEIGHT_GRID = (0.0, 0.25, 0.5, 0.75)
CONTROL_EDGES = tuple(x / 1000 for x in range(20, 601, 5))  # 0.020 … 0.600


def rows_without_bias(rows: list[ReplayRow]) -> list[ReplayRow] | None:
    """The same strikes priced with no learned station bias, or None if the
    rows predate the field (older frozen datasets)."""
    if any(r.p_model_raw is None for r in rows):
        return None
    return [replace(r, p_model=r.p_model_raw) for r in rows]


def threshold_matched(rows: list[ReplayRow], fee_fn: FeeFn, target_n: int,
                      market_prior_weight: float = 0.0,
                      edges: tuple[float, ...] = CONTROL_EDGES) -> tuple[float, list[ReplayTrade]]:
    """Raise min_edge (blend fixed) until the rule trades at most `target_n`
    times; returns (min_edge, trades). The selectivity control for E1."""
    best_edge, best = edges[-1], []
    for edge in edges:
        trades = decide(rows, fee_fn, min_edge=edge, market_prior_weight=market_prior_weight)
        best_edge, best = edge, trades
        if len(trades) <= target_n:
            break
    return best_edge, best


def ablation(rows: list[ReplayRow], fee_fn: FeeFn, min_edge: float = 0.08,
             market_prior_weight: float = 0.5, ci: bool = True) -> list[dict]:
    """E1. Each entry: {"rule", "min_edge", "weight", "bias", **summary}."""
    out: list[dict] = []

    def add(rule: str, trades: list[ReplayTrade], edge: float, weight: float, bias: bool,
            note: str = "") -> None:
        out.append({"rule": rule, "min_edge": edge, "weight": weight, "bias": bias,
                    "note": note, **summarize(trades, ci=ci)})

    raw = rows_without_bias(rows)
    if raw is None:
        out.append({"rule": "naked model vs market", "min_edge": min_edge, "weight": 0.0,
                    "bias": False, "n": 0,
                    "note": "unavailable: rows predate p_model_raw; re-collect"})
    else:
        add("naked model vs market", decide(raw, fee_fn, min_edge, 0.0), min_edge, 0.0, False)
    add("+ learned station bias", decide(rows, fee_fn, min_edge, 0.0), min_edge, 0.0, True)
    production = decide(rows, fee_fn, min_edge, market_prior_weight)
    add("+ market-prior blend (production)", production, min_edge, market_prior_weight, True)
    # The control: same un-blended model, edge bar raised to the blend's activity.
    edge_c, control = threshold_matched(rows, fee_fn, len(production), 0.0)
    add("control: no blend, edge bar raised to match trade count", control, edge_c, 0.0, True,
        note=f"min_edge searched to n<={len(production)}")
    return out


def grid(rows: list[ReplayRow], fee_fn: FeeFn, edges: tuple[float, ...] = EDGE_GRID,
         weights: tuple[float, ...] = WEIGHT_GRID) -> list[dict]:
    """E2. One record per (min_edge, weight) cell."""
    cells = []
    for w in weights:
        for e in edges:
            s = summarize(decide(rows, fee_fn, min_edge=e, market_prior_weight=w))
            cells.append({"min_edge": e, "weight": w, "n": s.get("n", 0),
                          "pnl_per_contract": s.get("pnl_per_contract"),
                          "total_pnl": s.get("total_pnl"), "win_rate": s.get("win_rate")})
    return cells


def by_group(trades: list[ReplayTrade], key: Callable[[ReplayTrade], str]) -> dict[str, dict]:
    groups: dict[str, list[ReplayTrade]] = {}
    for t in trades:
        groups.setdefault(key(t), []).append(t)
    return {k: summarize(v) for k, v in sorted(groups.items())}


def by_station(trades: list[ReplayTrade]) -> dict[str, dict]:
    return by_group(trades, lambda t: t.station)


def by_week(trades: list[ReplayTrade]) -> dict[str, dict]:
    def week(t: ReplayTrade) -> str:
        y, w, _ = date.fromisoformat(t.day).isocalendar()
        return f"{y}-W{w:02d}"
    return by_group(trades, week)


def by_kind(trades: list[ReplayTrade], rows: list[ReplayRow]) -> dict[str, dict]:
    kind_of = {r.ticker: r.kind for r in rows}
    return by_group(trades, lambda t: kind_of.get(t.ticker, "?"))


def bias_stability(store, station: str, kind: str, lead: int, split: date) -> dict:
    """E9. Learned bias on days < split vs days >= split (no shrinkage, so the
    two halves are directly comparable), and the sign agreement."""
    before = store.errors(station, kind, lead, before=split.isoformat())
    all_errs = store.errors(station, kind, lead)
    after_n = len(all_errs) - len(before)
    mean_b = sum(before) / len(before) if before else float("nan")
    # errors() has no `after`; the complement of `before` within all_errs is
    # not recoverable by value, so recompute from the raw rows.
    after = _errors_from(store, station, kind, lead, split.isoformat())
    mean_a = sum(after) / len(after) if after else float("nan")
    return {"station": station, "kind": kind, "lead": lead, "split": split.isoformat(),
            "n_before": len(before), "bias_before": mean_b,
            "n_after": len(after), "bias_after": mean_a,
            "same_sign": (mean_b * mean_a > 0) if before and after else None,
            "n_after_expected": after_n}


def _errors_from(store, station: str, kind: str, lead: int, since: str) -> list[float]:
    guidance: dict[str, float] = {}
    settled: dict[str, float] = {}
    for r in store._rows():
        if r["station"] != station or r["kind"] != kind or r["target_date"] < since:
            continue
        if r["type"] == "guidance" and r["lead"] == lead:
            guidance[r["target_date"]] = r["mean"]
        elif r["type"] == "settlement":
            settled[r["target_date"]] = r["value"]
    return [settled[d] - guidance[d] for d in guidance.keys() & settled.keys()]


def run_all(rows: list[ReplayRow], fee_fn: FeeFn, min_edge: float = 0.08,
            market_prior_weight: float = 0.5) -> dict:
    """E1, E2, E6 in one record (the CLI adds E4/E5/E7 which need a model,
    another data source, or a risk profile)."""
    production = decide(rows, fee_fn, min_edge, market_prior_weight)
    days = sorted({r.day for r in rows})
    return {
        "rows": len(rows), "days": len(days),
        "window": [days[0], days[-1]] if days else None,
        "stations": sorted({r.station for r in rows}),
        "params": {"min_edge": min_edge, "market_prior_weight": market_prior_weight},
        "ablation": ablation(rows, fee_fn, min_edge, market_prior_weight),
        "grid": grid(rows, fee_fn),
        "by_station": by_station(production),
        "by_week": by_week(production),
        "by_kind": by_kind(production, rows),
    }


# --- rendering ------------------------------------------------------------------

def _fmt_ci(s: dict) -> str:
    ci = s.get("ci95_pnl_per_contract")
    if not ci or ci[0] != ci[0]:  # NaN
        return "—"
    return f"[{ci[0]:+.3f}, {ci[1]:+.3f}]"


def render_markdown(results: dict) -> str:
    lines = [f"# Replay experiments · {results.get('window')} · {results['rows']} rows · "
             f"{results['days']} days · stations {', '.join(results['stations'])}", ""]
    lines += ["## E1 ablation", "",
              "| rule | min_edge | w | bias | n | win | PnL/ct | 95% CI (day bootstrap) "
              "| total | maxDD | t |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for a in results["ablation"]:
        if not a.get("n"):
            lines.append(f"| {a['rule']} | {a['min_edge']:.3f} | {a['weight']:.2f} | {a['bias']} "
                         f"| 0 | — | — | — | — | — | — |  {a.get('note', '')}")
            continue
        lines.append(
            f"| {a['rule']} | {a['min_edge']:.3f} | {a['weight']:.2f} | {a['bias']} | {a['n']} "
            f"| {a['win_rate']:.0%} | {a['pnl_per_contract']:+.3f} | {_fmt_ci(a)} "
            f"| {a['total_pnl']:+.2f} | {a.get('max_drawdown', float('nan')):.2f} "
            f"| {a.get('t_stat', float('nan')):.2f} |")
    lines += ["", "## E2 grid (PnL/ct, n)", ""]
    weights = sorted({c["weight"] for c in results["grid"]})
    edges = sorted({c["min_edge"] for c in results["grid"]})
    lines.append("| w \\ min_edge | " + " | ".join(f"{e:.2f}" for e in edges) + " |")
    lines.append("|---|" + "---|" * len(edges))
    cell = {(c["weight"], c["min_edge"]): c for c in results["grid"]}
    for w in weights:
        row = [f"{w:.2f}"]
        for e in edges:
            c = cell[(w, e)]
            row.append("—" if not c["n"] else f"{c['pnl_per_contract']:+.3f} ({c['n']})")
        lines.append("| " + " | ".join(row) + " |")
    for title, key in (("E6 by station", "by_station"), ("E6 by week", "by_week"),
                       ("E6 by kind", "by_kind")):
        lines += ["", f"## {title}", "", "| group | n | win | PnL/ct | total |",
                  "|---|---|---|---|---|"]
        for g, s in results[key].items():
            lines.append(f"| {g} | {s['n']} | {s['win_rate']:.0%} | {s['pnl_per_contract']:+.3f} "
                         f"| {s['total_pnl']:+.2f} |")
    for extra in ("llm", "sources", "simulation", "bias_stability", "snapshot_sensitivity"):
        if extra in results:
            lines += ["", f"## {extra}", "", "```json",
                      _dumps(results[extra]), "```"]
    return "\n".join(lines) + "\n"


def _dumps(obj) -> str:
    import json
    return json.dumps(obj, indent=1, default=str)
