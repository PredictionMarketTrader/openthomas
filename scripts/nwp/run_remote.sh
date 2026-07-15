#!/usr/bin/env bash
# Run one GraphCast inference on a remote host and pull the station rows back.
#
#   scripts/nwp/run_remote.sh [host] [mode]
#
# host: ssh target of a compute box (default: $OPENTHOMAS_NWP_HOST).
# mode: "--gpu" (needs a ≥32GB card) or "" for CPU (~55min, any box).
#
# The remote needs this repo at ~/openthomas plus the GraphCast venv-gc and
# weights (see setup_graphcast_env.sh). Compute hosts produce artifacts; the
# trading box consumes files — ssh is the whole protocol.
set -euo pipefail

# Machine-specific settings (ssh host, GPU UUID, vLLM container name) live in a
# private file outside the repo and are never committed — keep the fleet's
# topology out of the open-source tree.
[ -f "$HOME/.openthomas/nwp.env" ] && . "$HOME/.openthomas/nwp.env"

HOST="${1:-${OPENTHOMAS_NWP_HOST:?pass a host as arg 1 or set OPENTHOMAS_NWP_HOST}}"
MODE="${2--gpu}"     # GPU by default; pass "" for CPU (${2-} keeps an explicit
                     # empty arg, unlike ${2:-} which would re-trigger the default).
                     # GraphCast operational peaks ~26GB on GPU: it fits a 48GB
                     # A6000 / 80GB A800, not a 24GB card — use CPU ("") there.
# Pin the remote run to one GPU when the box is shared (CUDA_VISIBLE_DEVICES
# honours GPU-UUID strings, which survive index reshuffles and other tenants).
# Set OPENTHOMAS_GPU_UUID in the private env above; empty means "use device 0".
# The GPU pin is host-specific — nwp.env holds the primary box's UUID, which is
# wrong on a single-GPU box. OPENTHOMAS_GPU_UUID_OVERRIDE (even empty)
# wins over nwp.env, so `OPENTHOMAS_GPU_UUID_OVERRIDE= run_remote.sh HOST --gpu`
# runs on that box's only card.
if [ -n "${OPENTHOMAS_GPU_UUID_OVERRIDE+x}" ]; then
  GPU_UUID="$OPENTHOMAS_GPU_UUID_OVERRIDE"
else
  GPU_UUID="${OPENTHOMAS_GPU_UUID:-}"
fi
# If a docker model server owns the remote GPU, name it here (in the private
# nwp.env) and --gpu will stop it for the run and restart it after, so the card
# never needs freeing by hand. Empty = a dedicated/free card.
VLLM_CONTAINER="${OPENTHOMAS_VLLM_CONTAINER:-}"
OUT="$HOME/.openthomas/local-models.jsonl"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"

# The GPU box may reach this box only over a flaky link; keep sessions alive and
# tolerate a slow connect rather than aborting a whole run on one hiccup.
SSH_OPTS=(-o ConnectTimeout=25 -o ServerAliveInterval=15 -o ServerAliveCountMax=4)

# The GPU box may have no clean route to GitHub — push the minimal file set over
# ssh instead of git-pulling there. Layout is preserved so extract_stations.py
# finds the station registry relative to itself.
echo "[$(date -Is)] syncing pipeline to $HOST"
rsync -a --relative -e "ssh ${SSH_OPTS[*]}" \
  "$REPO/./scripts/nwp/run_graphcast.sh" \
  "$REPO/./scripts/nwp/extract_stations.py" \
  "$REPO/./openthomas/weather/stations.py" \
  "$HOST:openthomas/"

echo "[$(date -Is)] remote graphcast run on $HOST (mode: ${MODE:-cpu})"
# The GPU box may have no clean route to ECMWF — reverse-SOCKS the downloads
# through this box (needs pysocks in the remote venv; OpenSSH ≥7.6).
ssh "${SSH_OPTS[@]}" -R 18080 -o ExitOnForwardFailure=yes "$HOST" \
  "cd ~/openthomas && OPENTHOMAS_GPU_UUID='$GPU_UUID' \
   OPENTHOMAS_VLLM_CONTAINER='$VLLM_CONTAINER' \
   ALL_PROXY=socks5h://127.0.0.1:18080 HTTPS_PROXY=socks5h://127.0.0.1:18080 \
   bash scripts/nwp/run_graphcast.sh $MODE"

RUN=$(ssh "${SSH_OPTS[@]}" "$HOST" 'ls -dt ~/.openthomas/graphcast/runs/* | head -1')
mkdir -p "$(dirname "$OUT")"
ssh "${SSH_OPTS[@]}" "$HOST" "cat '$RUN/rows.jsonl'" >> "$OUT"
echo "[$(date -Is)] merged $(ssh "${SSH_OPTS[@]}" "$HOST" "wc -l < '$RUN/rows.jsonl'") rows from $HOST → $OUT"

# Pull the global temperature grid too — our own forecast field for the site's
# heatmap. Best-effort: an older run may predate it, and a missing grid just
# leaves the globe on the Open-Meteo nowcast.
GRID="$HOME/.openthomas/graphcast-tempgrid.json"
if ssh "${SSH_OPTS[@]}" "$HOST" "test -f '$RUN/tempgrid.json'"; then
  ssh "${SSH_OPTS[@]}" "$HOST" "cat '$RUN/tempgrid.json'" > "$GRID"
  echo "[$(date -Is)] pulled GraphCast temperature grid → $GRID"
else
  echo "[$(date -Is)] no tempgrid.json in $RUN (older run?) — globe stays on Open-Meteo"
fi

# The daily forecast series for the globe's time axis (lazy-loaded by the site).
if ssh "${SSH_OPTS[@]}" "$HOST" "test -f '$RUN/tempseries.json'"; then
  ssh "${SSH_OPTS[@]}" "$HOST" "cat '$RUN/tempseries.json'" > "$HOME/.openthomas/graphcast-tempseries.json"
  echo "[$(date -Is)] pulled GraphCast temperature series → graphcast-tempseries.json"
fi
