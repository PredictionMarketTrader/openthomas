#!/usr/bin/env bash
# Run one Pangu inference on a remote GPU box and pull the station rows back.
#
#   scripts/nwp/run_remote.sh [host]     # default host: gpu-host2
#
# The remote needs: this repo at ~/openthomas, the nwp venv-gpu, and the
# model assets (see docs comments in run_pangu.sh). Compute servers produce
# artifacts; the trading box consumes files — ssh is the whole protocol.
set -euo pipefail

HOST="${1:-gpu-host2}"
OUT="$HOME/.openthomas/local-models.jsonl"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"

# The GPU box may have no clean route to GitHub — push the minimal file set
# over ssh instead of git-pulling there. Layout is preserved so
# extract_stations.py finds the station registry relative to itself.
echo "[$(date -Is)] syncing pipeline to $HOST"
rsync -a --relative \
  "$REPO/./scripts/nwp/run_pangu.sh" \
  "$REPO/./scripts/nwp/extract_stations.py" \
  "$REPO/./openthomas/weather/stations.py" \
  "$HOST:openthomas/"

echo "[$(date -Is)] remote pangu run on $HOST"
# The GPU box has no clean route to ECMWF — reverse-SOCKS the downloads
# through this box (needs pysocks in the remote venv; OpenSSH ≥7.6).
ssh -R 18080 -o ExitOnForwardFailure=yes "$HOST" \
  'cd ~/openthomas && ALL_PROXY=socks5h://127.0.0.1:18080 HTTPS_PROXY=socks5h://127.0.0.1:18080 \
   bash scripts/nwp/run_pangu.sh --gpu'

RUN=$(ssh "$HOST" 'ls -dt ~/.openthomas/nwp/runs/* | head -1')
mkdir -p "$(dirname "$OUT")"
ssh "$HOST" "cat '$RUN/rows.jsonl'" >> "$OUT"
echo "[$(date -Is)] merged $(ssh "$HOST" "wc -l < '$RUN/rows.jsonl'") rows from $HOST → $OUT"
