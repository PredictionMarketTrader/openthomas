"""The forecast-budget ranking wired into the Agent.

The scanner ranks candidates by a mispricing proxy; the Agent supplies that
proxy from the statistical baseline and memoizes the assessment so ranking and
the forecast loop share one call per market. These are the seams that decide
which markets get the cycle's scarce LLM budget.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from openthomas.agent.loop import Agent
from openthomas.config import Settings
from openthomas.markets.base import Market


def agent(tmp_path) -> Agent:
    return Agent(Settings(home=tmp_path, news_enabled=False))


def market(mid: float, **kw) -> Market:
    bid = round(mid - 0.01, 3)
    ask = round(mid + 0.01, 3)
    defaults = dict(id="m", platform="kalshi", question="Will the high be >80F?",
                    category="climate/weather", yes_bid=bid, yes_ask=ask,
                    volume_24h=10_000, liquidity=20_000,
                    close_time=datetime.now(timezone.utc) + timedelta(days=1))
    defaults.update(kw)
    return Market(**defaults)


class Assessment:
    """Minimal stand-in for WeatherAssessment: only what the scorer reads."""
    def __init__(self, p_base):
        self.p_base = p_base
        self.decided = False


def test_assess_is_memoized_per_cycle(tmp_path):
    a = agent(tmp_path)
    calls = []
    a.weather.assess = lambda m: calls.append(m.id) or Assessment(0.7)

    cache: dict = {}
    m = market(0.4)
    first = a._assess(m, cache)
    second = a._assess(m, cache)
    assert first is second
    assert calls == ["m"]  # one call, reused


def test_a_failing_assessment_is_cached_as_none_not_retried(tmp_path):
    a = agent(tmp_path)
    calls = []

    def boom(m):
        calls.append(m.id)
        raise RuntimeError("network")

    a.weather.assess = boom
    cache: dict = {}
    m = market(0.4)
    assert a._assess(m, cache) is None
    assert a._assess(m, cache) is None
    assert calls == ["m"]  # the failure is remembered, not hammered


def test_baseline_gap_is_distance_from_the_market(tmp_path):
    a = agent(tmp_path)
    a.weather.assess = lambda m: Assessment(0.72)
    assert a._baseline_gap(market(0.40), {}) == pytest.approx(0.32)


def test_baseline_gap_is_none_when_no_baseline(tmp_path):
    a = agent(tmp_path)
    a.weather.assess = lambda m: Assessment(None)  # e.g. no NWP models for this strike
    assert a._baseline_gap(market(0.40), {}) is None


def test_a_thin_high_gap_market_wins_the_budget_over_a_busy_agreeing_one(tmp_path):
    """End to end through the real EdgeScanner: the scorer the Agent builds must
    reorder a busy, correctly-priced market behind a thin, mispriced one."""
    a = agent(tmp_path)
    # baseline says 0.85; the busy market is priced right at 0.84, the thin one
    # is priced at 0.40 — a 0.45 gap the crowd hasn't closed.
    bases = {"busy": 0.85, "thin": 0.85}
    a.weather.assess = lambda m: Assessment(bases[m.id])

    busy = market(0.84, id="busy", volume_24h=90_000)
    thin = market(0.40, id="thin", volume_24h=1_000)

    cache: dict = {}
    result = a.scanner.scan([busy, thin],
                            score_fn=lambda m: a._baseline_gap(m, cache))
    assert [m.id for m in result.candidates] == ["thin", "busy"]
    # and the shared cache means each was assessed once, available to the loop
    assert set(cache) == {"busy", "thin"}
