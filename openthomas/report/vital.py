"""Shareable performance card ("vital"): a self-contained HTML file users can
screenshot or host — the social proof loop for OpenThomas."""

from __future__ import annotations

from pathlib import Path

from ..config import Settings
from ..forecast.calibration import brier_score
from ..memory.journal import Journal

TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<title>OpenThomas vital</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; background:#0d1117; color:#e6edf3;
         display:flex; justify-content:center; padding:40px; }}
  .card {{ background:#161b22; border:1px solid #30363d; border-radius:16px; padding:32px;
           width:560px; }}
  h1 {{ font-size:18px; margin:0 0 4px; color:#8b949e; font-weight:500; }}
  .value {{ font-size:44px; font-weight:700; margin:0; }}
  .ret {{ font-size:20px; font-weight:600; }}
  .pos {{ color:#3fb950; }} .neg {{ color:#f85149; }}
  .grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-top:24px; }}
  .stat {{ background:#0d1117; border-radius:10px; padding:12px; }}
  .stat .k {{ font-size:11px; color:#8b949e; text-transform:uppercase; letter-spacing:.05em; }}
  .stat .v {{ font-size:20px; font-weight:600; margin-top:4px; }}
  svg {{ margin-top:24px; width:100%; }}
  .foot {{ margin-top:20px; font-size:12px; color:#8b949e; display:flex;
           justify-content:space-between; }}
</style></head><body><div class="card">
<h1>OpenThomas · {mode} trading · {platforms}</h1>
<p class="value">${value:,.2f}</p>
<p class="ret {ret_class}">{ret:+.2%} <span style="color:#8b949e;font-weight:400">
since ${bankroll:,.0f} start</span></p>
{sparkline}
<div class="grid">
  <div class="stat"><div class="k">settled trades</div><div class="v">{n}</div></div>
  <div class="stat"><div class="k">win rate</div><div class="v">{win_rate:.0%}</div></div>
  <div class="stat"><div class="k">brier score</div><div class="v">{brier}</div></div>
  <div class="stat"><div class="k">avg win</div><div class="v">${avg_win:,.2f}</div></div>
  <div class="stat"><div class="k">avg loss</div><div class="v">${avg_loss:,.2f}</div></div>
  <div class="stat"><div class="k">max drawdown</div><div class="v">{max_dd:.1%}</div></div>
</div>
<div class="foot"><span>github.com/PredictionMarketTrader/openthomas</span><span>{span}</span></div>
</div></body></html>"""


def _sparkline(curve: list[tuple[str, float]], width: int = 496, height: int = 80) -> str:
    if len(curve) < 2:
        return ""
    values = [v for _, v in curve]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    points = " ".join(
        f"{i / (len(values) - 1) * width:.1f},{height - (v - lo) / span * height:.1f}"
        for i, v in enumerate(values)
    )
    color = "#3fb950" if values[-1] >= values[0] else "#f85149"
    return (f'<svg viewBox="0 0 {width} {height}">'
            f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{points}"/></svg>')


def max_drawdown(curve: list[tuple[str, float]]) -> float:
    peak, worst = float("-inf"), 0.0
    for _, v in curve:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, 1 - v / peak)
    return worst


def render_vital(journal: Journal, settings: Settings, out: str) -> Path:
    stats = journal.settlement_stats()
    curve = journal.equity_curve()
    value = curve[-1][1] if curve else settings.bankroll
    pairs = journal.forecast_outcome_pairs()
    span = f"{curve[0][0][:10]} → {curve[-1][0][:10]}" if curve else "no cycles yet"
    ret = value / settings.bankroll - 1
    html = TEMPLATE.format(
        mode=settings.mode, platforms=" + ".join(settings.platforms),
        value=value, ret=ret, ret_class="pos" if ret >= 0 else "neg",
        bankroll=settings.bankroll, sparkline=_sparkline(curve),
        n=stats["n"], win_rate=stats["win_rate"],
        brier=f"{brier_score(pairs):.3f}" if pairs else "—",
        avg_win=stats["avg_win"], avg_loss=stats["avg_loss"],
        max_dd=max_drawdown(curve), span=span,
    )
    path = Path(out)
    path.write_text(html)
    return path
