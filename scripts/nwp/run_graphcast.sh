#!/usr/bin/env bash
# One GraphCast (operational, 0.25° / 13-level) inference → OpenThomas's local
# model source. GraphCast is the deterministic POINT-estimate NWP (it replaced
# Pangu); GenCast — the diffusion ensemble that yields calibrated probabilities —
# is a separate, GPU-only pipeline (see run_gencast.sh, roadmap).
#
# Default: CPU (~55 min/run) — fine for the 2×/day cadence, since ECMWF open-data
# initial conditions are ~7 h stale anyway, so faster inference buys no latency.
# GraphCast operational peaks ~26 GB on GPU: it fits a 48 GB A6000 / 80 GB A800,
# but NOT a 24 GB card (4090/3090). On a 24 GB box, use CPU.
#
# --gpu: infer on a big (≥32 GB) card (~minutes). Pin it with OPENTHOMAS_GPU_UUID.
# If a vLLM container owns the VRAM, set OPENTHOMAS_VLLM_CONTAINER and --gpu stops
# it for the run and restarts it after (the served model is briefly unavailable,
# so that case is hand-run, not cron).
#
# Requires the patched GraphCast venv at $OPENTHOMAS_GC_HOME/venv-gc and the
# weights under $OPENTHOMAS_GC_HOME/{params,stats} — build both with
# setup_graphcast_env.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"  # resolve before any cd
GC_HOME="${OPENTHOMAS_GC_HOME:-$HOME/.openthomas/graphcast}"
VENV="$GC_HOME/venv-gc"
VLLM_CONTAINER="${OPENTHOMAS_VLLM_CONTAINER:-}"

if [ "${1:-}" = "--gpu" ]; then
  # Pin to one GPU when the box is shared (UUIDs survive index reshuffles).
  if [ -n "${OPENTHOMAS_GPU_UUID:-}" ]; then
    export CUDA_VISIBLE_DEVICES="$OPENTHOMAS_GPU_UUID"
    echo "[$(date -Is)] pinned inference to the configured GPU"
  fi
  # Allocate on demand instead of grabbing 75% up front — lets the 26 GB model
  # use the whole card, and keeps a shared card's headroom honest.
  export XLA_PYTHON_CLIENT_PREALLOCATE=false
  if [ -n "$VLLM_CONTAINER" ] && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$VLLM_CONTAINER"; then
    echo "[$(date -Is)] stopping vLLM container for GPU inference"
    docker stop "$VLLM_CONTAINER"
    trap 'echo "[$(date -Is)] restarting vLLM container"; docker start "$VLLM_CONTAINER"' EXIT
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
