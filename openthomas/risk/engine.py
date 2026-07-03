"""Deterministic risk engine. The forecaster proposes; this code disposes.

Every check here exists because Prediction Arena documented frontier LLMs
failing without it: all-in concentration, correlated settlements, overtrading,
and trading through drawdowns.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import RiskProfile
from ..markets.base import Market, Position, Side


@dataclass
class RiskVerdict:
    approved: bool
    qty: int = 0
    reason: str = ""


def kelly_fraction(p: float, cost: float) -> float:
    """Full-Kelly bankroll fraction for a binary contract bought at `cost`
    (pays 1 if right) given belief `p` that it pays out. Negative → no bet."""
    if not 0 < cost < 1:
        return 0.0
    return (p - cost) / (1 - cost)


@dataclass
class PortfolioState:
    bankroll: float  # capital allocated to the agent
    cash: float
    positions: list[Position]
    peak_value: float  # high-water mark of account value
    account_value: float  # cash + mark-to-market at bid

    def exposure(self, *, market_id: str | None = None, event_id: str | None = None,
                 category: str | None = None, events: dict[str, str] | None = None,
                 categories: dict[str, str] | None = None) -> float:
        """Total cost basis of open positions matching a filter."""
        total = 0.0
        for pos in self.positions:
            if market_id and pos.market_id != market_id:
                continue
            if event_id and (events or {}).get(pos.market_id) != event_id:
                continue
            if category and (pos.category or (categories or {}).get(pos.market_id)) != category:
                continue
            total += pos.cost_basis
        return total


class RiskEngine:
    def __init__(self, profile: RiskProfile):
        self.profile = profile

    def drawdown_halted(self, state: PortfolioState) -> bool:
        if state.peak_value <= 0:
            return False
        drawdown = 1 - state.account_value / state.peak_value
        return drawdown >= self.profile.max_drawdown

    def size_entry(
        self,
        state: PortfolioState,
        market: Market,
        side: Side,
        p_calibrated: float,
        fee_per_contract: float = 0.0,
        events: dict[str, str] | None = None,
    ) -> RiskVerdict:
        """Approve and size a new entry, or reject with the binding constraint."""
        p = self.profile
        if self.drawdown_halted(state):
            return RiskVerdict(False, reason="kill-switch: max drawdown reached, human resume required")

        cost = market.price_to_buy(side)
        if cost is None:
            return RiskVerdict(False, reason="no quote")
        cost_eff = cost + fee_per_contract
        p_side = p_calibrated if side is Side.YES else 1 - p_calibrated

        if not (p.min_price <= cost <= p.max_price):
            return RiskVerdict(False, reason=f"price {cost:.2f} in longshot/near-certain zone")
        if market.liquidity < p.min_liquidity:
            return RiskVerdict(False, reason=f"liquidity {market.liquidity:.0f} < {p.min_liquidity:.0f}")
        if market.category and market.category in p.categories_blocked:
            return RiskVerdict(False, reason=f"category {market.category!r} blocked by profile")

        edge = p_side - cost_eff
        if edge < p.min_edge:
            return RiskVerdict(False, reason=f"edge {edge:+.3f} below threshold {p.min_edge:.3f}")

        f_star = kelly_fraction(p_side, cost_eff)
        target = state.bankroll * p.kelly_fraction * f_star

        # Concentration caps, all at cost basis.
        room_market = state.bankroll * p.max_position_frac - state.exposure(market_id=market.id)
        room_event = (
            state.bankroll * p.max_event_frac
            - state.exposure(event_id=market.event_id, events=events)
            if market.event_id else float("inf")
        )
        room_category = (
            state.bankroll * p.max_category_frac - state.exposure(category=market.category)
            if market.category else float("inf")
        )
        room_open = state.bankroll * p.max_open_risk_frac - state.exposure()
        budget = min(target, room_market, room_event, room_category, room_open, state.cash)

        qty = int(budget / cost_eff)
        if qty < 1:
            binding = min(
                (room_market, "per-market cap"), (room_event, "per-event cap"),
                (room_category, "per-category cap"), (room_open, "open-risk cap"),
                (state.cash, "cash"), (target, "kelly size"),
            )[1]
            return RiskVerdict(False, reason=f"sized to zero by {binding}")
        return RiskVerdict(True, qty=qty, reason=f"edge {edge:+.3f}, kelly {f_star:.3f}, {qty} contracts")

    def solvent(self, state: PortfolioState, cost: float) -> bool:
        return state.cash >= cost
