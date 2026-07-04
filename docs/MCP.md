# MCP server — drive OpenThomas from Claude or any agent

OpenThomas ships an [MCP](https://modelcontextprotocol.io) server so Claude
Code, Claude Desktop, OpenClaw, Hermes, or any MCP client can use it as a
**prediction-market backend with built-in discipline**: your agent brings the
market view; OpenThomas enforces sizing and risk.

```bash
pip install 'openthomas[mcp]'
openthomas init            # once: bankroll, risk profile
```

**Claude Code**

```bash
claude mcp add openthomas -- openthomas-mcp
```

**Claude Desktop** (`claude_desktop_config.json`)

```json
{ "mcpServers": { "openthomas": { "command": "openthomas-mcp" } } }
```

## Tools

| Tool | What it does | Latency |
|---|---|---|
| `scan_markets` | Live Polymarket + Kalshi markets through the edge filters, plus cross-platform arb candidates | ~10-30s |
| `get_market` | One market's prices, resolution rules, metadata | ~1s |
| `research_news` | Recent headlines for a question (GDELT + Google News, keyless) | ~2-5s |
| `forecast_market` | OpenThomas's own pipeline: news + LLM ensemble + calibration | minutes on local models |
| `propose_trade` | **The core contract**: your probability + reason → market-prior blend → fractional-Kelly sizing under hard caps → paper fill at real ask, or a rejection naming the binding constraint | ~2s |
| `portfolio_status` | Cash, positions, drawdown vs kill-switch | ~1s |
| `performance_report` | PnL, win rate, Brier score, calibration table, per-category stats | ~1s |

## The propose_trade contract

```
propose_trade(platform="polymarket", market_id="0x…",
              probability=0.47, confidence=0.8,
              reason="Market hasn't priced today's injury report")
→ {"filled": {"side": "yes", "qty": 145, "price": 0.171, ...},
   "blended_probability": 0.3205,
   "risk_note": "edge +0.142, kelly 0.173, 145 contracts"}
```

- Your probability is **blended with the market price** (default 50/50 —
  evidence says pure model views lose to the market; blends win). If the blend
  kills the edge, there was no edge.
- Every proposal passes the same deterministic risk engine as the internal
  loop: per-market/event/category caps, solvency including fees, longshot-zone
  filter, drawdown kill-switch.
- A rejection tells you which constraint bound. **Do not retry with inflated
  numbers** — the journal records every proposal, and your calibration is
  being tracked.

## Security posture

- Fills are **paper-mode only** (simulated at real bid/ask). The MCP surface
  has no path to live order endpoints, deposits, or withdrawals, regardless of
  the config file's mode.
- The server binds to stdio — nothing listens on the network.
- The journal is shared (SQLite WAL) with the trading loop, so your agent's
  proposals and the autonomous loop's trades appear in one track record.
