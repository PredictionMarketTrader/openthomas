"""The public feed: what it publishes, and what it must never publish."""

from __future__ import annotations

import json

import httpx
import pytest

from openthomas.config import ModelConfig, Settings
from openthomas.llm import CompletionClient
from openthomas.markets.base import Action, Fill, Market, Order, Side
from openthomas.memory.journal import Journal
from openthomas.memory.usage import Usage, UsageLedger, summarize
from openthomas.site.feed import build_feed, publish
from tests.test_llm import http_client


def market(mid: float = 0.40, market_id: str = "M1") -> Market:
    return Market(id=market_id, platform="kalshi", question="Will the high be >80F?",
                  category="climate/weather", yes_bid=mid - 0.01, yes_ask=mid + 0.01,
                  volume_24h=50_000, liquidity=50_000)


class Forecast:
    market_id = "M1"
    p_raw = 0.72
    p_calibrated = 0.70
    confidence = 0.8
    base_rate = 0.5
    market_gap_reason = "crowd anchors on the observed high"
    invalidation = "a marine layer forms overnight"
    reasoning = "R" * 5000
    model = "glm-5.2"


@pytest.fixture()
def settings(tmp_path) -> Settings:
    s = Settings(bankroll=1000.0, home=tmp_path)
    s.risk.min_edge = 0.08
    return s


def test_feed_reports_a_pending_edge_with_its_reasoning_truncated(settings):
    j = Journal(settings.db_path)
    j.record_forecast(Forecast(), market(mid=0.40), data="secret prompt", news="secret news")
    j.record_cycle(account_value=1100.0, cash=1100.0, n_positions=0)

    feed = build_feed(settings, j)
    (thesis,) = feed["theses"]
    assert thesis["status"] == "pending"  # forecast, no fill yet
    assert thesis["side"] == "yes"  # model 0.70 over market 0.40
    assert thesis["edge"] == pytest.approx(0.30)
    assert thesis["why"] == "crowd anchors on the observed high"
    assert thesis["invalidation"] == "a marine layer forms overnight"
    assert len(thesis["reasoning"]) == settings.site.max_reasoning_chars + 1  # + ellipsis
    assert feed["performance"]["account_value"] == 1100.0
    assert feed["performance"]["return_pct"] == pytest.approx(0.10)


def test_feed_never_leaks_prompt_inputs_or_venue_ids(settings):
    """data_text/news_text are prompt provenance, and market_id is an order
    handle. A `SELECT *` reaching the feed would ship all three."""
    j = Journal(settings.db_path)
    j.record_forecast(Forecast(), market(), data="GFS grid dump", news="paywalled article")

    blob = json.dumps(build_feed(settings, j))
    assert "GFS grid dump" not in blob
    assert "paywalled article" not in blob
    assert "M1" not in blob


def test_a_traded_market_leaves_the_outlook_and_a_held_one_stays(settings):
    j = Journal(settings.db_path)
    j.record_forecast(Forecast(), market())
    order = Order(market_id="M1", platform="kalshi", side=Side.YES, action=Action.BUY,
                  qty=10, limit_price=0.41, reason="edge")
    j.record_fill(Fill(order=order, qty=10, price=0.41, fee=0.05), market())

    (thesis,) = build_feed(settings, j)["theses"]
    assert thesis["status"] == "held"

    j.record_settlement(j.positions()[0], Side.YES)
    feed = build_feed(settings, j)
    assert feed["theses"] == []  # settled: it is track record now, not outlook
    assert feed["track_record"][0]["outcome"] == "yes"


def test_a_stale_untaken_edge_is_not_advertised_as_a_pending_bet(settings):
    j = Journal(settings.db_path)
    j.record_forecast(Forecast(), market())
    j.db.execute("UPDATE forecasts SET ts = '2020-01-01T00:00:00+00:00'")
    j.db.commit()
    assert build_feed(settings, j)["theses"] == []


def test_an_edge_under_the_bar_is_not_a_thesis(settings):
    j = Journal(settings.db_path)
    j.record_forecast(Forecast(), market(mid=0.68))  # 0.70 vs 0.68 = 0.02 < min_edge
    assert build_feed(settings, j)["theses"] == []


def test_a_serving_alias_is_not_published_as_the_model_name(settings):
    """vLLM's --served-model-name can be anything. Unset, the feed falls back to
    whatever the endpoint is called; set, it names the model and links weights."""
    settings.forecaster.model = "og-coding"
    assert build_feed(settings, Journal(settings.db_path))["agent"]["forecaster"] == {
        "label": "og-coding", "url": ""}

    settings.site.model_label = "GLM-5.2 (NVFP4)"
    settings.site.model_url = "https://huggingface.co/nvidia/GLM-5.2-NVFP4"
    assert build_feed(settings, Journal(settings.db_path))["agent"]["forecaster"] == {
        "label": "GLM-5.2 (NVFP4)", "url": "https://huggingface.co/nvidia/GLM-5.2-NVFP4"}


def test_links_are_omitted_rather_than_rendered_empty(settings):
    links = build_feed(settings, Journal(settings.db_path))["links"]
    assert links["x"] == "" and links["huggingface"] == ""  # the page hides both

    settings.site.x_handle = "openthomas"
    settings.site.huggingface = "https://huggingface.co/openthomas"
    links = build_feed(settings, Journal(settings.db_path))["links"]
    assert links["x"] == "https://x.com/openthomas"
    assert links["huggingface"] == "https://huggingface.co/openthomas"


def test_publish_writes_feed_json_atomically(settings, tmp_path):
    Journal(settings.db_path)
    path = publish(settings, tmp_path / "site")
    assert path.name == "feed.json"
    assert json.loads(path.read_text())["schema_version"] == 1
    assert not list(path.parent.glob("*.tmp"))


def test_compute_dates_the_ledger_so_zero_tokens_is_not_read_as_cheap(settings):
    j = Journal(settings.db_path)
    assert build_feed(settings, j)["compute"]["ledger_started"] is None

    UsageLedger(settings.home).record(
        Usage(ts="2026-07-10T00:00:00+00:00", node="forecast", provider="openai",
              model="glm-5.2", prompt_tokens=1000, completion_tokens=200))
    compute = build_feed(settings, j)["compute"]
    assert compute["ledger_started"] == "2026-07-10T00:00:00+00:00"
    assert compute["total"]["total_tokens"] == 1200


# --- token ledger ----------------------------------------------------------------
def test_openai_usage_is_recorded_per_call():
    seen: list[Usage] = []
    body = {"choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 100,
                      "prompt_tokens_details": {"cached_tokens": 800}}}
    client = CompletionClient(
        ModelConfig(provider="openai", model="glm-5.2", base_url="http://x/v1"),
        http=http_client(lambda req: httpx.Response(200, json=body)),
        usage_sink=seen.append, node="forecast")

    assert client.complete("s", "u") == "ok"
    assert seen == [Usage(ts=seen[0].ts, node="forecast", provider="openai", model="glm-5.2",
                          prompt_tokens=900, completion_tokens=100, cached_tokens=800)]


def test_a_subscription_cli_call_is_counted_but_its_tokens_are_not_invented():
    """`claude -p` bills a flat rate and reports nothing. Recording 0 tokens
    would understate training cost; the summary keeps those calls apart."""
    seen: list[Usage] = []

    class Proc:
        returncode, stdout, stderr = 0, "answer", ""

    client = CompletionClient(ModelConfig(provider="claude-cli", model="sonnet"),
                              run=lambda *a, **k: Proc(), usage_sink=seen.append,
                              node="propose")
    client.complete("s", "u")

    assert seen[0].total_tokens is None
    summary = summarize(seen)
    assert summary["total"] == {"calls": 1, "prompt_tokens": 0, "completion_tokens": 0,
                                "total_tokens": 0, "calls_without_usage": 1}


def test_summarize_cuts_spend_by_node_model_and_day():
    rows = [
        Usage(ts="2026-07-09T10:00:00+00:00", node="forecast", provider="openai",
              model="glm-5.2", prompt_tokens=100, completion_tokens=10),
        Usage(ts="2026-07-10T10:00:00+00:00", node="replay", provider="openai",
              model="glm-5.2", prompt_tokens=500, completion_tokens=50),
    ]
    s = summarize(rows)
    assert s["total"]["total_tokens"] == 660
    assert [n["node"] for n in s["by_node"]] == ["replay", "forecast"]  # ranked by spend
    assert [d["day"] for d in s["by_day"]] == ["2026-07-09", "2026-07-10"]  # chronological
    assert s["by_model"][0] == {"model": "glm-5.2", "calls": 2, "prompt_tokens": 600,
                                "completion_tokens": 60, "total_tokens": 660,
                                "calls_without_usage": 0}


def test_a_torn_ledger_line_never_breaks_the_feed(settings):
    ledger = UsageLedger(settings.home)
    ledger.record(Usage(ts="2026-07-10T00:00:00+00:00", node="forecast", provider="openai",
                        model="m", prompt_tokens=5, completion_tokens=5))
    with ledger.path.open("a") as fh:
        fh.write('{"ts": "2026-07-10T01:00')  # process died mid-write
    assert summarize(ledger.read())["total"]["calls"] == 1
