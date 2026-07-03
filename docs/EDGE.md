# The edge playbook

What actually makes money in prediction markets, with sources. OpenThomas's
scanner and strategies are built around these; anything not on this list is
assumed to be noise until proven otherwise.

## 1. Favorite-longshot bias (fade the moonshots)

The best-quantified inefficiency. Across 46,282 Kalshi contracts (2021–2025),
contracts priced ≤10¢ **lost over 60% of stake** on average after fees, while
contracts above 70¢ earned significantly positive post-fee returns
([Bürgi, Deng & Whelan 2026](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5502658)).
Polymarket shows the same bias.

- OpenThomas: the risk engine's `min_price`/`max_price` band keeps the agent
  from *buying* longshots. Systematically *shorting* them (buying NO on
  clustered sub-10¢ markets) is a strategy with real tail risk — one
  resolution-rules surprise can erase months of premium (see §5).

## 2. Cross-platform arbitrage (Polymarket × Kalshi)

The same real-world event often trades at different prices on both venues.
Practitioner writeups found ~50 genuine arbs at 5–21% gross across ~90k market
pairs. **The killer risk is resolution-criteria mismatch** — a documented "14%
arb" where Kalshi paid on a candidate advancing from a primary while Polymarket
paid on most votes; both legs can lose.

- OpenThomas: `EdgeScanner.find_cross_platform_arbs` surfaces candidate pairs;
  the agent must verify both markets' resolution rules with the LLM before
  acting. Fees on both legs and capital lockup to resolution are priced in.

## 3. Multi-outcome coherence / negRisk rebalancing

~$40M was extracted from Polymarket in a single year, of which **$32.7M came
from negRisk rebalancing** — multi-outcome events whose outcome prices sum
away from $1 ([arXiv:2508.03474](https://arxiv.org/html/2508.03474v1)).
Single-condition YES+NO≠$1 added $10.6M.

- OpenThomas: roadmap. Requires CTF split/merge/convert operations on-chain.

## 4. Market making (maker > taker, structurally)

The Whelan paper's cleanest result: **makers systematically outperform
takers**. Both venues pay you to make: Polymarket has maker rebates (makers
always pay zero fees), Kalshi runs a liquidity incentive program that pays for
resting orders. The hazard is adverse selection — naive reward calculations
overstate income 3–5× because fills cluster on informed flow.

- OpenThomas: roadmap. Prefer limit orders over crossing the spread everywhere
  (already the default in execution).

## 5. Resolution-rules edge — and oracle tail risk

Reading the rules better than the crowd is a repeatable manual edge (the #1
Polymarket P&L trader works exactly this niche). The same force in reverse is
the biggest tail risk on any position:

- UMA governance attack, March 2025 (whale swung a resolution; no refunds)
- Zelensky "suit" flip-flop, July 2025 (~$160M market)
- Kalshi's $54M Khamenei market resolving on a death carveout

- OpenThomas: forecasts are grounded in `rules_primary` / description text,
  not headlines, and the prompt requires naming what the crowd is missing.
  Never concentrate in positions that share one oracle decision (per-event caps).

## 6. LLM-researched divergence in the long tail

In liquid markets, prices are near-efficient (executable arbs live ~3.6s
median; latency games are a pro arms race you will lose). The defensible LLM
edge is Domer-style: obscure, illiquid markets where nobody has done the
research — an always-on agent scales what a human does manually. Evidence
constraint: pure LLM forecasts lose to market consensus; a market-heavy blend
wins ([Bridgewater AIA, arXiv:2511.07678](https://arxiv.org/pdf/2511.07678)).

- OpenThomas: `market_prior_weight` blends the forecast with the price (default
  0.5); the EV threshold then only fires on strong, specific divergence. News
  retrieval (the single biggest accuracy lever in tournament data) is the top
  roadmap item.

## Edge-eaters (why most bots lose)

- Polymarket taker fees (since ~March 2026): up to 1.75¢/contract at 50¢ —
  fatal to thin-edge strategies that cross the spread
- Kalshi taker fee: same formula, 7¢ ceiling per $1 at 50¢
- Bid-ask spreads of 2–5¢ — a round trip costs more than most "edges"
- Adverse selection on resting orders
- Capital lockup: money in a 96¢ position for 6 months underperforms T-bills
  (Kalshi pays ~4% APY on balances; Polymarket pUSD pays 0%)
- Overtrading: the strongest predictor of losses in Prediction Arena
