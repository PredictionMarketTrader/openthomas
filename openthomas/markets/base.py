"""Unified market data model and connector interface for all platforms."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Side(str, Enum):
    YES = "yes"
    NO = "no"

    @property
    def opposite(self) -> "Side":
        return Side.NO if self is Side.YES else Side.YES


class Action(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class Market:
    """A single binary market. Prices are probabilities in [0, 1] for the YES side."""

    id: str
    platform: str  # "polymarket" | "kalshi"
    question: str
    category: str = ""
    event_id: str = ""  # groups multi-outcome markets that settle together
    yes_bid: float | None = None
    yes_ask: float | None = None
    volume_24h: float = 0.0
    liquidity: float = 0.0
    close_time: datetime | None = None
    resolution_rules: str = ""
    url: str = ""
    # Scalar-market strike structure (temperature, price levels, …) where the
    # platform provides it: YES iff value > floor / < cap / in [floor, cap].
    strike_type: str = ""  # "greater" | "less" | "between" | ""
    floor_strike: float | None = None
    cap_strike: float | None = None

    @property
    def mid(self) -> float | None:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return (self.yes_bid + self.yes_ask) / 2

    @property
    def spread(self) -> float | None:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return self.yes_ask - self.yes_bid

    def price_to_buy(self, side: Side) -> float | None:
        """Cost per contract to enter `side` (crossing the spread)."""
        if side is Side.YES:
            return self.yes_ask
        return None if self.yes_bid is None else 1.0 - self.yes_bid

    def price_to_sell(self, side: Side) -> float | None:
        """Liquidation value per contract of an existing `side` position."""
        if side is Side.YES:
            return self.yes_bid
        return None if self.yes_ask is None else 1.0 - self.yes_ask

    def hours_to_close(self, now: datetime | None = None) -> float | None:
        if self.close_time is None:
            return None
        now = now or datetime.now(timezone.utc)
        return (self.close_time - now).total_seconds() / 3600


@dataclass
class Order:
    market_id: str
    platform: str
    side: Side
    action: Action
    qty: int  # contracts
    limit_price: float  # per-contract price in [0, 1] for the chosen side
    reason: str = ""


@dataclass
class Fill:
    order: Order
    qty: int
    price: float
    fee: float
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Position:
    market_id: str
    platform: str
    side: Side
    qty: int
    avg_cost: float  # per contract, includes nothing but price (fees tracked in journal)
    question: str = ""
    category: str = ""

    @property
    def cost_basis(self) -> float:
        return self.qty * self.avg_cost

    def mark_to_market(self, market: Market) -> float:
        """Conservative liquidation value at current bid for our side."""
        px = market.price_to_sell(self.side)
        return self.qty * (px if px is not None else self.avg_cost)


class MarketConnector(ABC):
    """Read (and optionally trade) one platform."""

    platform: str

    @abstractmethod
    def list_markets(self, limit: int = 200) -> list[Market]:
        """Active markets, roughly ordered by activity/volume."""

    @abstractmethod
    def get_market(self, market_id: str) -> Market | None:
        """Refresh a single market's prices and metadata."""

    def list_weather_markets(self, limit: int = 300) -> list[Market]:
        """Weather markets only (the focus vertical); platforms without
        weather coverage return nothing."""
        return []

    def fee(self, price: float, qty: int, category: str = "") -> float:
        """Taker fee for `qty` contracts at `price`. Zero unless overridden."""
        return 0.0

    # Trading is optional: data-only connectors leave these unimplemented.
    def place_order(self, order: Order) -> Fill:
        raise NotImplementedError(f"{self.platform} connector is data-only")

    def resolved_outcome(self, market_id: str) -> Side | None:
        """Settlement outcome if the market has resolved, else None."""
        return None
