import pytest

from openthomas.markets.base import Action, Market, Order, Side
from openthomas.markets.paper import InsufficientLiquidity, PaperBroker
from openthomas.memory.journal import Journal


class FreeConnector:
    platform = "polymarket"

    def fee(self, price, qty, category=""):
        return 0.0


def market(**kw) -> Market:
    defaults = dict(id="m1", platform="polymarket", question="q?", category="sports",
                    yes_bid=0.58, yes_ask=0.60, volume_24h=1000, liquidity=50000)
    defaults.update(kw)
    return Market(**defaults)


def order(**kw) -> Order:
    defaults = dict(market_id="m1", platform="polymarket", side=Side.YES,
                    action=Action.BUY, qty=10, limit_price=0.60)
    defaults.update(kw)
    return Order(**defaults)


@pytest.fixture
def journal(tmp_path):
    return Journal(tmp_path / "j.db")


def test_paper_buy_fills_at_ask():
    broker = PaperBroker({"polymarket": FreeConnector()})
    fill = broker.execute(order(), market())
    assert fill.price == 0.60 and fill.qty == 10


def test_paper_respects_limit():
    broker = PaperBroker({"polymarket": FreeConnector()})
    with pytest.raises(InsufficientLiquidity):
        broker.execute(order(limit_price=0.55), market())  # ask is 0.60


def test_journal_positions_and_cash(journal):
    broker = PaperBroker({"polymarket": FreeConnector()})
    m = market()
    fill = broker.execute(order(), m)
    journal.record_fill(fill, m)

    positions = journal.positions()
    assert len(positions) == 1
    assert positions[0].qty == 10 and positions[0].avg_cost == 0.60
    assert journal.cash(bankroll=100.0) == pytest.approx(100 - 6.0)


def test_settlement_pnl_and_calibration_pairs(journal):
    broker = PaperBroker({"polymarket": FreeConnector()})
    m = market()
    journal.record_fill(broker.execute(order(), m), m)

    from openthomas.forecast.engine import Forecast
    journal.record_forecast(
        Forecast(market_id="m1", p_raw=0.72, p_calibrated=0.68, confidence=0.7), m
    )

    pos = journal.positions()[0]
    pnl = journal.record_settlement(pos, Side.YES)
    assert pnl == pytest.approx(10 * 1.0 - 6.0)
    assert journal.positions() == []  # settled markets drop out
    assert journal.cash(bankroll=100.0) == pytest.approx(104.0)
    assert journal.forecast_outcome_pairs() == [(0.72, 1)]
    stats = journal.settlement_stats()
    assert stats["n"] == 1 and stats["win_rate"] == 1.0


def test_average_down_cost_basis(journal):
    broker = PaperBroker({"polymarket": FreeConnector()})
    m1 = market()
    journal.record_fill(broker.execute(order(), m1), m1)
    m2 = market(yes_ask=0.50)
    journal.record_fill(broker.execute(order(limit_price=0.50), m2), m2)
    pos = journal.positions()[0]
    assert pos.qty == 20
    assert pos.avg_cost == pytest.approx(0.55)
