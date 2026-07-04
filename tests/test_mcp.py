import asyncio

import pytest

pytest.importorskip("mcp")

EXPECTED = {
    "scan_markets", "get_market", "research_news", "forecast_market",
    "propose_trade", "portfolio_status", "performance_report",
}


def test_all_tools_registered():
    from openthomas.mcp_server import mcp

    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert EXPECTED <= names


def test_tool_descriptions_carry_the_contract():
    from openthomas.mcp_server import mcp

    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    # The propose_trade docstring is the behavioral contract clients see.
    desc = tools["propose_trade"].description
    assert "Kelly" in desc and "rejection" in desc


def test_propose_trade_validates_probability():
    from openthomas.mcp_server import propose_trade

    assert "error" in propose_trade("polymarket", "x", probability=1.5, reason="r")
