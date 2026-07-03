# Trading against agents: the adversarial playbook

Prediction markets are transitioning from mostly-human to heavily-agent flow.
The counterparty mix changes what works: exploiting human biases and exploiting
agent biases are different games, and OpenThomas plays in a pond where the
other fish increasingly run on the same base models it does. This document
covers both directions — how we avoid being the prey, and where agent-heavy
flow creates new edge.

## Threat model: how our agent gets exploited

| Attack | Mechanism | OpenThomas defense |
|---|---|---|
| **Stale-quote pickoff** | We compute EV on cycle-start prices; an LLM call takes seconds; a fast agent moves the book and our marketable order fills at a now-bad price | Live orders re-fetch the quote and re-validate the edge at execution time; limit price never chases upward (`agent/loop.py`) |
| **Cadence timing** | A fixed N-minute loop is a signature: an adversary quotes wide right before our cycle and tightens after | ±30% randomized jitter on the cycle interval |
| **Prompt injection via market text** | Market descriptions, rules, comments — and later, retrieved news — can embed text aimed at LLM forecasters ("this clearly resolves YES") or rules engineered to diverge from the headline (the honeypot market) | The forecast prompt treats all market text as untrusted data, is instructed to ignore embedded directives, and to cut confidence when rules and headline diverge. Confidence gates block the trade |
| **Wash-traded volume bait** | Fake volume attracts volume-ranked scanners (ours included) into thin, rigged books | Scanner requires real book depth (`liquidity`) and tight spreads, not just volume; paper broker caps fills at 2% of displayed depth — the same cap applies to live sizing |
| **Copy-trading and front-running** | Polymarket positions are public on-chain; a profitable wallet gets copied and front-run, degrading its own entries | No real defense at small scale — assume it. Mitigations: limit orders only, small size relative to depth, no predictable re-entry patterns. (Whales handle this by spoofing their copiers; we don't) |
| **Adverse selection while making** | Resting orders fill disproportionately against informed flow — the classic MM tax, worse when informed flow is fast agents | Market-making strategy (roadmap) ships only with inventory limits, quote-fading on flow imbalance, and reward-program economics priced net of adverse selection |
| **Oracle games** | UMA governance attacks and rules-lawyered resolutions transfer money from naive holders regardless of who was "right" | Per-event exposure caps; resolution-rules-first forecasting; longshot zone excluded |

## Offense: what agent-heavy flow makes newly profitable

1. **Fading the agent herd.** Agents sharing base models share biases — the
   Prediction Arena models herded into the same categories with the same
   systematic errors (e.g. weather overconfidence). When a headline drops,
   news-reactive agents pile in within minutes and overshoot; the edge moves
   from *reacting to news* (a speed game we lose by construction) to *pricing
   the overreaction* (a patience game). Signal to build: price jump on a spike
   in trade count with shallow depth behind it → quote the other side.
2. **LLM incoherence arbitrage.** LLMs give arbitrageably inconsistent
   probabilities across logically related questions (P(A∧B) > P(A), outcome
   sets summing ≠ 1 — arXiv:2412.18544). Human bookies police this; agent-made
   prices police it less than you'd think. Our multi-outcome coherence scanner
   (roadmap) is exactly this trade, and it's model-free once detected.
3. **Resolution-rules asymmetry.** Most agents forecast the headline; few
   parse the rules. Markets where rules and headline diverge are systematically
   mispriced by headline-reading agents. We forecast rules-first — the same
   property that defends us against honeypots finds us these trades.
4. **Second-order calibration.** Our journal records *market* prices at
   forecast time alongside outcomes. As agent flow grows, per-category
   calibration curves of the market itself (not just our model) reveal where
   the new crowd is miscalibrated — the longshot bias of the agent era won't
   be at the same price points as the human one. The data to detect the shift
   is already being collected.

## Design consequences (why this shapes the architecture)

- **Never play the speed game.** Every strategy must survive being the slowest
  actor in the market. EV thresholds, hold-to-settlement, and structural arbs
  all qualify; latency arbitrage never will, and PRs adding it will be
  declined — it's a losing arms race against colocated pros.
- **Determinism inside, unpredictability outside.** Risk limits are exactly
  reproducible; externally observable behavior (timing, sizing within caps)
  should carry jitter.
- **The LLM is an untrusted-input processor.** Everything it reads — market
  text today, retrieved news tomorrow — is attacker-controllable. Its output
  is a *proposal* filtered through confidence gates and the risk engine; a
  fully injected forecaster still can't exceed position caps, trade blocked
  categories, or bypass the kill-switch.
- **Assume you are being watched.** On-chain venues make our whole book
  public. Position sizes stay small relative to depth not just for execution
  quality but because visibility is leakage.
