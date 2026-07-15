# Our own weather models: GraphCast + GenCast

OpenThomas doesn't only *consume* forecasts — it runs its own AI numerical weather
prediction, so the desk's view of a settlement station comes partly from models we
control, retrain, and open-source. Two models, two jobs:

| model | job | output |
|---|---|---|
| **GraphCast** (operational, 0.25° / 13-level) | **point** forecast — the single best-guess trajectory | station daily high/low + a global 2 m temperature field for the globe |
| **GenCast** (diffusion ensemble) | **probabilistic** forecast — the distribution | per-station temperature spread → **P(high > strike)** for pricing |

The split matters because **prediction markets price probabilities, not point
estimates.** GraphCast gives the calibrated center; GenCast gives the odds a strike
resolves yes. The risk engine sizes against GenCast's probability; GraphCast is the
deterministic anchor and the field the site draws.

## Why these two

Both are open-weight DeepMind models that beat the operational IFS on most
headline scores. GraphCast is one autoregressive pass per step; GenCast is a
score-based diffusion model that samples an *ensemble* of trajectories — exactly
the uncertainty a trader needs.

**Inputs differ, and it matters.** GraphCast operational initialises from **free
ECMWF open-data** (13 pressure levels, ~7 h stale) — low latency, ideal for live.
GenCast additionally needs **sst** (sea-surface temperature), which open-data
omits, so it initialises from **ERA5 via CDS** (which carries sst and all 37
levels). ERA5 is a reanalysis ~5 days behind real time, so GenCast today is the
**hindcast / backtest / skill-comparison** engine; a live real-time GenCast would
inject a free sst field (e.g. NOAA OISST) into the open-data path instead.

## Measured resource profile (on an A800 80 GB)

| model | invoke as | VRAM | notes |
|---|---|---|---|
| **GraphCast operational** 0.25°/13 | `graphcast` | **26 GB** GPU · RAM on CPU | bf16 + grad-checkpoint; **~55 min/7-day on CPU** (80 cores, >57 GB RAM), minutes on a ≥32 GB GPU. **Live on the site.** |
| **GraphCast full** 0.25°/37 | `graphcast` + `GC_MODEL=full` | **~60 GB** GPU | rollout ~1m16s; needs ERA5 (37 levels) so `--input cds`. Fits 80 GB, not a 48 GB card. |
| **GenCast** 1.0° | `gencast-1.0` | **~9 GB** GPU | the practical ensemble: 5 members ~12 min, fits any ≥16 GB card. |
| GenCast 0.25° | `gencast-0.25-Oper` | **>80 GB / member** | TPU / model-sharding territory — does *not* fit a single 80 GB GPU even for one member. Avoid on our hardware. |

Two gotchas the scripts encode: (1) GenCast variants are selected by the **model
name** (`gencast-1.0`, `gencast-0.25-Oper`, …), *not* `--model-version` (which
ai-models ignores). (2) GenCast is **GPU-only** — the sampler does
`num_noise_levels = 20` forwards per step × the ensemble, so CPU is hours.

## Cadence & placement

- **GraphCast** — run **2×/day** (00z + 12z open-data). CPU box or a ≥32 GB GPU.
- **GenCast** — run **1×/day** (00z), small ensemble (8–16 members), on a large
  GPU.

Data hygiene note: `ai-models`'s open-data client is pinned to the AWS mirror (the
ECMWF portal rate-limits) and to the `oper` stream for every cycle (06/18z are
served as oper now; GenCast/GraphCast need the t−6h step, which always lands on
06/18z). `setup_graphcast_env.sh` applies both patches reproducibly.

## Pipeline

```
setup_graphcast_env.sh   # build the pinned venv + weights + patches (once)
run_graphcast.sh         # one inference → GRIB (CPU default, --gpu for a big card)
extract_stations.py      # GRIB → station high/low rows + tempgrid/tempseries JSON
run_remote.sh <host>     # orchestrate on a compute box, pull artifacts back
```

The station rows join the multi-model consensus through `LocalModelSource` (scored
per station like GFS or ECMWF — a model earns its weight by its bias/σ record). The
temperature field feeds the globe (`graphcast-tempgrid.json`), and the daily series
drives the time axis (`graphcast-tempseries.json`).

## Roadmap

- **GenCast integration** — wire the ensemble's per-station distribution into the
  forecaster as a probability, and add a probabilistic lens to the site.
- **Fine-tuning** — both models are the base; we retrain them on our own settled
  station history (the same leak-free hindcast that learns per-station bias) and
  **open-source the improved weights at [huggingface.co/openthomas](https://huggingface.co/openthomas)**.
  Strategy, harness, risk engine, and now the weather models: build in public.
