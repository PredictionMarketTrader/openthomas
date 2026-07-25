#!/usr/bin/env bash
# Run NWP inference on a remote compute box and pull the artifacts back.
#
#   scripts/nwp/run_remote.sh [host] [mode]
#
# host: ssh target of a compute box (default: $OPENTHOMAS_NWP_HOST).
# mode: --batch  what the 15-minute cron runs: compute only if ERA5 has a new day
#                AND a card is free. Idle probes and busy boxes are no-ops, not
#                failures — see nwp_batch.sh for the state machine.
#       --gpu    GraphCast only, on a ≥36GB card (~8 min), unconditionally
#       ""       GraphCast only, on CPU (~55min, any box with >57GB RAM)
#
# The trading box owns no GPU worth the name, so it drives the compute box over
# ssh and consumes the files that come back: compute hosts produce artifacts, the
# trading box reads them, and ssh is the whole protocol. The remote needs this
# repo's nwp scripts (synced below) plus the venvs and weights that
# setup_{graphcast,gencast}_env.sh build.
set -euo pipefail

# Machine-specific settings (ssh host, GPU policy, link characteristics) live in a
# private file outside the repo and are never committed — keep the fleet's
# topology out of the open-source tree.
[ -f "$HOME/.openthomas/nwp.env" ] && . "$HOME/.openthomas/nwp.env"

HOST="${1:-${OPENTHOMAS_NWP_HOST:?pass a host as arg 1 or set OPENTHOMAS_NWP_HOST}}"
MODE="${2--gpu}"     # GPU by default; pass "" for CPU (${2-} keeps an explicit
                     # empty arg, unlike ${2:-} which would re-trigger the default).
                     # GraphCast operational peaks ~34GB on GPU: it fits an 80GB
                     # A800 or a 48GB A6000, not a 24GB card — use CPU ("") there.

# Which card to use is decided ON the compute box at run time (see gpu.sh): the
# free card differs run to run on a shared machine. Set OPENTHOMAS_GPU_UUID to a
# UUID only to override that; "auto" (the default) means pick a free one — and if
# none is free, wait for the next probe rather than evicting another tenant.
GPU_UUID="${OPENTHOMAS_GPU_UUID_OVERRIDE-${OPENTHOMAS_GPU_UUID:-auto}}"
# Ensemble size, ERA5 lag and the post-failure backoff travel with the job, so the
# whole policy is configured in one place (this box's nwp.env) rather than
# duplicated on every compute host.
MEMBERS="${OPENTHOMAS_GENCAST_MEMBERS:-8}"
LAG_DAYS="${OPENTHOMAS_ERA5_LAG_DAYS:-6}"
FAIL_COOLDOWN_MIN="${OPENTHOMAS_NWP_FAIL_COOLDOWN_MIN:-60}"
# Hosts without a clean route to ECMWF/CDS borrow this box's exit via reverse
# SOCKS. Set OPENTHOMAS_NWP_PROXY=0 for a host with its own internet: the link to
# the compute box can be far slower than its own uplink, and NWP initial
# conditions are hundreds of MB per run.
USE_PROXY="${OPENTHOMAS_NWP_PROXY:-1}"

OUT="$HOME/.openthomas/local-models.jsonl"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"

# The compute box may reach this box only over a flaky link; keep sessions alive
# and tolerate a slow connect rather than aborting a whole run on one hiccup.
SSH_OPTS=(-o ConnectTimeout=25 -o ServerAliveInterval=15 -o ServerAliveCountMax=4)

# The compute box may have no clean route to GitHub — push the minimal file set
# over ssh instead of git-pulling there. Layout is preserved so extract_*.py find
# the station registry relative to themselves.
echo "[$(date -Is)] syncing pipeline to $HOST"
rsync -a --relative -e "ssh ${SSH_OPTS[*]}" \
  "$REPO/./scripts/nwp/gpu.sh" \
  "$REPO/./scripts/nwp/nwp_batch.sh" \
  "$REPO/./scripts/nwp/run_graphcast.sh" \
  "$REPO/./scripts/nwp/run_gencast.sh" \
  "$REPO/./scripts/nwp/extract_stations.py" \
  "$REPO/./scripts/nwp/extract_gencast.py" \
  "$REPO/./openthomas/weather/stations.py" \
  "$HOST:openthomas/"

if [ "$MODE" = "--batch" ]; then
  REMOTE_CMD="bash scripts/nwp/nwp_batch.sh"
  echo "[$(date -Is)] probing $HOST for a spare card (graphcast + gencast, whichever is due)"
else
  REMOTE_CMD="bash scripts/nwp/run_graphcast.sh $MODE"
  echo "[$(date -Is)] remote graphcast run on $HOST (mode: ${MODE:-cpu})"
fi

REMOTE_ENV="OPENTHOMAS_GPU_UUID='$GPU_UUID' \
  OPENTHOMAS_GENCAST_GPU_UUID='$GPU_UUID' \
  OPENTHOMAS_GENCAST_MEMBERS='$MEMBERS' \
  OPENTHOMAS_ERA5_LAG_DAYS='$LAG_DAYS' \
  OPENTHOMAS_NWP_FAIL_COOLDOWN_MIN='$FAIL_COOLDOWN_MIN'"

SSH_FWD=()
if [ "$USE_PROXY" = "1" ]; then
  # Needs pysocks in the remote venv; OpenSSH ≥7.6 for a dynamic -R.
  SSH_FWD=(-R 18080 -o ExitOnForwardFailure=yes)
  REMOTE_ENV="$REMOTE_ENV ALL_PROXY=socks5h://127.0.0.1:18080 \
    HTTPS_PROXY=socks5h://127.0.0.1:18080"
fi

# Don't let a half-failed batch skip the pull: GenCast can fail (CDS outage, ERA5
# not published yet) while GraphCast succeeded, and that GraphCast field is still
# what the site should show. Capture the status, pull what exists, exit with it.
#
# The output is teed rather than swallowed because the batch reports which models
# actually produced something (NWP_RAN=...). A probe that ran nothing must pull
# nothing: re-appending an unchanged rows.jsonl every two hours would pile ~1700
# duplicate rows a day into local-models.jsonl, and extremes() re-parses that file
# in full on every station lookup.
LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT
set +e   # PIPESTATUS, not the pipeline's status: tee must not mask the ssh code
ssh "${SSH_OPTS[@]}" "${SSH_FWD[@]}" "$HOST" \
  "cd ~/openthomas && $REMOTE_ENV $REMOTE_CMD" 2>&1 | tee "$LOG"
RC=${PIPESTATUS[0]}
set -e
[ "$RC" -ne 0 ] && echo "[$(date -Is)] remote run exited $RC — pulling whatever landed" >&2

# Only --batch reports a marker; the other modes ran GraphCast unconditionally.
if [ "$MODE" = "--batch" ]; then
  RAN="$(sed -n 's/^NWP_RAN=//p' "$LOG" | tail -1)"
else
  RAN="graphcast"
fi
ran_this_probe() { case " $RAN " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }

# --- GraphCast artifacts: newest run dir on the remote ------------------------
if ran_this_probe graphcast; then
  RUN=$(ssh "${SSH_OPTS[@]}" "$HOST" 'ls -dt ~/.openthomas/graphcast/runs/* 2>/dev/null | head -1')
  mkdir -p "$(dirname "$OUT")"

  if [ -n "$RUN" ] && ssh "${SSH_OPTS[@]}" "$HOST" "test -f '$RUN/rows.jsonl'"; then
    ssh "${SSH_OPTS[@]}" "$HOST" "cat '$RUN/rows.jsonl'" >> "$OUT"
    echo "[$(date -Is)] merged $(ssh "${SSH_OPTS[@]}" "$HOST" "wc -l < '$RUN/rows.jsonl'") rows from $HOST → $OUT"
  else
    echo "[$(date -Is)] no rows.jsonl in '${RUN:-<no run dir>}' — consensus keeps its previous local-model rows" >&2
  fi

  # Our own forecast field for the site's heatmap. Best-effort: a missing grid
  # just leaves the globe on the Open-Meteo nowcast.
  if [ -n "$RUN" ] && ssh "${SSH_OPTS[@]}" "$HOST" "test -f '$RUN/tempgrid.json'"; then
    ssh "${SSH_OPTS[@]}" "$HOST" "cat '$RUN/tempgrid.json'" > "$HOME/.openthomas/graphcast-tempgrid.json"
    echo "[$(date -Is)] pulled GraphCast temperature grid → graphcast-tempgrid.json"
  fi

  # The daily forecast series for the globe's time axis (lazy-loaded by the site).
  if [ -n "$RUN" ] && ssh "${SSH_OPTS[@]}" "$HOST" "test -f '$RUN/tempseries.json'"; then
    ssh "${SSH_OPTS[@]}" "$HOST" "cat '$RUN/tempseries.json'" > "$HOME/.openthomas/graphcast-tempseries.json"
    echo "[$(date -Is)] pulled GraphCast temperature series → graphcast-tempseries.json"
  fi
fi

# --- GenCast artifact: the ensemble's flow-dependent spread -------------------
# extract_gencast.py writes it to the remote's home, not the run dir.
if ran_this_probe gencast; then
  if ssh "${SSH_OPTS[@]}" "$HOST" "test -f ~/.openthomas/gencast-spread.json"; then
    ssh "${SSH_OPTS[@]}" "$HOST" "cat ~/.openthomas/gencast-spread.json" \
      > "$HOME/.openthomas/gencast-spread.json"
    echo "[$(date -Is)] pulled GenCast spread → gencast-spread.json"
  else
    echo "[$(date -Is)] no gencast-spread.json on $HOST — sigma stays climatological" >&2
  fi
fi

exit "$RC"
