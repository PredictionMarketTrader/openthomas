"""Bankroll simulation over replay trades: the risk engine, scored.

Per-contract PnL (weather/replay.py) says whether a decision rule has edge.
It says nothing about the layer the harness thesis rests on — sizing,
concentration caps, and the drawdown kill-switch — because a contract is a
contract. This module replays the same trades through `RiskEngine.size_entry`
in settlement order, with a real bankroll, so the paper can report an
equity curve, drawdown, and what each rail cost or saved (docs/EXPERIMENTS.md,
E7).

Deterministic, no LLM, no learned parameters — this file lives in `risk/`
and follows its rules (AGENTS.md). It builds nothing the live loop does not:
the engine is called exactly as the trading cycle calls it, with a
`PortfolioState` and a `Market` carrying the snapshot quotes.

Simplifications, stated: all of a day's entries are placed at the snapshot
and settle together at day end (they do — every temperature market of a day
settles on the same CLI report); mark-to-market between entry and
settlement is at cost (no intraday quotes in the frozen rows); the halted
state persists to the end of the window, exactly as the live kill-switch
does until a human resumes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import RiskProfile
from ..markets.base import Market, Position, Side
from ..weather.replay import ReplayTrade, max_drawdown
from .engine import PortfolioState, RiskEngine

VARIANTS = ("flat", "kelly", "kelly+caps", "kelly+caps+kill")


@dataclass
class SimResult:
    variant: str
    bankroll0: float
    final: float
    days: int
    entered: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    equity: list[tuple[str, float]] = field(default_factory=list)  # (day, account value)
    halted_on: str | None = None

    @property
    def ret(self) -> float:
        return self.final / self.bankroll0 - 1

    @property
    def max_drawdown(self) -> float:
        """Peak-to-trough, as a fraction of the peak."""
        peak, worst = 0.0, 0.0
        for _, v in self.equity:
            peak = max(peak, v)
            if peak > 0:
                worst = max(worst, 1 - v / peak)
        return worst

    def as_dict(self) -> dict:
        return {"variant": self.variant, "bankroll": self.bankroll0, "final": round(self.final, 2),
                "return": round(self.ret, 4), "max_drawdown": round(self.max_drawdown, 4),
                "days": self.days, "entered": self.entered, "rejected": self.rejected,
                "halted_on": self.halted_on,
                "daily_pnl_max_dd": round(max_drawdown([b - a for (_, a), (_, b) in
                                                        zip(self.equity, self.equity[1:])]), 2)}


def _profile(base: RiskProfile, variant: str) -> RiskProfile:
    """The rails a variant switches off are set to non-binding values; the
    engine code itself is never bypassed, so sizes stay engine-produced."""
    if variant == "kelly+caps+kill":
        return base
    if variant == "kelly+caps":
        return base.model_copy(update={"max_drawdown": 1.0})
    if variant == "kelly":
        return base.model_copy(update={"max_drawdown": 1.0, "max_position_frac": 1.0,
                                       "max_event_frac": 1.0, "max_category_frac": 1.0,
                                       "max_open_risk_frac": 1.0})
    if variant == "flat":
        return base.model_copy(update={"max_drawdown": 1.0})
    raise ValueError(f"unknown variant {variant!r}; use one of {VARIANTS}")


def _market(t: ReplayTrade) -> Market:
    # Rebuild the book for our side only: buying YES costs yes_ask, buying NO
    # costs 1 - yes_bid. The other side's quote is unused by size_entry.
    if t.side is Side.YES:
        return Market(id=t.ticker, platform="kalshi", question=t.ticker, category="weather",
                      event_id=f"{t.station}-{t.day}", yes_bid=t.price, yes_ask=t.price,
                      liquidity=1e9)
    return Market(id=t.ticker, platform="kalshi", question=t.ticker, category="weather",
                  event_id=f"{t.station}-{t.day}", yes_bid=1 - t.price, yes_ask=1 - t.price,
                  liquidity=1e9)


def simulate(trades: list[ReplayTrade], profile: RiskProfile, bankroll: float = 1000.0,
             variant: str = "kelly+caps+kill", flat_frac: float = 0.01) -> SimResult:
    """Run the trades through the engine day by day. `flat_frac` is the
    fraction of the STARTING bankroll a flat-sized entry costs."""
    engine = RiskEngine(_profile(profile, variant))
    cash = bankroll
    peak = bankroll
    result = SimResult(variant=variant, bankroll0=bankroll, final=bankroll, days=0)
    result.equity.append(("start", bankroll))
    by_day: dict[str, list[ReplayTrade]] = {}
    for t in trades:
        by_day.setdefault(t.day, []).append(t)

    for day in sorted(by_day):
        positions: list[Position] = []
        events: dict[str, str] = {}  # market -> event, so the per-event cap can see siblings
        for t in sorted(by_day[day], key=lambda x: x.ticker):
            # Account value at cost between entry and settlement (no intraday marks).
            value = cash + sum(p.cost_basis for p in positions)
            state = PortfolioState(bankroll=bankroll, cash=cash, positions=positions,
                                   peak_value=peak, account_value=value)
            market = _market(t)
            verdict = engine.size_entry(state, market, t.side, t.p, fee_per_contract=t.fee,
                                        events=events)
            if not verdict.approved:
                key = ("edge" if verdict.reason.startswith("edge") else
                       verdict.reason.split(":")[0].split(" by ")[-1].strip())
                result.rejected[key] = result.rejected.get(key, 0) + 1
                if verdict.reason.startswith("kill-switch") and result.halted_on is None:
                    result.halted_on = day
                continue
            qty = verdict.qty
            if variant == "flat":
                qty = max(1, int(bankroll * flat_frac / (t.price + t.fee)))
                qty = min(qty, int(cash / (t.price + t.fee)))
                if qty < 1:
                    result.rejected["cash"] = result.rejected.get("cash", 0) + 1
                    continue
            cash -= qty * (t.price + t.fee)
            events[t.ticker] = market.event_id
            positions.append(Position(market_id=t.ticker, platform="kalshi", side=t.side,
                                      qty=qty, avg_cost=t.price, category="weather"))
            result.entered += 1
        # Settlement: winners pay $1 per contract, losers nothing.
        wins = {t.ticker: t.outcome_win for t in by_day[day]}
        for p in positions:
            if wins.get(p.market_id):
                cash += p.qty
        peak = max(peak, cash)
        result.days += 1
        result.equity.append((day, cash))
    result.final = cash
    return result


def compare(trades: list[ReplayTrade], profile: RiskProfile, bankroll: float = 1000.0,
            variants: tuple[str, ...] = VARIANTS) -> list[dict]:
    """E7: the same trades under each sizing regime."""
    return [simulate(trades, profile, bankroll, v).as_dict() for v in variants]
