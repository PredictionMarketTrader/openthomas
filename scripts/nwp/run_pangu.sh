#!/usr/bin/env bash
# One Pangu-Weather inference run → OpenThomas local model source.
#
# Default: CPU (~1h/run) — fine for the 2x/day cadence, since ECMWF open-data
# initial conditions are ~7h stale anyway, so faster inference buys no latency.
#
# --gpu: infer on GPU (~2min). On a box with a free card this is the normal,
# cron-safe path — pin it with OPENTHOMAS_GPU_UUID and go. If this box also
# serves a vLLM container that owns the VRAM, set OPENTHOMAS_VLLM_CONTAINER and
# --gpu will stop it for the run and restart it after; the served model is
# briefly unavailable then, so that case is hand-run, not cron.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"  # resolve before any cd
NWP_HOME="${OPENTHOMAS_NWP_HOME:-$HOME/.openthomas/nwp}"
VENV="$NWP_HOME/venv"
# If this box serves a vLLM container that owns the GPU, name it via this env var
# (kept in the private nwp.env, never committed) so --gpu can evict it for the
# run. Empty = a dedicated/free card, nothing to stop.
VLLM_CONTAINER="${OPENTHOMAS_VLLM_CONTAINER:-}"

if [ "${1:-}" = "--gpu" ]; then
  VENV="$NWP_HOME/venv-gpu"
  # Pin to one GPU when the box is shared with other tenants. CUDA_VISIBLE_DEVICES
  # honours GPU-UUID strings, which survive index reshuffles. Unset means device
  # 0, correct on a box we've just cleared.
  if [ -n "${OPENTHOMAS_GPU_UUID:-}" ]; then
    export CUDA_VISIBLE_DEVICES="$OPENTHOMAS_GPU_UUID"
    echo "[$(date -Is)] pinned inference to the configured GPU"
  fi
  # If a vLLM container owns the VRAM, evict it for the run and guarantee it
  # comes back. On a box with a free card there is none and the GPU is just free.
  if [ -n "$VLLM_CONTAINER" ] && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$VLLM_CONTAINER"; then
    echo "[$(date -Is)] stopping vLLM container for GPU inference"
    docker stop "$VLLM_CONTAINER"
    trap 'echo "[$(date -Is)] restarting vLLM container"; docker start "$VLLM_CONTAINER"' EXIT
  fi
else
  # The CPU venv carries the CPU onnxruntime build; belt-and-braces.
  export CUDA_VISIBLE_DEVICES=""
fi

WORK="$NWP_HOME/runs/$(date -u +%Y%m%dT%H%M)"
mkdir -p "$WORK"
cd "$WORK"

echo "[$(date -Is)] pangu run starting in $WORK (venv: $VENV)"
"$VENV/bin/ai-models" --input ecmwf-open-data \
  --assets "$NWP_HOME/assets" --download-assets \
  --lead-time 168 --path pangu.grib panguweather

"$VENV/bin/python" "$SCRIPT_DIR/extract_stations.py" pangu.grib --model pangu_local
echo "[$(date -Is)] pangu run done"

# Keep the last few runs only; GRIBs are ~2GB each.
ls -dt "$NWP_HOME"/runs/* | tail -n +4 | xargs -r rm -rf
