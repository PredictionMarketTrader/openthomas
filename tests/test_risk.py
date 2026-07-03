from openthomas.config import RiskProfile
from openthomas.markets.base import Market, Position, Side
from openthomas.risk.engine import PortfolioState, RiskEngine, kelly_fraction


def market(**kw) -> Market:
    defaults = dict(id="m1", platform="kalshi", question="Will it rain?", category="weather",
                    yes_bid=0.48, yes_ask=0.50, volume_24h=5000, liquidity=10000)
    defaults.update(kw)
    return Market(**defaults)


def state(**kw) -> PortfolioState:
    defaults = dict(bankroll=1000.0, cash=1000.0, positions=[], peak_value=1000.0,
                    account_value=1000.0)
    defaults.update(kw)
    return PortfolioState(**defaults)


def test_kelly_fraction_positive_edge():
    # p=0.65, cost=0.50 → f* = 0.15/0.50 = 0.30
    assert abs(kelly_fraction(0.65, 0.50) - 0.30) < 1e-9


def test_kelly_fraction_no_edge_is_negative():
    assert kelly_fraction(0.40, 0.50) < 0


def test_rejects_below_min_edge():
    engine = RiskEngine(RiskProfile.preset("conservative"))
    verdict = engine.size_entry(state(), market(), Side.YES, p_calibrated=0.54)
    assert not verdict.approved
    assert "edge" in verdict.reason


def test_approves_and_sizes_with_edge():
    engine = RiskEngine(RiskProfile.preset("conservative"))
    verdict = engine.size_entry(state(), market(), Side.YES, p_calibrated=0.70)
    assert verdict.approved
    # kelly f* = 0.4, sized at 0.15× = 6% of bankroll, but per-market cap is 5%
    assert verdict.qty == int(1000 * 0.05 / 0.50)


def test_per_market_cap_counts_existing_position():
    engine = RiskEngine(RiskProfile.preset("conservative"))
    existing = Position(market_id="m1", platform="kalshi", side=Side.YES, qty=100,
                        avg_cost=0.50, category="weather")
    verdict = engine.size_entry(state(positions=[existing], cash=950), market(),
                                Side.YES, p_calibrated=0.70)
    assert not verdict.approved  # 100 × 0.50 = $50 already = full 5% cap
    assert "per-market" in verdict.reason


def test_category_cap_binds():
    profile = RiskProfile.preset("conservative")
    engine = RiskEngine(profile)
    positions = [
        Position(market_id=f"w{i}", platform="kalshi", side=Side.YES, qty=100,
                 avg_cost=0.50, category="weather")
        for i in range(5)
    ]  # $250 in weather = 25% cap reached
    verdict = engine.size_entry(state(positions=positions, cash=750), market(id="m9"),
                                Side.YES, p_calibrated=0.70)
    assert not verdict.approved
    assert "per-category" in verdict.reason


def test_drawdown_kill_switch():
    engine = RiskEngine(RiskProfile.preset("conservative"))  # max_drawdown 0.15
    s = state(account_value=840.0, peak_value=1000.0)
    verdict = engine.size_entry(s, market(), Side.YES, p_calibrated=0.90)
    assert not verdict.approved
    assert "kill-switch" in verdict.reason


def test_longshot_zone_rejected():
    engine = RiskEngine(RiskProfile.preset("conservative"))
    verdict = engine.size_entry(state(), market(yes_bid=0.02, yes_ask=0.03),
                                Side.YES, p_calibrated=0.20)
    assert not verdict.approved
    assert "longshot" in verdict.reason


def test_no_side_uses_complement_probability():
    engine = RiskEngine(RiskProfile.preset("conservative"))
    # forecast p(YES)=0.30, buying NO at 1-0.48=0.52 → p_no=0.70, edge 0.18
    verdict = engine.size_entry(state(), market(), Side.NO, p_calibrated=0.30)
    assert verdict.approved


def test_fees_reduce_edge():
    engine = RiskEngine(RiskProfile.preset("conservative"))
    no_fee = engine.size_entry(state(), market(), Side.YES, 0.59)
    with_fee = engine.size_entry(state(), market(), Side.YES, 0.59, fee_per_contract=0.02)
    assert no_fee.approved and not with_fee.approved
