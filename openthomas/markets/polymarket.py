"""Polymarket connector.

Market discovery and prices via the public Gamma API (no auth). Live order
placement requires the optional `py-clob-client` dependency plus a funded
Polygon wallet; see docs/LIVE_TRADING.md.
"""

from __future__ import annotations

import json
from datetime import datetime

import httpx

from .base import Fill, Market, MarketConnector, Order

GAMMA = "https://gamma-api.polymarket.com"


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class PolymarketConnector(MarketConnector):
    platform = "polymarket"

    def __init__(self, client: httpx.Client | None = None):
        self.http = client or httpx.Client(base_url=GAMMA, timeout=30)

    def _to_market(self, m: dict) -> Market | None:
        outcomes = m.get("outcomes")
        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes or "[]")
        # Only binary Yes/No markets for now; multi-outcome events arrive as
        # one binary market per outcome sharing an event id.
        if outcomes and [o.lower() for o in outcomes] != ["yes", "no"]:
            return None
        events = m.get("events") or []
        event = events[0] if events else {}
        bid, ask = m.get("bestBid"), m.get("bestAsk")
        return Market(
            id=m.get("conditionId") or str(m.get("id")),
            platform=self.platform,
            question=m.get("question", ""),
            category=(event.get("category") or m.get("category") or "").lower(),
            event_id=str(event.get("id") or ""),
            yes_bid=float(bid) if bid is not None else None,
            yes_ask=float(ask) if ask is not None else None,
            volume_24h=float(m.get("volume24hr") or 0),
            liquidity=float(m.get("liquidityNum") or m.get("liquidity") or 0),
            close_time=_parse_ts(m.get("endDate")),
            resolution_rules=m.get("description", ""),
            url=f"https://polymarket.com/market/{m.get('slug', '')}",
        )

    def list_markets(self, limit: int = 200) -> list[Market]:
        markets: list[Market] = []
        offset = 0
        while len(markets) < limit:
            page = self.http.get(
                "/markets",
                params={
                    "active": "true", "closed": "false", "limit": min(100, limit),
                    "offset": offset, "order": "volume24hr", "ascending": "false",
                },
            ).json()
            if not page:
                break
            for raw in page:
                market = self._to_market(raw)
                if market is not None:
                    markets.append(market)
            offset += len(page)
        return markets[:limit]

    def get_market(self, market_id: str) -> Market | None:
        page = self.http.get("/markets", params={"condition_ids": market_id}).json()
        return self._to_market(page[0]) if page else None

    def resolved_outcome(self, market_id: str):
        from .base import Side

        page = self.http.get("/markets", params={"condition_ids": market_id}).json()
        if not page:
            return None
        m = page[0]
        if not m.get("closed"):
            return None
        prices = m.get("outcomePrices")
        if isinstance(prices, str):
            prices = json.loads(prices or "[]")
        if not prices:
            return None
        # Settled binary markets report ["1", "0"] (yes won) or ["0", "1"].
        return Side.YES if float(prices[0]) > 0.5 else Side.NO

    # Taker fees since ~March 2026: shares × rate × p × (1−p), rate by category.
    # Makers pay zero. https://docs.polymarket.com/trading/fees
    FEE_RATES = {
        "crypto": 0.07, "sports": 0.03, "finance": 0.04, "politics": 0.04,
        "tech": 0.04, "mentions": 0.04, "economics": 0.05, "culture": 0.05,
        "weather": 0.05, "geopolitics": 0.0, "world": 0.0,
    }
    DEFAULT_FEE_RATE = 0.05

    def fee(self, price: float, qty: int, category: str = "") -> float:
        rate = self.FEE_RATES.get(category, self.DEFAULT_FEE_RATE)
        return qty * rate * price * (1 - price)

    def place_order(self, order: Order) -> Fill:
        raise NotImplementedError(
            "Live Polymarket trading needs the official SDK (pip install --pre "
            "polymarket-client) and a funded wallet; see docs/LIVE_TRADING.md. "
            "Note: the pre-2026 py-clob-client is archived and non-functional (pUSD migration)."
        )
