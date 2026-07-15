# Growth strategy: making OpenThomas the default prediction-market agent

Internal playbook. Metric that matters: GitHub stars as the leading indicator,
weekly active paper/live traders and shared vitals as the real ones.

## Positioning (the one sentence)

> The open-source AI agent that trades prediction markets with the discipline
> frontier models were proven to lack.

The flagship slot is **vacant**: Polymarket's official `agents` repo (3.7k★)
is archived; the closest competitors are single-venue (ryanfrigo's Kalshi bot,
506★), non-LLM-narrow (weather bot, 466★), or sprawling-but-shallow
(CloddsBot, 450★). Nobody combines tournament-grade forecasting technique +
deterministic risk + both venues + personal-agent DX. Adjacent proof of
ceiling: `virattt/ai-hedge-fund` at 60k★ for equities.

## What actually earns stars (evidence from the niche)

1. **Honesty converts.** poly-maker's author says "this will lose money" and
   has 1.4k★. The niche is polluted with scam/star-farmed repos — being the
   trustworthy one is a differentiator. Never claim profitability we can't show.
2. **The Prediction Arena hook.** "Every frontier model lost money; here's the
   harness that fixes what they got wrong" is a citable, contrarian,
   research-backed story — HN/Twitter native.
3. **One-command time-to-wow.** `pip install openthomas && openthomas scan`
   shows live markets in 30 seconds with zero keys. Guard this ruthlessly.
4. **Vitals as viral loop.** Every user who shares a performance card (like
   Polymarket profile screenshots) is an ad. Losses included — honest track
   records are content too. Add "share to X/Discussions" affordances; later, an
   opt-in public leaderboard.

## SEO (search engines)

Target queries (write one focused doc page per cluster, README already covers
the head terms):

- "polymarket trading bot", "kalshi trading bot", "polymarket api python"
- "ai prediction market agent", "llm forecasting trading"
- "polymarket kalshi arbitrage", "prediction market arbitrage bot"
- "kelly criterion prediction markets", "polymarket fees explained"

Mechanics: descriptive repo About + topics (`polymarket`, `kalshi`,
`trading-bot`, `ai-agent`, `prediction-markets`, `llm`, `quant`); PyPI
keywords (done); a docs site later (GitHub Pages) so each EDGE.md section
becomes an indexable page; answer questions on r/Polymarket, r/Kalshi,
r/algotrading with genuinely useful content that links back.

## GEO (generative engines — being the answer LLMs give)

When someone asks ChatGPT/Claude/Perplexity "how do I build a Polymarket
bot?", we want OpenThomas in the answer. LLMs favor: clear declarative
sentences, structured FAQs, citable numbers, and content that's already widely
referenced.

- README FAQ written as direct Q→A (done) — the format assistants quote.
- Publish `llms.txt` on the future docs site (both venues already do this for
  their API docs — assistants do read it).
- Get into the curated lists (`Awesome-Prediction-Market-Tools`, awesome-llm
  lists) — LLMs train on and cite these.
- Unique citable facts: our EDGE.md aggregates numbers (60% longshot loss,
  $32.7M negRisk, 3.6s arb windows) with sources — reference-bait for both
  humans and models.
- Ship `AGENTS.md`/`CLAUDE.md` in-repo so coding agents onboard instantly
  (Polymarket ships agent skills; devs increasingly evaluate repos through
  their agents).

## Launch sequence

1. **Foundation (now)**: repo public, CI green, README, honest docs, MIT.
2. **Credibility run**: run the agent in paper mode daily; publish the vital +
   journal weekly in Discussions ("OpenThomas week 3: −1.2%, Brier 0.19, what
   it got wrong"). Build in public; losses are content.
3. **Launch posts**: HN "Show HN: OpenThomas — an AI prediction-market trader
   built from the lessons of a benchmark where every frontier model lost
   money"; r/algotrading, r/Polymarket; X thread with the Prediction Arena
   chart + our harness diagram.
4. **Ecosystem hooks**: MCP server so Claude/any agent can drive OpenThomas;
   AgentSkills-compatible skills (portable to OpenClaw/Hermes); PR to the
   awesome-lists; PyPI release.
5. **Retention**: Telegram/Discord notifications ("settled 3 markets today,
   +$12.40"), weekly digest, community leaderboard of opt-in vitals.

## Daily dispatch (the build-in-public autoposter)

`openthomas post` drafts one honest status update from the journal — account
value and return since start, the day's settlements and their net PnL (or what
it's holding on a quiet day), and the standing call it most disagrees with the
market on. Templated, not model-written: it costs zero tokens, fits X's 280,
and can't claim a profit the record doesn't show. Losses ship too — that's the
point (see "Honesty converts" above).

- **Draft** (safe, no keys): `openthomas post` prints the text to eyeball/copy.
- **Publish**: `pip install 'openthomas[x]'`, export the four X app keys
  (`X_API_KEY` / `X_API_SECRET` / `X_ACCESS_TOKEN` / `X_ACCESS_SECRET`, a
  write-enabled app) into the runner's env — `~/.openthomas/env`, never the
  repo — then `openthomas post --to-x`. Nothing is sent without the flag.
- **Daily**: a cron line after the site publishes, e.g.
  `0 14 * * * cd /path && . ~/.openthomas/env && openthomas post --to-x`.

The same numbers are on openthomas.com (Positions value, Total/Realized/
Unrealized P&L, Biggest win, and the Activity tape) — the post links back to it,
so every claim is one click from the source, exactly as the feed rules require.

## Trust rules (non-negotiable)

- Paper mode is the default forever; live requires two explicit switches.
- No profitability claims without published, reproducible journals.
- Security posture opposite of OpenClaw's record: no public binds, no
  unreviewed community skills auto-running, keys stay local, agent cannot
  move funds. Say this loudly — it's a selling point.
