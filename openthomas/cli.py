"""OpenThomas CLI: init, scan, run, report, vital."""

from __future__ import annotations

from pathlib import Path

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
    for d in report.degraded:
        console.print(f"  [yellow]FAILOVER[/yellow] {d}")
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
        final = agent.run_forever(on_report=_print_report)
        if final.halted:
            raise typer.Exit(3)  # distinct code so supervisors don't blind-restart


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

    from .report.brier import summarize_skill, weather_skill
    buckets = weather_skill(j)
    if buckets:
        table = Table(title="Weather skill · Brier by station × lead (model vs market)")
        for col in ("station", "lead", "n", "model", "market", "skill"):
            table.add_column(col)
        for b in buckets:
            table.add_row(
                b["station"], b["lead"], str(b["n"]), f"{b['brier_model']:.3f}",
                f"{b['brier_market']:.3f}" if b["brier_market"] is not None else "—",
                f"{b['skill']:+.0%}" if b["skill"] is not None else "—",
            )
        total = summarize_skill(buckets)
        if total:
            table.add_row("[bold]ALL[/bold]", "", str(total["n"]),
                          f"{total['brier_model']:.3f}", f"{total['brier_market']:.3f}",
                          f"{total['skill']:+.0%}" if total["skill"] is not None else "—")
        console.print(table)
        console.print("skill > 0 = beating the market price it traded against.")


@app.command()
def vital(out: str = typer.Option("vital.html", help="output HTML file")):
    """Generate a shareable performance card (like a Polymarket profile page)."""
    from .memory.journal import Journal
    from .report.vital import render_vital

    s = Settings.load()
    path = render_vital(Journal(s.db_path), s, out)
    console.print(f"[green]✓[/green] Wrote {path} — open it in a browser, screenshot, share.")


@app.command()
def dispatch(day: str = typer.Option("", help="build for this UTC date (YYYY-MM-DD)")):
    """Write today's build-in-public field note to the site log and print the
    X-ready line to copy by hand.

    Templated from the journal — no tokens, no auto-posting. `openthomas publish`
    (the site cron) refreshes today's note on its own, so you rarely need this;
    run it to preview the entry or grab the line for X. Today's note stays
    current until the date rolls over, then freezes into the public timeline.
    """
    from .memory.journal import Journal
    from .report.dispatch import LIMIT, ensure_today

    s = Settings.load()
    entry = ensure_today(Journal(s.db_path), s, day=day or None)
    console.print(f"[bold]{entry['title']}[/bold]")
    for para in entry["body"]:
        console.print(para)
    console.print(f"\n[dim]── copy to X  ({len(entry['tweet'])}/{LIMIT}) ──[/dim]")
    console.print(entry["tweet"])


@app.command()
def publish(out: str = typer.Option("site", help="directory to write feed.json into")):
    """Render the public build-in-public feed (openthomas.com reads feed.json)."""
    from .site.feed import publish as write_feed

    s = Settings.load()
    path = write_feed(s, out)
    size = path.stat().st_size
    console.print(f"[green]✓[/green] Wrote {path} ({size:,} bytes)")


@app.command()
def dataset(
    out: str = typer.Option("data/journal.jsonl", help="local JSONL to write"),
    push: bool = typer.Option(False, "--push", help="also upload to Hugging Face"),
):
    """Export the journal as a leak-free training set; optionally publish it.

    Settled markets only, first forecast per market, temporal split carried in
    the rows. See docs/TRAINING.md."""
    from .memory.journal import Journal
    from .train.dataset import build, summary, write_jsonl
    from .train.hub import aliases

    s = Settings.load()
    rows = build(Journal(s.db_path), aliases=aliases(s))
    if not rows:
        console.print("[yellow]No settled markets yet — nothing to export.[/yellow]")
        raise typer.Exit(0)

    stats = summary(rows)
    path = write_jsonl(rows, out)
    console.print(f"[green]✓[/green] Wrote {path} — {stats['rows']} settled markets "
                  f"({stats['train']} train / {stats['validation']} validation)")
    if stats["trainable_rows"] < stats["rows"]:
        console.print(f"[dim]{stats['rows'] - stats['trainable_rows']} rows carry a label but "
                      "no prompt inputs (forecast predates journal archiving) — not trainable.[/dim]")
    if stats["trainable_rows"] < 500:
        console.print(f"[yellow]Only {stats['trainable_rows']} trainable rows.[/yellow] "
                      "Below ~500 a fine-tune memorizes noise; Platt scaling is already on.")

    if push:
        from .train.hub import HubError, push_dataset
        try:
            sha = push_dataset(s, rows)
        except HubError as e:
            console.print(f"[red]✗[/red] {e}")
            raise typer.Exit(1) from None
        from .train.hub import dataset_repo
        console.print(f"[green]✓[/green] Pushed to "
                      f"https://huggingface.co/datasets/{dataset_repo(s)} @ [bold]{sha[:12]}[/bold]")
        console.print("[dim]Cite that revision when you publish an adapter trained on it.[/dim]")


@app.command()
def push_model(
    adapter: str = typer.Option(..., help="directory holding the trained adapter"),
    name: str = typer.Option(..., help="model repo name, e.g. openthomas-lora-12b"),
    base: str = typer.Option(..., help="base model id it adapts"),
    dataset_revision: str = typer.Option(..., help="dataset commit sha it was fit on"),
    eval_json: str = typer.Option(..., "--eval", help="JSON file: brier_base, brier_tuned, n_validation"),
):
    """Publish a trained adapter to Hugging Face, with the evidence that earns it.

    Refuses without held-out numbers and a dataset revision: a model card
    without evidence is a claim we cannot support."""
    import json as _json

    from .train.hub import HubError, push_adapter

    s = Settings.load()
    try:
        evaluation = _json.loads(Path(eval_json).read_text())
        url = push_adapter(s, name, adapter, base, dataset_revision, evaluation)
    except HubError as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1) from None
    console.print(f"[green]✓[/green] Published {url}")


@app.command()
def hindcast(
    days: int = typer.Option(90, help="days of history to load (max 92)"),
    split: str = typer.Option(None, help="ISO date: report learned bias before vs after it (E9)"),
    mos: bool = typer.Option(True, help="also load NBM / GFS MOS baselines for the same days (E5)"),
):
    """Bulk-load leak-free forecast history so the baseline learns station
    bias/sigma immediately instead of over weeks of live settlements.

    Run it daily (scripts/paper_data.sh): the previous-runs archive only
    reaches 92 days back, so the store is the only place the record grows."""
    from datetime import date as _date
    from datetime import timedelta as _td

    from .weather.hindcast import Hindcast
    from .weather.stations import KALSHI_SERIES, STATIONS
    from .weather.verification import VerificationStore

    s = Settings.load()
    store = VerificationStore(s.home / "weather-verification.jsonl")
    hc = Hindcast(store)
    stations = sorted({key for key, _ in KALSHI_SERIES.values()})
    for key in stations:
        station = STATIONS[key]
        try:
            g, st = hc.load_station(station, days)
            console.print(f"[green]✓[/green] {station.obs_id}: +{g} guidance, +{st} settlements")
        except Exception as e:
            console.print(f"[red]✗[/red] {station.obs_id}: {e}")

    if mos:
        from .weather.mos import MosStore
        end = _date.today() - _td(days=1)
        start = end - _td(days=days)
        for source in ("nbm", "gfsmos"):
            ms = MosStore(s.home / f"mos-{source}.jsonl", source, store)
            for key in stations:
                try:
                    n = ms.load_station(STATIONS[key], start, end)
                    if n:
                        console.print(f"[green]✓[/green] {source} "
                                      f"{STATIONS[key].obs_id}: +{n} days")
                except Exception as e:
                    console.print(f"[red]✗[/red] {source} {STATIONS[key].obs_id}: {e}")

    table = Table(title="Baseline verification stats (bias / sigma / n)")
    table.add_column("station")
    for lead in range(1, 6):
        table.add_column(f"high L{lead}")
    for key in stations:
        row = [STATIONS[key].obs_id]
        for lead in range(1, 6):
            bias, sigma, n = store.stats(key, "high", lead)
            row.append(f"{bias:+.1f}/{sigma:.1f}/n={n}")
        table.add_row(*row)
    console.print(table)

    if split:
        from .weather.ablation import bias_stability
        t2 = Table(title=f"Learned bias, lead 1: before vs from {split} (E9)")
        for col in ("station", "kind", "n before", "bias before", "n after", "bias after",
                    "same sign"):
            t2.add_column(col)
        for series, (key, kind) in sorted(KALSHI_SERIES.items()):
            r = bias_stability(store, key, kind, 1, _date.fromisoformat(split))
            t2.add_row(STATIONS[key].obs_id, kind, str(r["n_before"]), f"{r['bias_before']:+.2f}",
                       str(r["n_after"]), f"{r['bias_after']:+.2f}", str(r["same_sign"]))
        console.print(t2)


def _replay_window(days: int, start: str | None, end: str | None):
    from datetime import date as _date

    from .weather.replay import window
    return window(days, _date.fromisoformat(start) if start else None,
                  _date.fromisoformat(end) if end else None)


@app.command()
def replay(
    days: int = typer.Option(21, help="settled days to replay (ignored when --start is given)"),
    start: str = typer.Option(None, help="ISO date: first settlement day of the window"),
    end: str = typer.Option(None, help="ISO date: last settlement day (default: yesterday)"),
    min_edge: float = typer.Option(0.08, help="required edge after fees"),
    weight: float = typer.Option(0.5, help="market-prior blend weight (0 = model only)"),
    rows_out: str = typer.Option(None, help="freeze rows to this JSONL (cite its digest)"),
    rows_in: str = typer.Option(None, help="score a frozen JSONL instead of fetching"),
    source: str = typer.Option("consensus", help="guidance for p_model: consensus | nbm | gfsmos"),
    low_hour: int = typer.Option(None, help="local snapshot hour for lows (default 0)"),
    high_hour: int = typer.Option(None, help="local snapshot hour for highs (default 11)"),
):
    """Replay settled temperature markets against the statistical baseline
    alone (no LLM, no intraday obs) — a conservative expectancy bound.

    A window is two explicit dates; freeze it with --rows-out so every later
    experiment (`openthomas ablate`) scores the same markets."""
    from .markets.kalshi import KalshiConnector
    from .weather.replay import (SNAPSHOT_LOCAL_HOUR, collect_all, dataset_digest,
                                 decide, load_rows, save_rows, summarize)
    from .weather.verification import VerificationStore

    s = Settings.load()
    fee = KalshiConnector().fee
    if rows_in:
        rows = load_rows(rows_in)
        console.print(f"Loaded {len(rows)} frozen rows from {rows_in} "
                      f"(digest {dataset_digest(rows_in)})")
    else:
        store = VerificationStore(s.home / "weather-verification.jsonl")
        w_start, w_end = _replay_window(days, start, end)
        hours = dict(SNAPSHOT_LOCAL_HOUR)
        if low_hour is not None:
            hours["low"] = low_hour
        if high_hour is not None:
            hours["high"] = high_hour
        guidance_source = None
        if source != "consensus":
            from .weather.mos import MosStore
            guidance_source = MosStore(s.home / f"mos-{source}.jsonl", source, store)
        with console.status(f"collecting {w_start} → {w_end} from Kalshi ({source})…"):
            rows = collect_all(store, start=w_start, end=w_end, snapshot_hours=hours,
                               guidance_source=guidance_source)
        console.print(f"Collected {len(rows)} rows · {w_start} → {w_end} · snapshots {hours}")
        if rows_out:
            path = save_rows(rows, rows_out)
            console.print(f"[green]✓[/green] Frozen to {path} (digest {dataset_digest(path)})")

    by_series: dict[str, list] = {}
    for r in rows:
        by_series.setdefault(r.ticker.split("-")[0], []).append(r)
    table = Table(title=f"Baseline-only replay · min_edge={min_edge:.02f} · w={weight:.02f}")
    for col in ("series", "trades", "win rate", "pnl/contract", "total"):
        table.add_column(col)
    all_trades = []
    for series, srows in sorted(by_series.items()):
        trades = decide(srows, fee, min_edge, weight)
        all_trades += trades
        s_ = summarize(trades)
        if s_["n"]:
            table.add_row(series, str(s_["n"]), f"{s_['win_rate']:.0%}",
                          f"${s_['pnl_per_contract']:+.3f}", f"${s_['total_pnl']:+.2f}")
    total = summarize(all_trades, ci=True)
    if total["n"]:
        table.add_row("[bold]ALL[/bold]", str(total["n"]), f"{total['win_rate']:.0%}",
                      f"${total['pnl_per_contract']:+.3f}", f"${total['total_pnl']:+.2f}")
        console.print(table)
        lo, hi = total["ci95_pnl_per_contract"]
        console.print(f"PnL/contract 95% CI (day bootstrap): [{lo:+.3f}, {hi:+.3f}] · "
                      f"days {total['days']} · t={total.get('t_stat', float('nan')):.2f} · "
                      f"max drawdown ${total['max_drawdown']:.2f}")
        console.print(f"Priced edge kept after settlement: "
                      f"{total['pnl_per_contract'] / max(total['avg_edge_priced'], 1e-9):.0%} "
                      f"of the {total['avg_edge_priced']:.03f} average modeled edge.")
    else:
        console.print("No replay trades cleared the edge bar — run `openthomas hindcast` first?")


@app.command()
def ablate(
    rows_in: str = typer.Option(..., help="frozen replay rows (from `replay --rows-out`)"),
    out: str = typer.Option(None, help="write results JSON here (and a .md next to it)"),
    min_edge: float = typer.Option(0.08, help="production edge bar"),
    weight: float = typer.Option(0.5, help="production market-prior weight"),
    llm_deltas: str = typer.Option(None, help="E4: anchor clamps, e.g. 0,0.05,0.1,0.15,0.25,1"),
    llm_sample: int = typer.Option(0, help="E4: score a deterministic stride sample of N rows "
                                           "(0 = all; a reasoning model costs ~1 min/row)"),
    sources: str = typer.Option(None, help="E5: frozen row files priced from other sources"),
    simulate: bool = typer.Option(True, help="E7: bankroll simulation through the risk engine"),
    bankroll: float = typer.Option(1000.0, help="E7 starting bankroll"),
):
    """Run the paper's offline experiments on ONE frozen dataset
    (docs/EXPERIMENTS.md): E1 ablation + selectivity control, E2 grid, E6
    breakdowns, and optionally E4 (LLM clamp sweep), E5 (NBM / GFS MOS
    baselines) and E7 (sizing regimes)."""
    import json as _json
    from pathlib import Path as _Path

    from .markets.kalshi import KalshiConnector
    from .weather.ablation import render_markdown, run_all
    from .weather.replay import dataset_digest, decide, load_rows, summarize

    s = Settings.load()
    fee = KalshiConnector().fee
    rows = load_rows(rows_in)
    results = run_all(rows, fee, min_edge, weight)
    results["dataset"] = {"path": rows_in, "digest": dataset_digest(rows_in)}
    results["generated"] = __import__("datetime").datetime.now().isoformat(timespec="seconds")

    if sources:
        results["sources"] = {}
        for path in sources.split(","):
            path = path.strip()
            srows = load_rows(path)
            name = srows[0].source if srows else path
            common = {r.ticker for r in rows} & {r.ticker for r in srows}
            paired = [r for r in srows if r.ticker in common]
            base = [r for r in rows if r.ticker in common]
            results["sources"][name] = {
                "path": path, "digest": dataset_digest(path), "rows": len(srows),
                "paired_rows": len(common),
                "this_source": summarize(decide(paired, fee, min_edge, weight), ci=True),
                "consensus_on_same_rows": summarize(decide(base, fee, min_edge, weight), ci=True),
                "brier_source": _brier(paired), "brier_consensus": _brier(base),
            }

    if llm_deltas:
        from .improve.forecast_replay import ForecastReplayer
        from .improve.genome import active_params, params_from_settings
        from .kernel.gate import sample_rows
        from .memory.usage import UsageLedger
        params = {**params_from_settings(s), **active_params(s.home)}
        replayer = ForecastReplayer(
            s.forecaster, s.home / "llm-replay-cache.jsonl",
            decision_params={"risk.min_edge": min_edge, "risk.market_prior_weight": weight},
            fee_fn=fee, usage_sink=UsageLedger(s.home).record)
        template = params.get("forecast_prompt")
        llm_rows = sample_rows(rows, llm_sample) if llm_sample else rows
        results["llm"] = {"model": s.forecaster.model, "rows": len(llm_rows),
                          "sample": "stride" if llm_sample else "all",
                          "baseline_on_sample": summarize(decide(llm_rows, fee, min_edge, weight),
                                                          ci=True),
                          "baseline_brier": _brier(llm_rows), "deltas": []}
        for d in llm_deltas.split(","):
            delta = float(d)
            with console.status(f"LLM-in-replay, clamp ±{delta:.2f} over {len(llm_rows)} rows…"):
                trades, pairs = replayer.strategy(template, delta)(llm_rows)
            from .forecast.calibration import brier_score
            results["llm"]["deltas"].append({
                "delta": delta, "brier": brier_score(pairs) if pairs else None,
                "n_pairs": len(pairs), **summarize(trades, ci=True)})
            console.print(f"  ±{delta:.2f}: n={len(trades)} "
                          f"PnL/ct={summarize(trades).get('pnl_per_contract', 0):+.3f} "
                          f"Brier={results['llm']['deltas'][-1]['brier']}")

    if simulate:
        from .risk.simulate import compare
        production = decide(rows, fee, min_edge, weight)
        results["simulation"] = compare(production, s.risk, bankroll)

    md = render_markdown(results)
    if out:
        p = _Path(out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json.dumps(results, indent=1, default=str))
        p.with_suffix(".md").write_text(md)
        console.print(f"[green]✓[/green] Wrote {p} and {p.with_suffix('.md')}")
    else:
        console.print(md)


def _brier(rows) -> float | None:
    from .forecast.calibration import brier_score
    pairs = [(r.p_model, 1 if r.outcome_yes else 0) for r in rows]
    return brier_score(pairs) if pairs else None


@app.command(name="push-forecasts")
def push_forecasts_cmd(
    repo: str = typer.Option(None, help="dataset repo id, default <hub.org>/weather-forecasts"),
    replay_dir: str = typer.Option(None, help="directory of frozen replay rows / results to include"),
):
    """Publish the weather desk's as-of forecast record (NWP guidance,
    settlements, own GraphCast/GenCast rows, NBM/MOS baselines) and any frozen
    replay datasets to Hugging Face. Needs a write-scoped HF_TOKEN."""
    from pathlib import Path as _Path

    from .train.hub import HubError, forecasts_repo, push_forecasts

    s = Settings.load()
    extra = sorted(_Path(replay_dir).glob("*")) if replay_dir else []
    try:
        sha = push_forecasts(s, repo, extra)
    except HubError as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1) from None
    console.print(f"[green]✓[/green] Pushed to https://huggingface.co/datasets/"
                  f"{forecasts_repo(s, repo)} @ [bold]{sha[:12]}[/bold]")


@app.command()
def improve(
    operator: str = typer.Option("decision", help="mutation operator: decision | forecast"),
    days: int = typer.Option(45, help="replay window for the gate"),
    dry_run: bool = typer.Option(False, "--dry-run", help="score candidates, write nothing"),
    history: bool = typer.Option(False, "--history", help="show the generation lineage"),
    export: str = typer.Option(None, help="with --history: also write the lineage as JSON here (E8)"),
):
    """One self-improvement meta-cycle: mine the journal, propose mutations
    (decision numerics, or the forecast prompt via LLM-in-replay), gate them
    on leak-free replay, promote or roll back. See docs/RSI.md — the trading
    loop also runs this on its own cadence after settlements."""
    from .improve.genome import GenerationStore, display_params
    from .improve.loop import Improver

    s = Settings.load()
    if history:
        table = Table(title="Generation lineage")
        for col in ("gen", "parent", "status", "operator", "proposer", "params", "note"):
            table.add_column(col)
        for g in GenerationStore(s.home).all():
            table.add_row(str(g.id), "—" if g.parent is None else str(g.parent),
                          g.status, g.operator, g.proposer,
                          " ".join(f"{k.split('.')[-1]}={v}"
                                   for k, v in display_params(g.params).items()),
                          (g.rationale or g.note)[:60])
        console.print(table)
        if export:
            import json as _json
            from dataclasses import asdict as _asdict
            from pathlib import Path as _Path
            gens = [_asdict(g) for g in GenerationStore(s.home).all()]
            log_path = s.home / "improve-log.jsonl"
            cycles = ([_json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
                      if log_path.exists() else [])
            _Path(export).write_text(_json.dumps({"generations": gens, "meta_cycles": cycles},
                                                 indent=1, default=str))
            console.print(f"[green]✓[/green] Lineage exported to {export} "
                          f"({len(gens)} generations, {len(cycles)} meta-cycles)")
        return

    report = Improver(s).meta_cycle(days=days, dry_run=dry_run, operator=operator)
    console.print(f"Replay rows: {report.rows} · operator: {report.operator}")
    if report.rollback:
        console.print(f"[yellow]ROLLBACK[/yellow] {report.rollback}")
    for c in report.candidates:
        mark = "[green]✓[/green]" if c["verdict"] == "pass" else "[dim]✗[/dim]"
        params = " ".join(f"{k.split('.')[-1]}={v}" for k, v in c["params"].items())
        brier = c["held_in"].get("brier")
        console.print(f"{mark} [{c['proposer']}] {params} · "
                      f"in ${c['held_in'].get('total_pnl', 0):+.2f} / "
                      f"out ${c['held_out'].get('total_pnl', 0):+.2f}"
                      + (f" · Brier {brier:.4f}" if brier is not None else "")
                      + ("" if c["verdict"] == "pass" else f" · {c['verdict']}"))
    if report.promoted is not None:
        console.print(f"[green bold]Promoted generation {report.promoted}[/green bold] — {report.reason}")
    else:
        console.print(report.reason or "champion holds")
    if dry_run:
        console.print("[dim](dry run: nothing written)[/dim]")


@app.command()
def version():
    console.print(f"openthomas {__version__}")


if __name__ == "__main__":
    app()
