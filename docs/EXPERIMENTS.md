# Experiments: what the paper claims, and the runs that decide it

The paper (`paper/`) is written build-in-public: its empirical claims are
**pre-registered here**, the numbers are filled in from the pipeline below as
the data accrues, and the conclusions follow the numbers — including the
ones that go against the thesis. Every table in the paper names the frozen
dataset (file + digest) it was scored on.

## The pipeline

`scripts/paper_data.sh` runs daily on the trading box:

1. `openthomas hindcast --days 92` — grows `weather-verification.jsonl`
   (as-of NWP guidance at leads 0–5 from the Open-Meteo previous-runs API,
   official settlements from ACIS) and `mos-{nbm,gfsmos}.jsonl` (NBM and GFS
   MOS daily max/min from the IEM MOS archive, `d−1` 12Z run). The
   previous-runs archive only reaches 92 days back, so the store is the only
   place the record grows; a day missed is a day lost.
2. `openthomas replay --start S --end E --rows-out …` — freezes decision-time
   rows for an explicit window: baseline probability at the snapshot, the
   Kalshi bid/ask at that snapshot (hourly candles), the outcome. One file per
   guidance source (consensus / nbm / gfsmos). Snapshots are *local* hours
   (lows: local midnight, the first minute of the NWS CLI day; highs: 11:00
   local), so nothing of the extreme being bet on can be on the books.
3. `openthomas ablate --rows-in … --out …` — every offline experiment on that
   one file, JSON + Markdown.
4. `openthomas improve --history --export` — the self-improvement lineage.
5. `openthomas push-forecasts --replay-dir …` — everything above to
   [huggingface.co/openthomas](https://huggingface.co/openthomas) (dataset
   `weather-forecasts`), so each commit is a timestamped record of what was
   known when.

Window: start **2026-07-09** (first day with archived lead-1 guidance for
every station), end = yesterday, rolling. Station bias/σ are learned only
from days before the start.

## Pre-registered experiments and decision rules

| # | Experiment | Command | Decides |
|---|---|---|---|
| E1 | Ablation: naked model → + station bias → + market blend → **control: no blend, edge bar raised until it trades as rarely as the blend** | `ablate` | If the control's PnL/ct is positive with a 95% CI excluding zero, *selectivity* is the edge. If only the blended row is, the edge is the **blend** (a better probability), and the paper's thesis sentence changes to say so. |
| E2 | Grid: `min_edge` × `market_prior_weight` | `ablate` | The E1 conclusion must hold across neighbouring cells, not one. |
| E3 | Snapshot sensitivity: lows at local 00/01/02, highs at 09/11 | `replay --low-hour/--high-hour` | If PnL/ct moves by more than its CI half-width, timing is doing work and the leak-free claim is weaker than stated. |
| E4 | LLM on/off and clamp sweep δ ∈ {0, .05, .10, .15, .25, ∞}, PnL and Brier | `ablate --llm-deltas` | Whether the bounded LLM adjustment adds anything over the statistical baseline; which δ. Brier must not worsen. |
| E5 | Baselines: the same decision rule priced from NBM and GFS MOS (each with its own learned bias) | `replay --source`, `ablate --sources` | Whether the seven-model consensus + learned bias beats what the market already reads. If NBM ties it, "learnable local edge" is dropped. |
| E6 | Breakdown by station, ISO week, and high/low | `ablate` | Whether the edge is broad or one station in one week. |
| E7 | Sizing regimes on the production trades: flat, Kelly, Kelly+caps, Kelly+caps+kill-switch; equity curve, max drawdown | `ablate` (risk/simulate.py) | What each rail costs or saves — the only numbers the "harness" layer gets. |
| E8 | Self-improvement lineage: promotions, rollbacks, Brier vetoes over ≥ 4 weeks of meta-cycles; the gate's anti-gaming rules as test cases (`tests/test_experiments.py`) | `improve --history --export` | Whether the RSI section keeps an empirical claim or becomes future work. |
| E9 | Station bias on the first vs second half of the hindcast window | `hindcast --split` | Whether "biases persist out-of-sample" is true (same sign, similar magnitude). |

Error bars everywhere: PnL per contract with a **block bootstrap by
settlement day** (markets on one day share a thermometer and a synoptic
pattern), daily-PnL t-statistic, and max drawdown of the daily path.

## Reproducing a table

```bash
openthomas hindcast --days 92
openthomas replay --start 2026-07-09 --end 2026-09-10 --rows-out rows.jsonl   # prints the digest
openthomas ablate --rows-in rows.jsonl --out results.json
```

Frozen row files and results are also on the Hugging Face dataset under
`replay/`; `ablate --rows-in` on a downloaded file reproduces its `.md`
byte-for-byte (the bootstrap is seeded).
