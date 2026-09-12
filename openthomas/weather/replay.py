"""Market replay: what would the live decision rule have earned on settled
temperature markets?

Mirrors the production pipeline, not a fantasy one: station bias/sigma are
learned strictly BEFORE the replay window (no peeking), the baseline is
blended with the market price exactly as the loop does, and only then does
the edge bar apply. Prices come from Kalshi hourly candlesticks at a fixed
decision snapshot. Still conservative vs live: no intraday observations,
no LLM adjustment, yesterday's model run only.

The first, unblended run of this replay lost -$0.038/contract on 417 trades
— the market at 11am already knows the morning obs. That number is why the
market-prior blend is not optional.

Split into two halves so the self-improvement loop can score many candidate
decision rules against ONE fetched dataset:

- `collect_rows` (network): everything about a settled market that does not
  depend on the decision rule — model probability, snapshot quotes, outcome.
- `decide` (pure): the decision rule itself — blend, edge bar, side pick.

`decide` is part of the frozen evaluator surface (see docs/RSI.md): the
improvement loop tunes its *parameters*, never its code.

Reproducibility (docs/EXPERIMENTS.md): a replay window is a pair of explicit
dates, never "the last N days" — the same command a year from now must
score the same markets. Collected rows can be frozen to JSONL with
`save_rows` and re-scored offline with `load_rows`; `dataset_digest` is the
hash a paper cites.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from ..markets.base import Market, Side
from ..markets.kalshi import KalshiConnector
from .baseline import strike_probability
from .stations import KALSHI_SERIES, STATIONS, Station
from .strikes import parse_strike
from .verification import VerificationStore

# Decision snapshots must precede the extreme being bet on, and the NWS CLI
# day is a *local* calendar day — so the snapshot is a local hour, not a UTC
# one. Lows: local midnight, the first minute of the CLI day, when nothing of
# the day's minimum can be on the books yet (a fixed 06Z was 2am on the east
# coast, two hours into the day, and 11pm the day before at LAX). Highs: late
# morning — the afternoon peak is still a live contest. The first replay run
# went 0-for-23 on low series at a 15:00 UTC snapshot: by then the dawn low
# was a settled fact.
SNAPSHOT_LOCAL_HOUR = {"high": 11, "low": 0}
REPLAY_LEAD = 1  # freshest guidance that is certainly pre-snapshot
KALSHI_PAGE = 200  # /markets page size; the endpoint paginates by cursor


@dataclass
class ReplayRow:
    """Decision-rule-independent facts about one settled market.

    The trailing fields carry everything the LLM-in-replay evaluator needs to
    rebuild the forecast prompt as-of decision time (docs/RSI.md) — guidance
    consensus and station stats, never the outcome.
    """

    ticker: str
    station: str
    kind: str
    day: str  # ISO date the market settles on
    p_model: float  # baseline P(yes) before any market blend
    yes_bid: float
    yes_ask: float
    outcome_yes: bool
    question: str = ""
    strike_desc: str = ""
    mean: float | None = None  # guidance consensus, °F
    spread: float = 0.0
    bias: float = 0.0
    sigma: float = 0.0
    # Ablation support: the same strike priced with NO learned station bias.
    # Rows collected before this field existed load as None and the
    # "bias off" ablation reports itself as unavailable rather than lying.
    p_model_raw: float | None = None
    source: str = "consensus"  # which guidance priced p_model (docs/EXPERIMENTS.md)
    snapshot_utc: str = ""  # ISO timestamp of the quote snapshot actually used


@dataclass
class ReplayTrade:
    ticker: str
    station: str
    side: Side
    price: float  # per contract, for our side
    fee: float
    p: float  # our P(side wins)
    outcome_win: bool
    pnl: float  # per contract
    day: str = ""  # ISO settlement date, for held-in/held-out splits


def snapshot_time(station: Station, day: date, kind: str,
                  hours: dict[str, int] | None = None) -> datetime:
    """The UTC instant of the decision snapshot for one station-day."""
    local_hour = (hours or SNAPSHOT_LOCAL_HOUR)[kind]
    local = datetime(day.year, day.month, day.day, local_hour, tzinfo=ZoneInfo(station.timezone))
    return local.astimezone(timezone.utc)


class QuoteCache:
    """Snapshot quotes keyed by (ticker, snapshot instant), on disk.

    A settled market's candle at a fixed past instant never changes, so the
    fetch is made once and every later replay — the daily re-freeze, the
    other guidance sources (E5), the snapshot sweep (E3) — reads it back.
    Misses (no book at the snapshot) are cached too, as null, so a dead
    market is not re-asked daily. Append-only JSONL; last write wins.
    """

    def __init__(self, path: Path | str | None):
        self.path = Path(path) if path else None
        self._data: dict[str, list[float] | None] = {}
        if self.path and self.path.exists():
            for line in self.path.read_text().splitlines():
                try:
                    row = json.loads(line)
                    self._data[row["k"]] = row["q"]
                except (json.JSONDecodeError, KeyError):
                    continue

    @staticmethod
    def key(ticker: str, at: datetime) -> str:
        return f"{ticker}@{int(at.timestamp())}"

    def get(self, ticker: str, at: datetime) -> tuple[bool, tuple[float, float] | None]:
        """(hit, quotes)."""
        k = self.key(ticker, at)
        if k not in self._data:
            return False, None
        q = self._data[k]
        return True, (None if q is None else (q[0], q[1]))

    def put(self, ticker: str, at: datetime, quotes: tuple[float, float] | None) -> None:
        k = self.key(ticker, at)
        self._data[k] = None if quotes is None else [quotes[0], quotes[1]]
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps({"k": k, "q": self._data[k]}) + "\n")


def _snapshot_quotes(kalshi: KalshiConnector, series: str, ticker: str,
                     at: datetime, cache: QuoteCache | None = None) -> tuple[float, float] | None:
    """(yes_bid, yes_ask) at the decision snapshot, from hourly candles."""
    if cache is not None:
        hit, quotes = cache.get(ticker, at)
        if hit:
            return quotes
    quotes = _fetch_snapshot_quotes(kalshi, series, ticker, at)
    if cache is not None:
        cache.put(ticker, at, quotes)
    return quotes


def _fetch_snapshot_quotes(kalshi: KalshiConnector, series: str, ticker: str,
                           at: datetime) -> tuple[float, float] | None:
    ts = int(at.timestamp())
    data = kalshi.http.get(
        f"/series/{series}/markets/{ticker}/candlesticks",
        params={"start_ts": ts - 3600, "end_ts": ts, "period_interval": 60},
    ).json()
    candles = data.get("candlesticks") or []
    if not candles:
        return None
    last = candles[-1]
    try:
        bid = float(last["yes_bid"]["close_dollars"])
        ask = float(last["yes_ask"]["close_dollars"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (0 < bid and 0 < ask < 1) or ask - bid > 0.10:
        return None  # no real book at the snapshot
    return bid, ask


def _settled_markets(kalshi: KalshiConnector, series: str, start: date, end: date) -> list[dict]:
    """Every settled market of a series whose ticker date lies in [start, end].

    Paginates: a busy series lists several strikes per day, and one page
    silently truncating a long window would bias the sample toward whichever
    end the venue lists first.
    """
    min_close = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    # Markets close (settle) a day or so after their ticker date; pad the far end.
    max_close = int((datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
                     + timedelta(days=3)).timestamp())
    out: list[dict] = []
    cursor = ""
    for _ in range(50):  # hard stop: a broken cursor must not loop forever
        params = {"series_ticker": series, "status": "settled", "limit": KALSHI_PAGE,
                  "min_close_ts": min_close, "max_close_ts": max_close}
        if cursor:
            params["cursor"] = cursor
        data = kalshi.http.get("/markets", params=params).json()
        out += data.get("markets", [])
        cursor = data.get("cursor") or ""
        if not cursor:
            break
    return out


def window(days: int | None = None, start: date | None = None,
           end: date | None = None, today: date | None = None) -> tuple[date, date]:
    """Resolve a replay window to explicit dates. `end` defaults to yesterday
    (the last day whose CLI report can exist); `start` to `end - days`."""
    today = today or datetime.now(timezone.utc).date()
    end = end or (today - timedelta(days=1))
    if start is None:
        start = end - timedelta(days=(days or 30) - 1)
    if start > end:
        raise ValueError(f"replay window start {start} is after end {end}")
    return start, end


def collect_rows(kalshi: KalshiConnector, store: VerificationStore, series: str,
                 station: Station, kind: str, days: int = 30, *,
                 start: date | None = None, end: date | None = None,
                 snapshot_hours: dict[str, int] | None = None,
                 guidance_source=None, quote_cache: QuoteCache | None = None) -> list[ReplayRow]:
    """Fetch the settled markets, guidance, and snapshot quotes for one series.

    Network-heavy and decision-rule-independent: run once, score many rules.

    `guidance_source` swaps the multi-model consensus for another as-of
    guidance (e.g. NBM or GFS MOS, weather/mos.py) that answers
    `guidance(station_key, kind, day) -> °F | None` and
    `stats(station_key, kind, before) -> (bias, sigma, n)`. The station
    bias/sigma are then learned for THAT source, out-of-window, so each
    source is scored on its own record.
    """
    start, end = window(days, start, end)
    # Bias/sigma learned only from days before the replay window — no peeking.
    cutoff = start.isoformat()
    if guidance_source is None:
        bias, sigma_stat, _ = store.stats(station.key, kind, REPLAY_LEAD, before=cutoff)
        source_name = "consensus"
    else:
        bias, sigma_stat, _ = guidance_source.stats(station.key, kind, before=cutoff)
        source_name = guidance_source.name

    rows: list[ReplayRow] = []
    for raw in _settled_markets(kalshi, series, start, end):
        try:
            if raw.get("result") not in ("yes", "no"):
                continue
            market: Market = kalshi._to_market(raw)
            strike = parse_strike(market)
            parts = market.id.split("-")
            if strike is None or len(parts) < 3:
                continue
            try:
                day = datetime.strptime(parts[1], "%y%b%d").date()
            except ValueError:
                continue
            if not (start <= day <= end):
                continue

            if guidance_source is None:
                guidance = store.guidance(station.key, kind, day, REPLAY_LEAD)
                if guidance is None:
                    continue
                mean, spread = guidance
            else:
                value = guidance_source.guidance(station.key, kind, day)
                if value is None:
                    continue
                mean, spread = value, 0.0
            sigma = max(sigma_stat, 0.8 * spread)
            p_model = strike_probability(strike, mean + bias, sigma, kind)
            p_raw = strike_probability(strike, mean, sigma, kind)

            at = snapshot_time(station, day, kind, snapshot_hours)
            quotes = _snapshot_quotes(kalshi, series, market.id, at, quote_cache)
            if quotes is None:
                continue
            bid, ask = quotes
            rows.append(ReplayRow(
                ticker=market.id, station=station.key, kind=kind,
                day=day.isoformat(), p_model=p_model,
                yes_bid=bid, yes_ask=ask, outcome_yes=raw["result"] == "yes",
                question=market.question, strike_desc=strike.describe(),
                mean=mean, spread=spread, bias=bias, sigma=sigma,
                p_model_raw=p_raw, source=source_name, snapshot_utc=at.isoformat(),
            ))
        except Exception:  # one bad candle fetch must not kill the meta-cycle
            continue
    return rows


def decide(rows: list[ReplayRow], fee_fn: Callable[[float, int], float],
           min_edge: float = 0.08,
           market_prior_weight: float = 0.5) -> list[ReplayTrade]:
    """Apply the live decision rule to collected rows. Pure and deterministic."""
    trades: list[ReplayTrade] = []
    for row in rows:
        # The loop's rule exactly: the crowd is information, not noise.
        mid = (row.yes_bid + row.yes_ask) / 2
        p_yes = market_prior_weight * mid + (1 - market_prior_weight) * row.p_model

        for side, price, p in ((Side.YES, row.yes_ask, p_yes),
                               (Side.NO, 1 - row.yes_bid, 1 - p_yes)):
            fee = fee_fn(price, 1)
            if p - price - fee < min_edge or not (0.05 <= price <= 0.95):
                continue
            win = row.outcome_yes if side is Side.YES else not row.outcome_yes
            pnl = (1 - price - fee) if win else (-price - fee)
            trades.append(ReplayTrade(row.ticker, row.station, side, price, fee,
                                      p, win, pnl, day=row.day))
            break  # at most one side per market
    return trades


def replay_station(kalshi: KalshiConnector, store: VerificationStore, series: str,
                   station: Station, kind: str, days: int = 30,
                   min_edge: float = 0.08,
                   market_prior_weight: float = 0.5, **collect_kw) -> list[ReplayTrade]:
    rows = collect_rows(kalshi, store, series, station, kind, days, **collect_kw)
    return decide(rows, kalshi.fee, min_edge, market_prior_weight)


def collect_all(store: VerificationStore, days: int = 30, **collect_kw) -> list[ReplayRow]:
    """All series' rows in one flat list — the evaluator's frozen dataset.
    Quotes are cached next to the verification store unless a cache is given."""
    kalshi = KalshiConnector()
    if "quote_cache" not in collect_kw:
        collect_kw["quote_cache"] = QuoteCache(store.path.parent / "replay-quotes.jsonl")
    rows: list[ReplayRow] = []
    for series, (station_key, kind) in KALSHI_SERIES.items():
        try:
            rows += collect_rows(kalshi, store, series, STATIONS[station_key], kind, days,
                                 **collect_kw)
        except Exception:  # a dead series must not sink the others
            continue
    return rows


def replay_all(store: VerificationStore, days: int = 30,
               min_edge: float = 0.08, **collect_kw) -> dict[str, list[ReplayTrade]]:
    kalshi = KalshiConnector()
    out: dict[str, list[ReplayTrade]] = {}
    for series, (station_key, kind) in KALSHI_SERIES.items():
        out[series] = replay_station(kalshi, store, series, STATIONS[station_key],
                                     kind, days, min_edge, **collect_kw)
    return out


# --- frozen datasets ------------------------------------------------------------

def save_rows(rows: list[ReplayRow], path: Path | str) -> Path:
    """Freeze collected rows to JSONL, sorted so the file is byte-stable for a
    given window: the digest of this file is what a paper cites."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows, key=lambda r: (r.day, r.ticker))
    path.write_text("".join(json.dumps(asdict(r), sort_keys=True) + "\n" for r in ordered))
    return path


def load_rows(path: Path | str) -> list[ReplayRow]:
    known = {f.name for f in fields(ReplayRow)}
    rows: list[ReplayRow] = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        rows.append(ReplayRow(**{k: v for k, v in raw.items() if k in known}))
    return rows


def dataset_digest(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


# --- scoring --------------------------------------------------------------------

def daily_pnl(trades: list[ReplayTrade]) -> dict[str, float]:
    by_day: dict[str, float] = {}
    for t in trades:
        by_day[t.day] = by_day.get(t.day, 0.0) + t.pnl
    return dict(sorted(by_day.items()))


def max_drawdown(series: list[float]) -> float:
    """Largest peak-to-trough fall of a cumulative-sum path starting at 0."""
    peak = total = 0.0
    worst = 0.0
    for x in series:
        total += x
        peak = max(peak, total)
        worst = max(worst, peak - total)
    return worst


def bootstrap_ci(trades: list[ReplayTrade], n_boot: int = 2000, seed: int = 0,
                 level: float = 0.95) -> tuple[float, float]:
    """Block bootstrap of PnL per contract, resampling settlement DAYS.

    Markets on the same day are not independent — one station's strikes all
    settle on one thermometer, and one synoptic pattern spans stations — so
    resampling trades would understate the noise. Days are the exchangeable
    unit here. Returns (lo, hi) of the mean PnL per contract.
    """
    days: dict[str, list[float]] = {}
    for t in trades:
        days.setdefault(t.day, []).append(t.pnl)
    blocks = list(days.values())
    if len(blocks) < 2:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        pnl = n = 0.0
        for _ in blocks:
            b = blocks[rng.randrange(len(blocks))]
            pnl += sum(b)
            n += len(b)
        means.append(pnl / n if n else 0.0)
    means.sort()
    lo = means[int((1 - level) / 2 * n_boot)]
    hi = means[min(n_boot - 1, int((1 + level) / 2 * n_boot))]
    return lo, hi


def summarize(trades: list[ReplayTrade], ci: bool = False, seed: int = 0) -> dict:
    """Totals-first summary; `ci=True` adds the error bars a paper needs
    (block-bootstrap CI by day, daily Sharpe, drawdown). The gate calls this
    per candidate, so the expensive part is opt-in."""
    n = len(trades)
    if not n:
        return {"n": 0}
    wins = [t for t in trades if t.outcome_win]
    losses = [t for t in trades if not t.outcome_win]
    pnl = sum(t.pnl for t in trades)
    out = {
        "n": n, "win_rate": len(wins) / n, "pnl_per_contract": pnl / n, "total_pnl": pnl,
        "avg_edge_priced": sum(t.p - t.price - t.fee for t in trades) / n,
        "avg_win": sum(t.pnl for t in wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(t.pnl for t in losses) / len(losses) if losses else 0.0,
        "fees": sum(t.fee for t in trades),
    }
    if ci:
        by_day = daily_pnl(trades)
        daily = list(by_day.values())
        out["days"] = len(daily)
        out["max_drawdown"] = max_drawdown(daily)
        if len(daily) > 1:
            mean = sum(daily) / len(daily)
            var = sum((x - mean) ** 2 for x in daily) / (len(daily) - 1)
            sd = math.sqrt(var)
            out["daily_mean"] = mean
            out["daily_sd"] = sd
            out["daily_sharpe"] = mean / sd if sd > 0 else float("nan")
            out["t_stat"] = mean / (sd / math.sqrt(len(daily))) if sd > 0 else float("nan")
        lo, hi = bootstrap_ci(trades, seed=seed)
        out["ci95_pnl_per_contract"] = [lo, hi]
    return out
