"""The daily dispatch: honest, bounded, and never posts on its own."""

from __future__ import annotations

import pytest

from openthomas.config import Settings
from openthomas.memory.journal import Journal
from openthomas.report.dispatch import LIMIT, daily_text, post_to_x


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(bankroll=1000.0, home=tmp_path)


def test_daily_text_shows_the_loss_and_fits_the_limit(settings):
    """A down day is content too. The return is published with its sign, and the
    post always fits X's ceiling and points back to the site of record."""
    j = Journal(settings.db_path)
    j.record_cycle(account_value=990.0, cash=990.0, n_positions=0)  # -1% on $1,000

    text = daily_text(j, settings)
    assert len(text) <= LIMIT
    assert "-1.00%" in text  # the loss is stated, not hidden
    assert "openthomas.com" in text
    assert settings.mode in text  # "paper" — never implies real money


def test_daily_text_counts_only_the_given_days_settlements(settings):
    from openthomas.markets.base import Action, Fill, Market, Order, Side

    m = Market(id="M1", platform="kalshi", question="Will the high be >80F?",
               category="climate/weather", yes_bid=0.39, yes_ask=0.41)
    j = Journal(settings.db_path)
    order = Order(market_id="M1", platform="kalshi", side=Side.YES, action=Action.BUY,
                  qty=10, limit_price=0.41, reason="edge")
    j.record_fill(Fill(order=order, qty=10, price=0.41, fee=0.0), m)
    pnl = j.record_settlement(j.positions()[0], Side.YES)  # a win, dated today

    today = j.recent_settlements(1)[0]["ts"][:10]
    assert "1 settled" in daily_text(j, settings, day=today)
    assert "No settlements today" in daily_text(j, settings, day="2000-01-01")
    assert pnl > 0


def test_post_to_x_refuses_without_credentials(settings, monkeypatch):
    """Posting is opt-in and outward-facing: with no keys in the environment it
    fails loudly with the fix, rather than silently swallowing the post."""
    for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="credentials"):
        post_to_x("hello", settings)
