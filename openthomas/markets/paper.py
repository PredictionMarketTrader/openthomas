"""Paper broker: real market data, simulated fills.

Deliberately conservative, mirroring the Prediction Arena methodology:
buys fill at the ask, sells at the bid (never at mid), fees are charged using
the real platform's fee model, and fills are capped by displayed liquidity.
"""

from __future__ import annotations

from .base import Action, Fill, Market, MarketConnector, Order


class InsufficientLiquidity(Exception):
    pass


class PaperBroker:
    def __init__(self, connectors: dict[str, MarketConnector]):
        self.connectors = connectors

    def execute(self, order: Order, market: Market) -> Fill:
        connector = self.connectors[order.platform]
        if order.action is Action.BUY:
            px = market.price_to_buy(order.side)
        else:
            px = market.price_to_sell(order.side)
        if px is None or px <= 0 or px >= 1:
            raise InsufficientLiquidity(f"no quote for {order.market_id} {order.side.value}")
        # A limit order only fills if the book is at or better than our limit.
        if order.action is Action.BUY and px > order.limit_price:
            raise InsufficientLiquidity(
                f"ask {px:.2f} above limit {order.limit_price:.2f} for {order.market_id}"
            )
        if order.action is Action.SELL and px < order.limit_price:
            raise InsufficientLiquidity(
                f"bid {px:.2f} below limit {order.limit_price:.2f} for {order.market_id}"
            )
        qty = order.qty
        if market.liquidity:
            max_qty = int(market.liquidity * 0.02 / max(px, 0.01))  # ≤2% of displayed depth
            qty = min(qty, max(max_qty, 1))
        return Fill(order=order, qty=qty, price=px, fee=connector.fee(px, qty, market.category))
