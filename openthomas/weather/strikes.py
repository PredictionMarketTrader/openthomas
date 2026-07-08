"""Strike semantics for scalar temperature markets.

Kalshi supplies strike_type / floor_strike / cap_strike structurally:
  greater → YES iff T > floor    ("84° or above" when floor=83, integer °F)
  less    → YES iff T < cap
  between → YES iff floor ≤ T ≤ cap  (both ends inclusive)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Strike:
    kind: str  # "greater" | "less" | "between"
    lo: float | None = None
    hi: float | None = None

    def covers(self, temp: float) -> bool:
        """Does `temp` resolve this market YES?"""
        if self.kind == "greater":
            return temp > self.lo
        if self.kind == "less":
            return temp < self.hi
        return self.lo <= temp <= self.hi

    def describe(self) -> str:
        if self.kind == "greater":
            return f"> {self.lo:g}°F"
        if self.kind == "less":
            return f"< {self.hi:g}°F"
        return f"{self.lo:g}°F to {self.hi:g}°F (inclusive)"


def parse_strike(market) -> Strike | None:
    t = market.strike_type
    if t == "greater" and market.floor_strike is not None:
        return Strike("greater", lo=market.floor_strike)
    if t == "less" and market.cap_strike is not None:
        return Strike("less", hi=market.cap_strike)
    if t == "between" and market.floor_strike is not None and market.cap_strike is not None:
        return Strike("between", lo=market.floor_strike, hi=market.cap_strike)
    return None
