"""Kalshi connector.

Public market data needs no auth. Live trading signs requests with an RSA key
(KALSHI-ACCESS-* headers); create one at kalshi.com → account → API keys.
"""

from __future__ import annotations

import base64
import math
import os
import time
from datetime import datetime

import httpx

from .base import Fill, Market, MarketConnector, Order, Side

# The recommended host since 2026 (the legacy api.elections.kalshi.com endpoint
# carries 5-10x rate-limit token costs). Set KALSHI_DEMO=1 for the paper-money
# demo exchange (separate signup at demo.kalshi.co).
API = "https://external-api.kalshi.com/trade-api/v2"
DEMO_API = "https://external-api.demo.kalshi.co/trade-api/v2"


class KalshiConnector(MarketConnector):
    platform = "kalshi"

    def __init__(self, client: httpx.Client | None = None):
        base = DEMO_API if os.environ.get("KALSHI_DEMO") else API
        self.http = client or httpx.Client(base_url=base, timeout=30)
        self.key_id = os.environ.get("KALSHI_API_KEY_ID")
        self.private_key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")

    def _to_market(self, m: dict, category: str = "") -> Market:
        def dollars(*keys) -> float | None:
            """Prices arrive as '0.0400' strings in *_dollars fields (the older
            integer-cent fields are deprecated and now return null)."""
            for key in keys:
                v = m.get(key)
                if v not in (None, ""):
                    return float(v) / (100 if key.endswith(("_bid", "_ask")) else 1)
            return None

        close = m.get("close_time")
        # A subtitle like 'T98' plus a title with markdown emphasis: clean both up.
        title = (m.get("title") or "").replace("**", "")
        return Market(
            id=m["ticker"],
            platform=self.platform,
            question=title,
            category=(category or m.get("category") or "").lower(),
            event_id=m.get("event_ticker", ""),
            yes_bid=dollars("yes_bid_dollars", "yes_bid"),
            yes_ask=dollars("yes_ask_dollars", "yes_ask"),
            volume_24h=float(m.get("volume_24h_fp") or m.get("volume_24h") or 0),
            liquidity=float(m.get("liquidity_dollars") or 0)
            or float(m.get("liquidity") or 0) / 100,
            close_time=datetime.fromisoformat(close.replace("Z", "+00:00")) if close else None,
            resolution_rules=m.get("rules_primary", ""),
            url=f"https://kalshi.com/markets/{m.get('event_ticker', m['ticker'])}",
        )

    def list_markets(self, limit: int = 200) -> list[Market]:
        """Open markets with real quotes, discovered via /events (which carries the
        category and groups markets that settle together). The raw /markets listing
        is cursor-ordered and front-loaded with quoteless parlay combos."""
        markets: list[Market] = []
        cursor = None
        for _ in range(40):  # page cap: don't hammer the API
            params: dict = {"status": "open", "with_nested_markets": "true", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            data = self.http.get("/events", params=params).json()
            for event in data.get("events", []):
                if event.get("mve_collection_ticker"):
                    continue  # skip parlay/multivariate collections
                for raw in event.get("markets") or []:
                    m = self._to_market(raw, category=event.get("category", ""))
                    if m.yes_bid and m.yes_ask:
                        markets.append(m)
            cursor = data.get("cursor")
            if not cursor or len(markets) >= limit * 3:
                break
        markets.sort(key=lambda m: (m.volume_24h, m.liquidity), reverse=True)
        return markets[:limit]

    def get_market(self, market_id: str) -> Market | None:
        data = self.http.get(f"/markets/{market_id}").json()
        return self._to_market(data["market"]) if "market" in data else None

    def resolved_outcome(self, market_id: str) -> Side | None:
        data = self.http.get(f"/markets/{market_id}").json()
        result = data.get("market", {}).get("result")
        return {"yes": Side.YES, "no": Side.NO}.get(result)

    def fee(self, price: float, qty: int, category: str = "") -> float:
        """Kalshi taker fee: ceil(7% * qty * p * (1-p)), in dollars per lot."""
        return math.ceil(7 * qty * price * (1 - price)) / 100

    # --- authenticated trading -------------------------------------------------
    def _signed_headers(self, method: str, path: str) -> dict:
        if not (self.key_id and self.private_key_path):
            raise NotImplementedError(
                "Live Kalshi trading needs KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH; "
                "see docs/LIVE_TRADING.md"
            )
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        ts = str(int(time.time() * 1000))
        message = f"{ts}{method}/trade-api/v2{path}".encode()
        with open(self.private_key_path, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        sig = key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
            "KALSHI-ACCESS-TIMESTAMP": ts,
        }

    def place_order(self, order: Order) -> Fill:
        payload = {
            "ticker": order.market_id,
            "action": order.action.value,
            "side": order.side.value,
            "count": order.qty,
            "type": "limit",
            f"{order.side.value}_price": round(order.limit_price * 100),
            "client_order_id": f"openthomas-{int(time.time() * 1000)}",
        }
        resp = self.http.post(
            "/portfolio/orders", json=payload, headers=self._signed_headers("POST", "/portfolio/orders")
        )
        resp.raise_for_status()
        data = resp.json()["order"]
        filled = int(data.get("taker_fill_count") or 0)
        avg = (data.get("taker_fill_cost") or 0) / 100 / max(filled, 1)
        return Fill(order=order, qty=filled, price=avg or order.limit_price,
                    fee=self.fee(order.limit_price, filled))
