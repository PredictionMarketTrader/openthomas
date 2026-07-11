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


def test_default_ranking_is_by_volume():
    """No scorer: preserve the original behavior (volume descending), which
    cli/mcp scan still rely on."""
    scanner = EdgeScanner(RiskProfile.preset("conservative"))
    result = scanner.scan([
        market(id="lo", volume_24h=1_000),
        market(id="hi", volume_24h=90_000),
        market(id="mid", volume_24h=30_000),
    ])
    assert [m.id for m in result.candidates] == ["hi", "mid", "lo"]


def test_scorer_ranks_by_mispricing_not_volume():
    """The production bug: a high-gap, thin market must outrank a low-gap,
    heavily-traded one, because the forecast budget should chase mispricing."""
    scanner = EdgeScanner(RiskProfile.preset("conservative"))
    gaps = {"thin_edge": 0.30, "busy_agree": 0.01}
    result = scanner.scan(
        [market(id="busy_agree", volume_24h=90_000),
         market(id="thin_edge", volume_24h=1_000)],
        score_fn=lambda m: gaps[m.id],
    )
    assert [m.id for m in result.candidates] == ["thin_edge", "busy_agree"]


def test_unscoreable_markets_fall_after_scored_and_keep_volume_order():
    """A None score (not a weather market, or no baseline) ranks behind every
    scoreable market, and unscored markets keep volume order among themselves."""
    scanner = EdgeScanner(RiskProfile.preset("conservative"))
    scores = {"scored_lo": 0.05, "none_big": None, "none_small": None}
    result = scanner.scan(
        [market(id="none_small", volume_24h=2_000),
         market(id="none_big", volume_24h=80_000),
         market(id="scored_lo", volume_24h=1_000)],
        score_fn=lambda m: scores[m.id],
    )
    assert [m.id for m in result.candidates] == ["scored_lo", "none_big", "none_small"]


def test_equal_scores_break_ties_by_volume():
    scanner = EdgeScanner(RiskProfile.preset("conservative"))
    result = scanner.scan(
        [market(id="thin", volume_24h=1_000), market(id="thick", volume_24h=50_000)],
        score_fn=lambda m: 0.2,
    )
    assert [m.id for m in result.candidates] == ["thick", "thin"]


def test_a_scorer_that_raises_never_drops_the_candidate():
    """A blowup in the (I/O-touching) scorer must degrade to unscored, not
    silently lose a tradeable market."""
    scanner = EdgeScanner(RiskProfile.preset("conservative"))

    def score(m):
        if m.id == "boom":
            raise RuntimeError("assess failed")
        return 0.4

    result = scanner.scan(
        [market(id="boom", volume_24h=99_000), market(id="fine", volume_24h=1_000)],
        score_fn=score,
    )
    assert {m.id for m in result.candidates} == {"boom", "fine"}
    assert result.candidates[0].id == "fine"  # scored beats the raised-None one


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
