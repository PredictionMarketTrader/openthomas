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

**The trigger is data, not the clock.** GenCast initialises from ERA5, which CDS
publishes once a day; until a new reanalysis day exists there is nothing to
compute. And when one does exist, the only thing between us and a forecast is a
free card on a box we share with training jobs. So the trading box — which owns
no GPU worth the name — reaches across to the compute box over ssh every 15
minutes, and `nwp_batch.sh` decides what that means:

```
   new ERA5 day?  ──no──▶  idle   (the common case: one date comparison,
         │yes                      no network, no nvidia-smi, no noise)
         ▼
   free card?     ──no──▶  retry in 15 min   (never evict another tenant;
         │yes                                 a busy box only costs us time)
         ▼
   GenCast on the LATEST ERA5  +  GraphCast on the latest open-data cycle
         │
         ▼  record the ERA5 day, return to idle until the next one
```

```
trading box  ──ssh, every 15 min──▶  compute box (8×A800)
  cron: run_remote.sh <host> --batch             nwp_batch.sh
                                                   ├─ GenCast   (ERA5/CDS, ~9 GB)
                                                   └─ GraphCast (open-data, ~34 GB)
  ◀── gencast-spread.json · rows.jsonl · tempgrid · tempseries
```

Three properties this buys, each of which a fixed daily slot gets wrong:

- **The ERA5 day is resolved when the card is won, not when the hunt began.** If
  the box stays busy for two days we don't then compute a two-day-old forecast —
  we compute the current one, late.
- **Nothing is recomputed.** A successful run records its ERA5 day and the script
  goes idle; without that guard, a 15-minute probe would spend hours of A800
  reproducing byte-identical output from an unchanged reanalysis file.
- **Failures back off instead of spinning.** A failed attempt (CDS outage, ERA5
  not published yet at our assumed lag) waits an hour rather than re-queuing a
  request every quarter hour, and writes no stamp — so the hunt resumes by itself.

**GraphCast rides along.** Its own input refreshes every 6 h, so coupling it to
ERA5 costs some freshness; but holding a free card and *not* spending the ~10
minutes to refresh the site's temperature field costs more. It is re-probed
separately at 36 GB, so a card with room for the ensemble but not for GraphCast
still delivers the spread the new reanalysis day exists to give us.

**Nothing about the run is pinned.** GraphCast takes the latest open-data cycle
implicitly; GenCast is handed today minus `OPENTHOMAS_ERA5_LAG_DAYS` (6). The card
is chosen the same way — at run time, by UUID (`gpu.sh`) — because a UUID baked
into config sends inference at whatever tenant owns that index today.

A probe that finds a busy box exits 0. It should read as quiet, not broken.

Both models run **serially** and under an `flock`, so a slow run (CDS queues ERA5
requests for many minutes) can never be lapped by the next probe. They share
`~/.cache/ai-models/constants-0p25.grib2.tmp`, and racing them deletes each
other's temp file mid-download.

Downstream, `GencastSpread` treats a run older than `FRESHNESS_HOURS = 30` as
stale and falls back to a 1.0 sigma multiplier. So a hunt that goes unfed for more
than ~30 h degrades the desk to climatological sigma rather than letting it trust
stale spread — safe, but blind, and worth noticing in the log.

Data hygiene note: `ai-models`'s open-data client is pinned to the AWS mirror (the
ECMWF portal rate-limits) and to the `oper` stream for every cycle (06/18z are
served as oper now; GenCast/GraphCast need the t−6h step, which always lands on
06/18z). `setup_graphcast_env.sh` applies both patches reproducibly.

## Pipeline

```
setup_graphcast_env.sh   # build the pinned venv + weights + patches (once)
setup_gencast_env.sh     # same for GenCast (one venv can serve both plugins)
gpu.sh                   # pick a free card by UUID, or report none
nwp_batch.sh             # the state machine above; runs ON the compute box
run_graphcast.sh         # one inference → GRIB (CPU default, --gpu for a big card)
run_gencast.sh           # one ensemble → GRIB (GPU only; date defaults to ERA5 lag)
extract_stations.py      # GRIB → station high/low rows + tempgrid/tempseries JSON
extract_gencast.py       # ensemble GRIB → per-station spread JSON
run_remote.sh <host>     # drive a compute box over ssh, pull artifacts back
```

On the trading box, one cron line runs the whole thing. The 15-minute interval is
the *probe* rate, not the compute rate — see the state machine above:

```cron
*/15 * * * * cd ~/projects/openthomas && scripts/nwp/run_remote.sh "" --batch >> ~/.openthomas/nwp-cron.log 2>&1
```

The empty first argument means "take the host from `~/.openthomas/nwp.env`", which
is also where the link's characteristics live (`OPENTHOMAS_NWP_PROXY=0` for a
compute box with its own clean route to ECMWF/CDS — borrowing the trading box's
exit over a ~1 MB/s link would be far slower than letting it fetch its own initial
conditions).

To see what the state machine thinks without running anything:

```
ssh <compute-box> 'bash ~/openthomas/scripts/nwp/nwp_batch.sh --status'
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
