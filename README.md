# OpenThomas — AI trading agent for prediction markets

**An autonomous AI agent that trades Polymarket and Kalshi for you: it scans thousands of markets, forecasts with an LLM ensemble, sizes positions with fractional Kelly, and enforces risk limits the model can't override. You set the bankroll, the risk profile, and the goal. It does the rest — and learns from every settled trade.**

```bash
pip install openthomas
openthomas init --bankroll 1000 --risk conservative
openthomas run        # paper trading on live market prices — the default
```

No wallet, no exchange API keys needed to start: paper mode simulates fills against real Polymarket + Kalshi order books. You only connect real money after you've watched it trade.

> ⚠️ **Prediction market trading can lose all the money you allocate.** OpenThomas ships with paper trading as the default, hard position limits, and a drawdown kill-switch — but no software makes trading safe. Roughly 70% of Polymarket addresses have lost money. This is not financial advice.

---

## Why another trading bot? Because the LLM is not the edge.

The [Prediction Arena benchmark](https://arxiv.org/abs/2604.07355) gave six frontier AI models $10,000 each and let them trade real prediction markets autonomously for 57 days. **Every single one lost money** — between −16% and −30.8%. Research volume didn't matter. Token spend didn't matter. What separated the least-bad from the worst:

1. **Initial prediction accuracy** — being right, early
2. **Sizing up when correct, down when uncertain**
3. **Exit discipline** — early exits systematically underperformed holding to settlement
4. **Not trading at all** when there's no edge

The only profitable run on record (+10.9%) was selective (112 trades), asymmetric (avg win $63.89 vs avg loss $3.23), and low-drawdown (4.1%). That profile is a *harness* property, not a model property. OpenThomas is that harness:

| Layer | What it does | Who's in control |
|---|---|---|
| **Edge scanner** | Filters thousands of markets down to plausible mispricings; flags cross-platform arbitrage (Polymarket vs Kalshi price gaps) | deterministic code |
| **Forecast engine** | LLM ensemble (median of N independent estimates), grounded in resolution rules and base rates | your choice of model |
| **Calibration layer** | Platt-scales raw forecasts against *your own* settled-trade history; blends with the market price ([pure LLM forecasts lose to market consensus; a blend beats it](https://arxiv.org/abs/2511.07678)) | deterministic code |
| **Risk engine** | Fractional Kelly sizing, per-market / per-event / per-category caps, fee-aware EV threshold, longshot-zone filter, drawdown kill-switch | deterministic code — **the model proposes, the risk engine disposes** |
| **Memory** | Every forecast and fill journaled to SQLite; post-settlement reflection distills lessons that feed back into future prompts | agent, human-auditable |

## What it looks like

```
$ openthomas run --once
────────────── cycle · account $1,004.12 · cash $861.40 ──────────────
markets 300 → candidates 58 → forecasts 12 → trades 2
  TRADE BUY 41 NO @ 0.34 [polymarket] Will the Fed cut rates in September?
  TRADE BUY 28 YES @ 0.61 [kalshi] Will CPI YoY be above 2.8% in July?
  ARB? +0.045 gap | polymarket:Fed cuts in September vs kalshi:Fed decision Sept
  skip: Will Spain win the World Cup?: edge +0.021 below threshold 0.080
  skip: Putin out by Dec 31?: confidence 0.40 too low
```

```
$ openthomas report
Account value: $1,041.87  (start $1,000.00, +4.2%)
Settled: 31 · win rate 61% · avg win $4.10 / avg loss $1.35
Brier score: 0.191 (0.25 = coin flip, lower is better)
```

`openthomas vital` renders a shareable performance card (equity curve, win rate, Brier score, max drawdown) — post your track record, good or bad.

## Quickstart

```bash
pip install openthomas

# 1. Configure: bankroll, risk appetite, goal, forecasting model
openthomas init --bankroll 1000 --risk conservative \
  --goal "Grow steadily; protecting capital beats chasing returns"
export ANTHROPIC_API_KEY=sk-ant-...   # or any OpenAI-compatible endpoint

# 2. See what it sees
openthomas scan

# 3. Let it trade (paper mode: real prices, simulated fills)
openthomas run

# 4. Check in
openthomas report
openthomas vital
```

**Local / self-hosted models** (no API bill, full privacy):

```bash
openthomas init --provider openai --base-url http://localhost:11434/v1 --model gemma3:12b
```

Any OpenAI-compatible server works — Ollama, vLLM, llama.cpp. If you have GPUs, [docs/TRAINING.md](docs/TRAINING.md) covers fine-tuning a local model on your own trade journal (calibration LoRA) so the agent's forecaster improves on *your* markets.

## Risk profiles

You hand OpenThomas a mandate, not a suggestion — these are enforced in deterministic code, outside the LLM:

| | conservative | moderate | aggressive |
|---|---|---|---|
| Kelly fraction | 0.15× | 0.25× | 0.33× |
| Max per market | 5% | 8% | 12% |
| Min edge after fees | 8¢ | 6¢ | 5¢ |
| Drawdown kill-switch | 15% | 20% | 30% |
| Max trades/cycle | 3 | 5 | 8 |

Plus, in every profile: per-event correlation caps (correlated settlements caused the largest single-session losses in Prediction Arena), longshot-zone filter (contracts under 10¢ lose >60% of stake on average — [Whelan et al. 2026](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5502658)), solvency checks including fees, and mark-to-market at bid prices (liquidation value, not hope).

## How it learns

- **Journal** — every forecast, fill, and settlement in SQLite (`~/.openthomas/journal.db`). Your data, on your machine.
- **Calibration** — once ≥30 markets settle, OpenThomas fits Platt scaling per category to correct your model's systematic bias (e.g. "runs 10 points hot on weather").
- **Lessons** — after settlements, a reflection pass writes short falsifiable rules to `~/.openthomas/lessons/`, which are injected into future forecast prompts.
- **Skills** — strategies and domain playbooks are markdown files (`skills/`), the same pattern as OpenClaw / Hermes / Claude skills. Add your own, or let the agent draft one from its post-mortems for you to approve.

## FAQ

**Can an AI bot actually make money on Polymarket or Kalshi?**
Unproven for pure LLM forecasting — every frontier model tested in live benchmarks lost money, and no rigorous public demonstration of sustained fee-adjusted LLM alpha exists. The documented, durable edges are structural: longshot-bias fading, cross-platform arbitrage, market-making spread capture, and researched divergence trades in illiquid long-tail markets. OpenThomas is built to exploit those with LLM research as an input — and to *decline to trade* the rest. Paper-trade first. Expect losses.

**Do I need API keys to try it?**
No exchange keys. Paper mode uses public market data from both venues. You need an LLM (API key or a local model via Ollama) for forecasting.

**Which markets does it trade?**
Binary markets on Polymarket (global, crypto-settled in pUSD) and Kalshi (US, CFTC-regulated). Kalshi's demo exchange is supported (`KALSHI_DEMO=1`). Live Polymarket trading from the US is geoblocked to close-only on the offshore CLOB; Polymarket US is a separate venue (integration planned).

**Can it withdraw or move my funds?**
No. The agent only trades within the bankroll you allocate. It has no withdrawal, transfer, or deposit capability — and live mode requires two independent explicit switches.

**What models work best?**
Any strong reasoning model via Anthropic or OpenAI-compatible APIs. Local Gemma-class models (12B+) work for the privacy-conscious; expect weaker forecasts until you fine-tune on your journal (see [docs/TRAINING.md](docs/TRAINING.md)).

## Documentation

- [Architecture & design rationale](docs/DESIGN.md)
- [The edge playbook — documented inefficiencies with sources](docs/EDGE.md)
- [Trading against agents — the adversarial playbook](docs/ADVERSARIAL.md)
- [Live trading setup (Kalshi, Polymarket)](docs/LIVE_TRADING.md)
- [Training a local forecaster on your journal](docs/TRAINING.md)

## Roadmap

- [x] Paper trading loop on live Polymarket + Kalshi data
- [x] LLM ensemble forecasting, calibration, market-prior blending
- [x] Deterministic risk engine (Kelly, caps, kill-switch)
- [x] Cross-platform arbitrage scanner
- [x] Journal, lessons, shareable vital card
- [ ] Live Kalshi execution hardening (order amend/cancel, WebSocket fills)
- [ ] Polymarket live execution via official SDK (pUSD)
- [ ] NegRisk / multi-outcome coherence arbitrage
- [ ] Market-making strategy (maker rebates + liquidity incentive programs)
- [x] News retrieval pipeline for forecasts (GDELT + Google News, keyless)
- [ ] Web dashboard, Telegram/Discord notifications
- [ ] Journal → LoRA fine-tuning recipes for local models
- [ ] Public community leaderboard of (opt-in) vitals

## Contributing

The interesting problems are open: retrieval quality, resolution-rule matching for cross-venue arbitrage, calibration under small samples, market-making without adverse selection. PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). If you run OpenThomas, share your `vital` card in [Discussions](https://github.com/autotradingagent/openthomas/discussions) — including the losses; honest track records are how this gets better.

## License

MIT. Trade at your own risk; comply with your local laws and each venue's terms.
