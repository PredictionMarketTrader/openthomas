# Live trading setup

**Read this first.** Live mode requires two deliberate steps, so a typo can't
put real money at risk:

1. `mode: live` in `~/.openthomas/config.yaml`
2. the `--live` flag on `openthomas run`

Everything the risk engine enforces in paper mode (Kelly caps, per-event
limits, drawdown kill-switch, solvency checks) applies identically in live
mode. The agent cannot deposit, withdraw, or move funds on any venue.

## Kalshi (US, CFTC-regulated) — supported

1. Create an account at [kalshi.com](https://kalshi.com) (or
   [demo.kalshi.co](https://demo.kalshi.co) for the paper-money exchange).
2. Generate an API key at account → profile → API keys. Save the private key —
   it is shown once.
3. Configure:

```bash
pip install 'openthomas[kalshi]'
export KALSHI_API_KEY_ID=...
export KALSHI_PRIVATE_KEY_PATH=~/.openthomas/kalshi.pem
# optional: trade the demo exchange with mock funds first
export KALSHI_DEMO=1
```

Requests are signed with RSA-PSS/SHA-256 over `timestamp + method + path`,
per Kalshi's API docs. OpenThomas places **limit orders only** (Kalshi removed
naked market orders in Feb 2026). Fees: taker `ceil(0.07 × qty × p × (1−p))`;
maker orders are cheaper — preferring resting orders is on the roadmap.

Note: Kalshi's API is evolving fast (fixed-point field migration, token-bucket
rate limits, scoped keys). If order placement fails after an API change, open
an issue with the response body.

## Polymarket (global) — data supported, live execution on the roadmap

Paper trading against live Polymarket prices works out of the box. Live order
placement is being rebuilt on the official unified SDK because the ecosystem
shifted under everyone in 2026:

- The long-standing `py-clob-client` was **archived in May 2026** and no longer
  functions (collateral migrated from USDC.e to **pUSD**).
- The replacement is the official pre-release SDK: `pip install --pre polymarket-client`.
- Orders are EIP-712-signed; you need a funded Polygon wallet, and matched
  trades are gasless.
- **US users**: the offshore CLOB is close-only ([geoblock](https://docs.polymarket.com/api-reference/geoblock)).
  Polymarket US (the CFTC-regulated venue acquired via QCEX) is a separate API
  at [docs.polymarket.us](https://docs.polymarket.us) — treated as a distinct
  venue on our roadmap.

## Operational safety checklist

- Start with money you can afford to lose entirely; the bankroll cap is the
  agent's whole world, make it small first.
- Watch a week of paper trading and read `openthomas report` before going live.
- Keep the drawdown kill-switch conservative; it halts trading and requires a
  human to resume.
- Run on a machine you control; API keys and the journal never leave it.
- Check your venue's terms and your local laws — automated trading is allowed
  on both venues, but eligibility varies by jurisdiction (and by US state for
  Kalshi sports markets / Polymarket US).
