from datetime import datetime, timedelta, timezone

from openthomas.config import RiskProfile
from openthomas.edge.scanner import EdgeScanner, question_similarity
from openthomas.markets.base import Market


def market(**kw) -> Market:
    defaults = dict(
        id="x", platform="polymarket", question="Will the Fed cut rates in September?",
        category="economics", yes_bid=0.40, yes_ask=0.42, volume_24h=50000, liquidity=20000,
        close_time=datetime.now(timezone.utc) + timedelta(days=30),
    )
    defaults.update(kw)
    return Market(**defaults)


def test_filters_illiquid_and_extreme():
    scanner = EdgeScanner(RiskProfile.preset("conservative"))
    result = scanner.scan([
        market(id="ok"),
        market(id="thin", liquidity=10),
        market(id="longshot", yes_bid=0.01, yes_ask=0.02),
        market(id="wide", yes_bid=0.30, yes_ask=0.55),
        market(id="closing", close_time=datetime.now(timezone.utc) + timedelta(minutes=20)),
    ])
    assert [m.id for m in result.candidates] == ["ok"]
    assert result.skipped == {"illiquid": 1, "extreme_price": 1, "wide_spread": 1,
                              "closing_too_soon": 1}


def test_cross_platform_arb_detected():
    scanner = EdgeScanner(RiskProfile.preset("conservative"))
    a = market(id="pm", platform="polymarket", yes_bid=0.38, yes_ask=0.40)
    b = market(id="k", platform="kalshi",
               question="Fed cuts rates at the September meeting?",
               yes_bid=0.50, yes_ask=0.52)
    arbs = scanner.find_cross_platform_arbs([a, b])
    # buy YES on polymarket at 0.40, buy NO on kalshi at 1-0.50=0.50 → gap 0.10
    assert arbs and abs(arbs[0].gross_gap - 0.10) < 1e-9


def test_same_platform_not_arbed():
    scanner = EdgeScanner(RiskProfile.preset("conservative"))
    a = market(id="a", yes_bid=0.38, yes_ask=0.40)
    b = market(id="b", yes_bid=0.50, yes_ask=0.52)
    assert scanner.find_cross_platform_arbs([a, b]) == []


def test_question_similarity():
    assert question_similarity("Will the Fed cut rates in September?",
                               "Fed cuts rates at the September meeting?") > 0.4
    assert question_similarity("Will the Fed cut rates?",
                               "Will the Lakers win the championship?") < 0.2
