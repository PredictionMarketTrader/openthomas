"""The paper's experiment plumbing (docs/EXPERIMENTS.md): explicit replay
windows, frozen datasets, error bars, the selectivity control, official
baselines, the sizing simulation, and the gate's anti-gaming rules stated as
adversarial cases a reader can cite."""

import json
from datetime import date, datetime, timezone

import httpx

from openthomas.config import RiskProfile
from openthomas.kernel import gate
from openthomas.markets.base import Side
from openthomas.markets.kalshi import KalshiConnector
from openthomas.risk.simulate import VARIANTS, compare, simulate
from openthomas.weather import ablation
from openthomas.weather.mos import MosStore, _parse_bulletin
from openthomas.weather.replay import (QuoteCache, ReplayRow, ReplayTrade, bootstrap_ci,
                                       collect_rows, dataset_digest, decide,
                                       load_rows, max_drawdown, save_rows,
                                       snapshot_time, summarize, window)
from openthomas.weather.stations import STATIONS
from openthomas.weather.verification import VerificationStore

FLAT_FEE = lambda price, qty: 0.01  # noqa: E731


def row(day, p_model, bid, ask, outcome, ticker="T", station="nyc", kind="high", p_raw=None):
    return ReplayRow(ticker=ticker, station=station, kind=kind, day=day, p_model=p_model,
                     yes_bid=bid, yes_ask=ask, outcome_yes=outcome, p_model_raw=p_raw)


def trade(day, pnl, ticker="T", station="nyc", win=None):
    return ReplayTrade(ticker, station, Side.YES, 0.5, 0.01, 0.6, pnl > 0 if win is None else win,
                       pnl, day=day)


# --- windows and snapshots -------------------------------------------------------

def test_window_is_explicit_dates_and_ends_yesterday():
    start, end = window(days=7, today=date(2026, 9, 12))
    assert (start, end) == (date(2026, 9, 5), date(2026, 9, 11))
    start, end = window(start=date(2026, 7, 1), end=date(2026, 7, 21))
    assert (start, end) == (date(2026, 7, 1), date(2026, 7, 21))


def test_snapshot_is_local_not_utc():
    # Local midnight for a low: 04:00Z in New York (EDT), 07:00Z in Los Angeles.
    assert snapshot_time(STATIONS["nyc"], date(2026, 7, 8), "low").hour == 4
    assert snapshot_time(STATIONS["lax"], date(2026, 7, 8), "low").hour == 7
    # Late morning for a high: 15Z in New York, 18Z in LA. Overridable for E3.
    assert snapshot_time(STATIONS["nyc"], date(2026, 7, 8), "high").hour == 15
    early = {"high": 9, "low": 0}
    assert snapshot_time(STATIONS["nyc"], date(2026, 7, 8), "high", early).hour == 13


def _kalshi_with(markets_pages, candle_at):
    """A mock venue: /markets paginates through `markets_pages`; candlesticks
    record the snapshot timestamp asked for and answer a 0.25/0.27 book."""
    seen = {"pages": 0, "snapshots": []}

    def handler(request):
        if request.url.path == "/markets":
            page = seen["pages"]
            seen["pages"] += 1
            body = {"markets": markets_pages[page]}
            if page + 1 < len(markets_pages):
                body["cursor"] = f"c{page + 1}"
            return httpx.Response(200, json=body)
        assert "candlesticks" in request.url.path
        seen["snapshots"].append(int(request.url.params["end_ts"]))
        return httpx.Response(200, json={"candlesticks": [
            {"yes_bid": {"close_dollars": "0.25"}, "yes_ask": {"close_dollars": "0.27"}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://x.test")
    return KalshiConnector(client=client), seen


def _raw(ticker, result="yes", floor=83):
    return {"ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0],
            "title": "Will the high temp in NYC be >83°?", "yes_bid_dollars": "0.25",
            "yes_ask_dollars": "0.27", "close_time": "2026-07-09T04:59:00Z",
            "strike_type": "greater", "floor_strike": floor, "result": result}


def test_collect_rows_paginates_filters_window_and_prices_raw_and_biased(tmp_path):
    store = VerificationStore(tmp_path / "v.jsonl")
    # Guidance for two days at lead 1; settlements BEFORE the window teach a +2 bias.
    for d in ("2026-06-20", "2026-06-21", "2026-06-22"):
        store.record_guidance("nyc", "high", date.fromisoformat(d), 1, 80.0, 1.0, {"a": 80})
        store.record_settlement("nyc", "high", date.fromisoformat(d), 82.0)
    for d in ("2026-07-08", "2026-07-09"):
        store.record_guidance("nyc", "high", date.fromisoformat(d), 1, 83.0, 1.0, {"a": 83})
    pages = [[_raw("KXHIGHNY-26JUL08-T83")],
             [_raw("KXHIGHNY-26JUL09-T83"), _raw("KXHIGHNY-26JUL20-T83")]]  # JUL20 outside
    kalshi, seen = _kalshi_with(pages, None)
    rows = collect_rows(kalshi, store, "KXHIGHNY", STATIONS["nyc"], "high",
                        start=date(2026, 7, 8), end=date(2026, 7, 9))
    assert seen["pages"] == 2, "must follow the cursor"
    assert [r.day for r in rows] == ["2026-07-08", "2026-07-09"]
    r = rows[0]
    assert r.bias > 0 and r.p_model > r.p_model_raw, "learned warm bias lifts P(>83)"
    assert r.source == "consensus" and r.snapshot_utc.startswith("2026-07-08T15:00")
    # The candle request asked for the local-11am snapshot, expressed in UTC.
    assert seen["snapshots"][0] == int(datetime(2026, 7, 8, 15, tzinfo=timezone.utc).timestamp())


def test_rows_freeze_and_reload_byte_stable(tmp_path):
    rows = [row("2026-07-09", 0.6, 0.5, 0.55, True, ticker="B"),
            row("2026-07-08", 0.7, 0.5, 0.55, False, ticker="A", p_raw=0.65)]
    p = save_rows(rows, tmp_path / "rows.jsonl")
    again = load_rows(p)
    assert [r.ticker for r in again] == ["A", "B"], "sorted, so the digest is stable"
    assert again[0].p_model_raw == 0.65 and again[1].p_model_raw is None
    d1 = dataset_digest(p)
    save_rows(list(reversed(rows)), p)
    assert dataset_digest(p) == d1
    # Older files without the new fields still load.
    legacy = {k: v for k, v in json.loads(p.read_text().splitlines()[0]).items()
              if k not in ("p_model_raw", "source", "snapshot_utc")}
    (tmp_path / "old.jsonl").write_text(json.dumps(legacy) + "\n")
    assert load_rows(tmp_path / "old.jsonl")[0].source == "consensus"


# --- error bars -----------------------------------------------------------------

def test_summarize_reports_error_bars_only_when_asked():
    trades = [trade(f"2026-07-{d:02d}", pnl) for d, pnl in
              ((1, 0.3), (1, -0.5), (2, 0.4), (3, 0.2), (4, -0.6), (5, 0.5), (6, 0.1))]
    lean = summarize(trades)
    assert "ci95_pnl_per_contract" not in lean and lean["avg_win"] > 0 > lean["avg_loss"]
    full = summarize(trades, ci=True)
    lo, hi = full["ci95_pnl_per_contract"]
    assert lo <= full["pnl_per_contract"] <= hi
    assert full["days"] == 6 and full["max_drawdown"] > 0 and "t_stat" in full


def test_bootstrap_resamples_days_not_trades():
    # One day carries all the profit: day-level resampling must sometimes drop
    # it, so the interval reaches well below the point estimate.
    trades = [trade("2026-07-01", 1.0, ticker=f"A{i}") for i in range(20)]
    trades += [trade(f"2026-07-{d:02d}", -0.05, ticker=f"B{d}") for d in range(2, 8)]
    point = summarize(trades)["pnl_per_contract"]
    lo, _ = bootstrap_ci(trades, n_boot=500)
    assert lo < 0 < point


def test_max_drawdown_of_a_path():
    assert max_drawdown([1, -2, 1, 1, -3, 4]) == 3
    assert max_drawdown([1, 1, 1]) == 0


# --- E1: the selectivity control --------------------------------------------------

def _mixed_rows():
    # Model says 0.90 everywhere; some strikes at 0.55/0.60, some at 0.70/0.75.
    # Un-blended, both clear a 0.08 bar (edges 0.29 and 0.14); blended with a
    # 0.5 market weight the cheap ones still clear (0.13) and the expensive
    # ones do not (0.05). The control must raise the bar past 0.14.
    rows = []
    for i in range(10):
        rows.append(row(f"2026-07-{i + 1:02d}", 0.90, 0.55, 0.60, True, ticker=f"C{i}", p_raw=0.85))
        rows.append(row(f"2026-07-{i + 1:02d}", 0.90, 0.70, 0.75, i % 2 == 0, ticker=f"E{i}",
                        p_raw=0.85))
    return rows


def test_threshold_control_matches_the_blends_activity():
    rows = _mixed_rows()
    production = decide(rows, FLAT_FEE, 0.08, 0.5)
    assert len(production) == 10
    edge, control = ablation.threshold_matched(rows, FLAT_FEE, len(production), 0.0)
    assert len(control) <= 10 and edge > 0.14, "bar raised past the expensive strikes' edge"
    table = ablation.ablation(rows, FLAT_FEE, 0.08, 0.5, ci=False)
    names = [t["rule"] for t in table]
    assert names[0].startswith("naked") and names[-1].startswith("control")
    assert table[0]["n"] == 20 and table[2]["n"] == 10 and table[3]["n"] <= 10


def test_ablation_admits_missing_raw_rows():
    rows = [row("2026-07-01", 0.9, 0.5, 0.55, True)]
    table = ablation.ablation(rows, FLAT_FEE, ci=False)
    assert table[0]["n"] == 0 and "unavailable" in table[0]["note"]


def test_grid_and_breakdowns_cover_every_cell():
    rows = _mixed_rows()
    cells = ablation.grid(rows, FLAT_FEE, edges=(0.05, 0.1), weights=(0.0, 0.5))
    assert len(cells) == 4
    results = ablation.run_all(rows, FLAT_FEE)
    assert results["window"] == ["2026-07-01", "2026-07-10"]
    assert set(results["by_station"]) == {"nyc"} and results["by_week"]
    md = ablation.render_markdown(results)
    assert "E1 ablation" in md and "control" in md


# --- E5: official baselines --------------------------------------------------------

def test_mos_bulletin_parsing_uses_the_nx_convention():
    rows = [{"ftime": "2026-07-09 00:00", "txn": 93.0}, {"ftime": "2026-07-08 12:00", "txn": "76"},
            {"ftime": "2026-07-08 15:00", "txn": None}]
    assert _parse_bulletin(rows, "txn", date(2026, 7, 8)) == {"high": 93.0, "low": 76.0}


def test_mos_store_loads_as_of_runs_and_learns_its_own_bias(tmp_path):
    verification = VerificationStore(tmp_path / "v.jsonl")
    for d, actual in (("2026-07-08", 85), ("2026-07-09", 86)):
        verification.record_settlement("mia", "high", date.fromisoformat(d), actual)
    asked = []

    def handler(request):
        asked.append(request.url.params["runtime"])
        target = datetime.strptime(request.url.params["runtime"][:10], "%Y-%m-%d").date()
        nxt = target.replace(day=target.day + 2)  # run d-1 -> max at d+1 00Z
        return httpx.Response(200, json={"data": [
            {"ftime": f"{nxt.isoformat()} 00:00", "txn": 83.0},
            {"ftime": f"{target.replace(day=target.day + 1).isoformat()} 12:00", "txn": 75.0}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ms = MosStore(tmp_path / "mos-nbm.jsonl", "nbm", verification, http=client)
    assert ms.load_station(STATIONS["mia"], date(2026, 7, 8), date(2026, 7, 9)) == 4
    assert asked == ["2026-07-07T12:00:00Z", "2026-07-08T12:00:00Z"], "d-1 12Z, one per day"
    assert ms.load_station(STATIONS["mia"], date(2026, 7, 8), date(2026, 7, 9)) == 0, "idempotent"
    assert ms.guidance("mia", "high", date(2026, 7, 9)) == 83.0
    bias, sigma, n = ms.stats("mia", "high")
    assert n == 2 and bias > 0, "NBM ran cold here; the source learns its own correction"
    assert ms.stats("mia", "high", before="2026-07-08")[2] == 0, "out-of-window learning"


def test_collect_rows_from_an_official_source(tmp_path):
    verification = VerificationStore(tmp_path / "v.jsonl")
    ms = MosStore(tmp_path / "mos-nbm.jsonl", "nbm", verification)
    ms.record("nyc", "high", date(2026, 7, 8), "2026-07-07T12:00:00", 86.0)
    kalshi, _ = _kalshi_with([[_raw("KXHIGHNY-26JUL08-T83")]], None)
    rows = collect_rows(kalshi, verification, "KXHIGHNY", STATIONS["nyc"], "high",
                        start=date(2026, 7, 8), end=date(2026, 7, 8), guidance_source=ms)
    assert len(rows) == 1 and rows[0].source == "nbm" and rows[0].mean == 86.0


# --- E7: the risk engine, scored ---------------------------------------------------

def _sim_trades():
    out = []
    for d in range(1, 11):
        for k in range(5):  # five correlated markets a day (same station-day event)
            out.append(ReplayTrade(f"M{d}-{k}", "nyc", Side.YES, 0.50, 0.02, 0.62,
                                   outcome_win=(d + k) % 3 != 0, pnl=0.0, day=f"2026-07-{d:02d}"))
    return out


def test_simulation_variants_order_risk_as_designed():
    profile = RiskProfile.preset("conservative")
    results = {r["variant"]: r for r in compare(_sim_trades(), profile, 1000.0)}
    assert set(results) == set(VARIANTS)
    uncapped = results["kelly"]
    capped = results["kelly+caps"]
    assert capped["max_drawdown"] <= uncapped["max_drawdown"], "caps bound the damage"
    assert capped["rejected"], "some entries hit a cap and are named by it"
    assert results["flat"]["entered"] == 50


def test_kill_switch_halts_and_stays_halted():
    profile = RiskProfile.preset("conservative").model_copy(
        update={"max_drawdown": 0.02, "max_position_frac": 0.5, "max_event_frac": 1.0,
                "max_category_frac": 1.0, "max_open_risk_frac": 1.0, "kelly_fraction": 0.1})
    losers = [ReplayTrade(f"L{d}", "nyc", Side.YES, 0.50, 0.02, 0.90, False, 0.0,
                          day=f"2026-07-{d:02d}") for d in range(1, 8)]
    r = simulate(losers, profile, 1000.0, "kelly+caps+kill")
    assert r.halted_on is not None and r.rejected.get("kill-switch", 0) >= 1
    assert r.entered < len(losers)
    r2 = simulate(losers, profile, 1000.0, "kelly+caps")
    assert r2.entered == len(losers), "without the switch every loser is taken"


# --- E8: the gate's anti-gaming rules, as cases ------------------------------------

def _score(trades_in, trades_out):
    return gate.Score(params={}, held_in=summarize(trades_in), held_out=summarize(trades_out))


def test_gate_a_rule_that_trades_nothing_cannot_win_on_drawdown():
    champion = _score([trade(f"2026-06-{d:02d}", -0.05, ticker=f"T{d}") for d in range(1, 30)], [])
    nothing = _score([], [])
    ok, why = gate.beats(nothing, champion)
    assert not ok and "not enough trades" in why


def test_gate_two_lucky_trades_cannot_outrank_a_compounding_edge():
    steady = _score([trade(f"2026-06-{d:02d}", 0.05, ticker=f"S{d}") for d in range(1, 30)], [])
    lucky = _score([trade("2026-06-01", 0.9, ticker="L1"),
                    trade("2026-06-02", 0.9, ticker="L2")], [])
    assert summarize(lucky.held_in and [trade("2026-06-01", 0.9)])["pnl_per_contract"] > \
        steady.held_in["pnl_per_contract"], "the gamed ratio really is higher"
    ok, why = gate.beats(lucky, steady)
    assert not ok and "not enough trades" in why


def test_gate_a_prompt_that_wins_pnl_but_worsens_brier_is_vetoed():
    base_in = [trade(f"2026-06-{d:02d}", 0.05, ticker=f"S{d}") for d in range(1, 30)]
    champion = gate.Score(params={}, held_in={**summarize(base_in), "brier": 0.20, "n_pairs": 100},
                          held_out={"total_pnl": 0.0, "n": 0})
    better_pnl = [trade(f"2026-06-{d:02d}", 0.10, ticker=f"S{d}") for d in range(1, 30)]
    challenger = gate.Score(params={},
                            held_in={**summarize(better_pnl), "brier": 0.23, "n_pairs": 100},
                            held_out={"total_pnl": 0.0, "n": 0})
    ok, why = gate.beats_forecast(challenger, champion)
    assert not ok and "Brier worsens" in why


def test_quote_cache_makes_the_second_collection_free(tmp_path):
    store = VerificationStore(tmp_path / "v.jsonl")
    store.record_guidance("nyc", "high", date(2026, 7, 8), 1, 83.0, 1.0, {"a": 83})
    kalshi, seen = _kalshi_with([[_raw("KXHIGHNY-26JUL08-T83")]], None)
    cache = QuoteCache(tmp_path / "quotes.jsonl")
    kw = dict(start=date(2026, 7, 8), end=date(2026, 7, 8), quote_cache=cache)
    first = collect_rows(kalshi, store, "KXHIGHNY", STATIONS["nyc"], "high", **kw)
    assert len(first) == 1 and len(seen["snapshots"]) == 1
    kalshi2, seen2 = _kalshi_with([[_raw("KXHIGHNY-26JUL08-T83")]], None)
    again = collect_rows(kalshi2, store, "KXHIGHNY", STATIONS["nyc"], "high",
                         **{**kw, "quote_cache": QuoteCache(tmp_path / "quotes.jsonl")})
    assert seen2["snapshots"] == [], "served from disk"
    assert again[0].yes_ask == first[0].yes_ask
    # A different snapshot hour is a different key: fetched, not served stale.
    kalshi3, seen3 = _kalshi_with([[_raw("KXHIGHNY-26JUL08-T83")]], None)
    collect_rows(kalshi3, store, "KXHIGHNY", STATIONS["nyc"], "high",
                 **{**kw, "snapshot_hours": {"high": 9, "low": 0}})
    assert len(seen3["snapshots"]) == 1
