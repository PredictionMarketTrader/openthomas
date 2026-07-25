#!/usr/bin/env bash
# One GraphCast (operational, 0.25° / 13-level) inference → OpenThomas's local
# model source. GraphCast is the deterministic POINT-estimate NWP (it replaced
# Pangu); GenCast — the diffusion ensemble that yields calibrated probabilities —
# is a separate, GPU-only pipeline (see run_gencast.sh, roadmap).
#
# Default: CPU (~55 min/run) — the open-data initial conditions are ~7 h stale
# anyway, so faster inference buys no real latency. GraphCast operational peaks
# ~34 GB on GPU: it fits a 48 GB A6000 / 80 GB A800, but NOT a 24 GB card
# (4090/3090). On a 24 GB box, use CPU.
#
# --gpu: infer on a big card (~8 min; measured peak ~34 GB, so a 48 GB A6000 or an
# 80 GB A800). By default it picks the emptiest card at run time and aborts if none
# has room — it never evicts another tenant. OPENTHOMAS_GPU_UUID pins a specific
# card instead, which is how nwp_batch.sh hands it one it has already chosen.
#
# This runs the operational (13-level) model from free open-data — the live path.
# For the higher-fidelity 37-level FULL model, set GC_MODEL=full and use ERA5:
#   GC_MODEL=full ai-models --input cds --date <YYYYMMDD> --assets <dir> \
#     --lead-time 168 --path graphcast_full.grib graphcast
# (~60 GB VRAM, needs ~/.cdsapirc; ERA5 is ~5 days stale — hindcast, not live.)
#
# Requires the patched GraphCast venv at $OPENTHOMAS_GC_HOME/venv-gc and the
# weights under $OPENTHOMAS_GC_HOME/{params,stats} — build both with
# setup_graphcast_env.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"  # resolve before any cd
GC_HOME="${OPENTHOMAS_GC_HOME:-$HOME/.openthomas/graphcast}"
VENV="$GC_HOME/venv-gc"

if [ "${1:-}" = "--gpu" ]; then
  # Allocate on demand instead of grabbing 75% up front — lets the 26 GB model
  # use the whole card, and keeps a shared card's headroom honest.
  export XLA_PYTHON_CLIENT_PREALLOCATE=false
  # Choose the card at run time. A pin baked into config sends inference at
  # whatever tenant owns that index today; an explicit UUID still wins, which is
  # how nwp_batch.sh hands over the card it already found free.
  if [ -n "${OPENTHOMAS_GPU_UUID:-}" ] && [ "${OPENTHOMAS_GPU_UUID}" != auto ]; then
    export CUDA_VISIBLE_DEVICES="$OPENTHOMAS_GPU_UUID"
    echo "[$(date -Is)] pinned inference to the configured GPU"
  else
    . "$SCRIPT_DIR/gpu.sh"
    NEED_GB="${OPENTHOMAS_GC_NEED_GB:-36}"
    uuid="$(nwp_free_gpu "$NEED_GB")"   # empty stdout, not a bad exit, means none
    [ -n "$uuid" ] || {
      echo "[$(date -Is)] no card with ${NEED_GB}GB free for GraphCast — aborting" >&2
      exit 1
    }
    export CUDA_VISIBLE_DEVICES="$uuid"
    echo "[$(date -Is)] picked a free GPU: $uuid"
  fi
else
  # Force the CPU backend even though jaxlib carries CUDA; belt-and-braces.
  export JAX_PLATFORMS=cpu
  export CUDA_VISIBLE_DEVICES=""
fi

WORK="$GC_HOME/runs/$(date -u +%Y%m%dT%H%M)"
mkdir -p "$WORK"
cd "$WORK"

echo "[$(date -Is)] graphcast run starting in $WORK (venv: $VENV, mode: ${1:-cpu})"
# The env's ecmwf-opendata client is patched to the AWS mirror (the ECMWF portal
# 429-throttles) and to request every cycle from the 'oper' stream (06/18z are
# served as oper now, not scda). Assets are pre-staged, so no --download-assets.
"$VENV/bin/ai-models" --input ecmwf-open-data \
  --assets "$GC_HOME" \
  --lead-time 168 --path graphcast.grib graphcast

"$VENV/bin/python" "$SCRIPT_DIR/extract_stations.py" graphcast.grib --model graphcast
echo "[$(date -Is)] graphcast run done"

# Keep the last few runs only; GRIBs are large.
ls -dt "$GC_HOME"/runs/* | tail -n +4 | xargs -r rm -rf
