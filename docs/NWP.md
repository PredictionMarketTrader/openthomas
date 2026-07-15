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
headline scores, and both initialise from **free ECMWF open-data** (13 pressure
levels, ~7 h stale) — no paid CDS/ERA5 feed. GraphCast is one autoregressive pass
per step; GenCast is a score-based diffusion model that samples an *ensemble* of
trajectories, which is exactly the uncertainty a trader needs.

## Measured resource profile

- **GraphCast operational (0.25°)** — **~26 GB VRAM** on GPU (bf16 + gradient
  checkpointing, can't shrink further); **~55 min for a 7-day run on CPU** (80
  cores). It fits a ≥32 GB card (runs in minutes) but *not* a 24 GB card — on a
  24 GB box, run CPU.
- **GenCast** — the sampler does `num_noise_levels = 20` model forwards per step,
  times the ensemble size, so it is **GPU-only** (a diffusion ensemble on CPU is
  many hours). The **1.0° Mini** variant fits a single modest GPU; the 0.25°
  Operational variant wants a large (80 GB-class) GPU.

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
