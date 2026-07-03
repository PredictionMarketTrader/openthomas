"""OpenThomas CLI: init, scan, run, report, vital."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import ModelConfig, RiskProfile, Settings

app = typer.Typer(
    name="openthomas",
    help="Autonomous AI trading agent for prediction markets (Polymarket, Kalshi).",
    no_args_is_help=True,
)
console = Console()


@app.command()
def init(
    bankroll: float = typer.Option(1000.0, help="USD the agent may deploy"),
    risk: str = typer.Option("conservative", help="conservative | moderate | aggressive"),
    goal: str = typer.Option("Grow the bankroll steadily; protecting capital beats chasing returns."),
    provider: str = typer.Option("anthropic", help="anthropic | openai (incl. local endpoints)"),
    model: str = typer.Option("claude-sonnet-5", help="forecasting model id"),
    base_url: str = typer.Option(None, help="custom endpoint, e.g. http://localhost:11434/v1"),
):
    """Create ~/.openthomas/config.yaml with your bankroll, goal, and risk profile."""
    key_env = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
    settings = Settings(
        bankroll=bankroll, goal=goal, risk=RiskProfile.preset(risk),
        forecaster=ModelConfig(provider=provider, model=model, base_url=base_url,
                               api_key_env=key_env),
    )
    path = settings.save()
    console.print(f"[green]✓[/green] Config written to {path}")
    console.print(f"  bankroll ${bankroll:,.0f} · risk={risk} · model={model}")
    console.print("  Mode is [bold]paper[/bold] (simulated fills on real prices). "
                  "Run [bold]openthomas run[/bold] to start.")


@app.command()
def scan(limit: int = typer.Option(20, help="max rows to show")):
    """Scan live markets and show tradeable candidates + cross-platform arbs."""
    from .agent.loop import build_connectors
    from .edge.scanner import EdgeScanner

    s = Settings.load()
    markets = []
    for connector in build_connectors(s.platforms).values():
        with console.status(f"fetching {connector.platform} markets…"):
            try:
                markets += connector.list_markets(limit=150)
            except Exception as e:
                console.print(f"[red]{connector.platform}: {e}[/red]")
    result = EdgeScanner(s.risk).scan(markets)

    table = Table(title=f"Candidates ({len(result.candidates)} of {len(markets)} markets pass filters)")
    for col in ("platform", "question", "bid", "ask", "vol 24h", "closes in"):
        table.add_column(col)
    for m in result.candidates[:limit]:
        hours = m.hours_to_close()
        table.add_row(
            m.platform, m.question[:70],
            f"{m.yes_bid:.2f}" if m.yes_bid is not None else "—",
            f"{m.yes_ask:.2f}" if m.yes_ask is not None else "—",
            f"${m.volume_24h:,.0f}",
            f"{hours:.0f}h" if hours is not None else "—",
        )
    console.print(table)
    console.print(f"Skipped: {result.skipped}")
    if result.arbs:
        console.print("\n[bold]Cross-platform arbitrage candidates[/bold] (verify resolution rules!)")
        for arb in result.arbs[:10]:
            console.print(f"  {arb.describe()}")


def _print_report(report) -> None:
    console.rule(f"cycle · account ${report.account_value:,.2f} · cash ${report.cash:,.2f}")
    console.print(f"markets {report.markets_seen} → candidates {report.candidates} "
                  f"→ forecasts {report.forecasts} → trades {len(report.trades)}")
    for t in report.trades:
        console.print(f"  [green]TRADE[/green] {t}")
    for s_ in report.settlements:
        console.print(f"  [cyan]SETTLED[/cyan] {s_}")
    for a in report.arbs:
        console.print(f"  [magenta]ARB?[/magenta] {a}")
    for r in report.rejections[:8]:
        console.print(f"  [dim]skip: {r}[/dim]")
    if report.halted:
        console.print("[red bold]KILL-SWITCH: max drawdown reached. Trading halted — "
                      "review the journal, then delete peak_value to resume.[/red bold]")


@app.command()
def run(
    once: bool = typer.Option(False, "--once", help="run a single cycle and exit"),
    live: bool = typer.Option(False, "--live", help="trade with real money (default: paper)"),
):
    """Run the trading loop (paper mode by default)."""
    from .agent.loop import Agent

    s = Settings.load()
    if live:
        if s.mode != "live":
            console.print("[red]Refusing --live: set `mode: live` in ~/.openthomas/config.yaml "
                          "as well, so live trading requires two explicit steps.[/red]")
            raise typer.Exit(1)
        console.print("[yellow bold]LIVE MODE — real money.[/yellow bold]")
    else:
        s.mode = "paper"
    agent = Agent(s)
    if once:
        _print_report(agent.cycle())
    else:
        console.print(f"Trading loop started · every {s.cycle_minutes}m · Ctrl-C to stop")
        agent.run_forever(on_report=_print_report)


@app.command()
def report():
    """Performance summary: PnL, win rate, calibration, per-category stats."""
    from .forecast.calibration import brier_score, calibration_table
    from .memory.journal import Journal

    s = Settings.load()
    j = Journal(s.db_path)
    stats = j.settlement_stats()
    curve = j.equity_curve()
    value = curve[-1][1] if curve else s.bankroll
    console.print(f"[bold]Account value:[/bold] ${value:,.2f}  "
                  f"(start ${s.bankroll:,.2f}, {(value / s.bankroll - 1):+.1%})")
    console.print(f"Settled: {stats['n']} · win rate {stats['win_rate']:.0%} · "
                  f"avg win ${stats['avg_win']:.2f} / avg loss ${stats['avg_loss']:.2f}")
    pairs = j.forecast_outcome_pairs()
    if pairs:
        console.print(f"Brier score: {brier_score(pairs):.3f} (0.25 = coin flip, lower is better)")
        table = Table(title="Calibration")
        for col in ("forecast", "n", "observed"):
            table.add_column(col)
        for row in calibration_table(pairs):
            if row["n"]:
                table.add_row(row["bucket"], str(row["n"]),
                              f"{row['observed']:.0%}" if row["observed"] is not None else "—")
        console.print(table)
    cats = j.category_stats()
    if cats:
        table = Table(title="By category")
        for col in ("category", "settled", "win rate", "pnl"):
            table.add_column(col)
        for c in cats:
            table.add_row(c["category"] or "—", str(c["n"]),
                          f"{c['win_rate']:.0%}", f"${c['pnl']:+.2f}")
        console.print(table)


@app.command()
def vital(out: str = typer.Option("vital.html", help="output HTML file")):
    """Generate a shareable performance card (like a Polymarket profile page)."""
    from .memory.journal import Journal
    from .report.vital import render_vital

    s = Settings.load()
    path = render_vital(Journal(s.db_path), s, out)
    console.print(f"[green]✓[/green] Wrote {path} — open it in a browser, screenshot, share.")


@app.command()
def version():
    console.print(f"openthomas {__version__}")


if __name__ == "__main__":
    app()
