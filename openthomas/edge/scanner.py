"""Edge scanner: cheap structural filters that run BEFORE any LLM call.

Purpose: spend forecast tokens only where a mispricing is plausible, and
surface arbitrage that needs no forecast at all.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from ..config import RiskProfile
from ..markets.base import Market, Side


@dataclass
class ArbOpportunity:
    """Same event priced differently across platforms: buy YES on the cheap
    venue, NO on the dear one, lock in the gap minus fees."""

    market_a: Market
    market_b: Market
    gross_gap: float  # buy-yes(a) + buy-no(b) < 1 → gap = 1 − that sum
    similarity: float

    def describe(self) -> str:
        return (
            f"{self.gross_gap:+.3f} gap | {self.market_a.platform}:{self.market_a.question[:60]}"
            f" vs {self.market_b.platform}:{self.market_b.question[:60]}"
        )


@dataclass
class ScanResult:
    candidates: list[Market] = field(default_factory=list)
    arbs: list[ArbOpportunity] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)


_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"will", "the", "a", "an", "of", "in", "on", "by", "be", "to", "at", "or", "and", "for"}


def _tokens(text: str) -> set[str]:
    words = (w for w in _WORD.findall(text.lower()) if w not in _STOP)
    # crude plural/verb-s stemming so "cut rates" matches "cuts rates"
    return {w.rstrip("s") if len(w) > 3 else w for w in words}


def question_similarity(a: str, b: str) -> float:
    """Jaccard similarity on content words. Crude but cheap; the agent loop
    re-verifies resolution rules with the LLM before acting on a match."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


class EdgeScanner:
    def __init__(self, profile: RiskProfile):
        self.profile = profile

    def _skip_reason(self, m: Market) -> str | None:
        p = self.profile
        if m.yes_bid is None or m.yes_ask is None:
            return "no_quote"
        if m.liquidity < p.min_liquidity:
            return "illiquid"
        if m.category and m.category in p.categories_blocked:
            return "category_blocked"
        if not (p.min_price <= m.mid <= p.max_price):
            return "extreme_price"
        if m.spread is not None and m.spread > 0.10:
            return "wide_spread"
        hours = m.hours_to_close()
        if hours is not None and hours < 1:
            return "closing_too_soon"
        return None

    def scan(self, markets: list[Market],
             score_fn: Callable[[Market], float | None] | None = None) -> ScanResult:
        result = ScanResult()
        for m in markets:
            reason = self._skip_reason(m)
            if reason:
                result.skipped[reason] = result.skipped.get(reason, 0) + 1
            else:
                result.candidates.append(m)
        result.candidates = self._rank(result.candidates, score_fn)
        result.arbs = self.find_cross_platform_arbs(result.candidates)
        return result

    def _rank(self, candidates: list[Market],
              score_fn: Callable[[Market], float | None] | None) -> list[Market]:
        """Order candidates by forecast priority — this decides which markets
        get the cycle's scarce LLM budget.

        Default is 24h volume, a liquidity proxy. But liquidity is already a
        hard filter (`_skip_reason`), so volume ranks the survivors on an axis
        that has nothing to do with whether they are *mispriced* — the scanner's
        actual job. `score_fn(market) -> float | None` lets the caller inject a
        cheap mispricing proxy (e.g. the statistical baseline's distance from the
        market price): markets it can score rank by that, descending, ahead of
        the ones it cannot (None), which fall back to volume. Volume also breaks
        ties among equally-scored markets.
        """
        if score_fn is None:
            return sorted(candidates, key=lambda m: m.volume_24h, reverse=True)
        scores: dict[str, float | None] = {}
        for m in candidates:
            try:
                scores[m.id] = score_fn(m)
            except Exception:
                scores[m.id] = None  # a scorer failure must never drop a candidate

        def key(m: Market) -> tuple[bool, float, float]:
            s = scores[m.id]
            # All three descending under reverse=True: scoreable first, then by
            # score, then by volume. Unscoreable markets get 0.0 but the leading
            # False already sorts them behind every scored one.
            return (s is not None, s if s is not None else 0.0, m.volume_24h)

        return sorted(candidates, key=key, reverse=True)

    def find_cross_platform_arbs(
        self, markets: list[Market], min_gap: float = 0.02, min_similarity: float = 0.55
    ) -> list[ArbOpportunity]:
        by_platform: dict[str, list[Market]] = {}
        for m in markets:
            by_platform.setdefault(m.platform, []).append(m)
        platforms = list(by_platform)
        arbs: list[ArbOpportunity] = []
        for i, pa in enumerate(platforms):
            for pb in platforms[i + 1:]:
                for a in by_platform[pa]:
                    for b in by_platform[pb]:
                        sim = question_similarity(a.question, b.question)
                        if sim < min_similarity:
                            continue
                        # Two directions: yes(a)+no(b) or yes(b)+no(a).
                        for x, y in ((a, b), (b, a)):
                            buy_yes = x.price_to_buy(Side.YES)
                            buy_no = y.price_to_buy(Side.NO)
                            if buy_yes is None or buy_no is None:
                                continue
                            gap = 1 - (buy_yes + buy_no)
                            if gap >= min_gap:
                                arbs.append(ArbOpportunity(x, y, gap, sim))
        arbs.sort(key=lambda a: a.gross_gap, reverse=True)
        return arbs
