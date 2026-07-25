#!/usr/bin/env bash
# ERA5-triggered, GPU-opportunistic NWP batch.
#
#   nwp_batch.sh [--force] [--status]
#
# The trigger is data, not the clock. GenCast initialises from ERA5, which CDS
# publishes once a day, so there is nothing to compute until a new reanalysis day
# appears — and once one does, the only thing between us and a forecast is a free
# card on a box we share with training jobs.
#
# So cron fires every 15 minutes and this script decides what that means:
#
#     new ERA5 day?  ──no──▶  idle  (the common case: one date comparison, no
#           │yes                     network, no nvidia-smi, no noise)
#           ▼
#     free card?     ──no──▶  retry in 15 min  (we never evict another tenant;
#           │yes                                a busy box just costs us time)
#           ▼
#     run GenCast on the LATEST ERA5, and GraphCast too since we hold a card;
#     record the ERA5 day; go back to idle until the next one.
#
# Two consequences worth stating plainly:
#
#  * The ERA5 day is resolved when the card is won, not when the hunt began. If
#    the box stays busy for two days we don't then compute a two-day-old forecast
#    — we compute the current one, late.
#  * GraphCast rides along. Its own input (open-data) refreshes every 6 h, so
#    coupling it to ERA5 costs some freshness; holding a card and not spending the
#    ~10 minutes to refresh the site's temperature field costs more.
#
# Failures don't spin: a failed attempt backs off (default 60 min) instead of
# re-queuing a CDS request every quarter hour, and no stamp is written, so the
# hunt resumes on its own.
set -uo pipefail   # deliberately not -e: GraphCast must run even if GenCast dies

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/gpu.sh"

# Machine-specific settings live outside the repo — the open-source tree carries
# no fleet topology.
[ -f "$HOME/.openthomas/nwp.env" ] && . "$HOME/.openthomas/nwp.env"

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force)  FORCE=1 ;;
    --status) STATUS=1 ;;
    *) echo "usage: nwp_batch.sh [--force] [--status]" >&2; exit 2 ;;
  esac
done

MEMBERS="${OPENTHOMAS_GENCAST_MEMBERS:-8}"
LAG_DAYS="${OPENTHOMAS_ERA5_LAG_DAYS:-6}"
# Measured on an A800: GraphCast operational peaks ~34 GB with the JAX allocator
# on demand, GenCast 1.0° ~9 GB. Ask above the peak, not at it.
GC_NEED_GB="${OPENTHOMAS_GC_NEED_GB:-36}"
GX_NEED_GB="${OPENTHOMAS_GENCAST_NEED_GB:-12}"
FAIL_COOLDOWN_MIN="${OPENTHOMAS_NWP_FAIL_COOLDOWN_MIN:-60}"

STAMPS="$HOME/.openthomas/nwp-stamps"
mkdir -p "$STAMPS"
ERA5_DONE="$STAMPS/era5_done"     # the ERA5 day we last computed successfully
LAST_FAIL="$STAMPS/last_fail"     # mtime of the last failed attempt

# The freshest reanalysis day CDS should have. Resolved here, on every probe, so
# the value used is whatever is current when a card finally frees up.
TARGET="$(date -u -d "$LAG_DAYS days ago" +%Y%m%d)"
DONE="$(cat "$ERA5_DONE" 2>/dev/null || true)"

if [ "${STATUS:-0}" = 1 ]; then
  # nwp_free_gpu signals "nothing fits" with empty stdout, not a non-zero exit
  # (its last pipeline stage is a cut that succeeds on empty input), so test the
  # string rather than the status.
  gx_now="$(nwp_free_gpu "$GX_NEED_GB")"
  gc_now="$(nwp_free_gpu "$GC_NEED_GB")"
  echo "target ERA5 day     : $TARGET"
  echo "last computed       : ${DONE:-<never>}"
  echo "state               : $([ "$TARGET" = "$DONE" ] && echo idle || echo hunting)"
  echo "free card ${GX_NEED_GB}GB (GenCast)  : ${gx_now:-none}"
  echo "free card ${GC_NEED_GB}GB (GraphCast): ${gc_now:-none}"
  exit 0
fi

# Probes overlap when a run is slow (CDS queues ERA5 requests for many minutes),
# and two ai-models processes racing would clobber each other's shared constants
# cache. Same flock idiom as scripts/watchdog.sh.
LOCK=/tmp/openthomas-nwp-batch.lock
exec 9>"$LOCK"
flock -n 9 || { echo "[$(date -Is)] a batch is still running — skipping this probe"; exit 0; }

if [ "$FORCE" != 1 ] && [ "$TARGET" = "$DONE" ]; then
  echo "[$(date -Is)] ERA5 $TARGET already computed — idle until the next reanalysis day"
  exit 0
fi

if [ "$FORCE" != 1 ] && [ -f "$LAST_FAIL" ]; then
  age_min=$(( ( $(date +%s) - $(stat -c %Y "$LAST_FAIL") ) / 60 ))
  if [ "$age_min" -lt "$FAIL_COOLDOWN_MIN" ]; then
    echo "[$(date -Is)] last attempt failed ${age_min}m ago — backing off until ${FAIL_COOLDOWN_MIN}m"
    exit 0
  fi
fi

# ERA5 is the trigger, so gate the hunt on GenCast's smaller footprint: a card
# with room for the ensemble but not for GraphCast should still produce the
# spread the new reanalysis day exists to give us.
gx_uuid="$(nwp_free_gpu "$GX_NEED_GB")"
if [ -z "$gx_uuid" ]; then
  echo "[$(date -Is)] ERA5 $TARGET is new but no card has ${GX_NEED_GB}GB free — retrying in 15m"
  exit 0
fi

echo "[$(date -Is)] ERA5 $TARGET is new and a card is free — running the batch"
rc=0
RAN=""

# --- GenCast: the reason we woke up ------------------------------------------
echo "[$(date -Is)] --- GenCast: $MEMBERS-member ensemble, ERA5 $TARGET ---"
if OPENTHOMAS_GENCAST_GPU_UUID="$gx_uuid" \
   bash "$SCRIPT_DIR/run_gencast.sh" "$MEMBERS" "$TARGET"; then
  printf '%s\n' "$TARGET" > "$ERA5_DONE"
  rm -f "$LAST_FAIL"
  RAN="$RAN gencast"
  echo "[$(date -Is)] gencast ok — ERA5 $TARGET recorded"
else
  touch "$LAST_FAIL"
  echo "[$(date -Is)] gencast FAILED — backing off ${FAIL_COOLDOWN_MIN}m before the next attempt" >&2
  rc=1
fi

# --- GraphCast: we hold a card, so refresh the point forecast too -------------
# Re-probed rather than reusing gx_uuid: GenCast just released its memory, and
# GraphCast needs a card three times the size.
gc_uuid="$(nwp_free_gpu "$GC_NEED_GB")"
if [ -n "$gc_uuid" ]; then
  echo "[$(date -Is)] --- GraphCast: point forecast, latest open-data cycle ---"
  if OPENTHOMAS_GPU_UUID="$gc_uuid" bash "$SCRIPT_DIR/run_graphcast.sh" --gpu; then
    RAN="$RAN graphcast"
    echo "[$(date -Is)] graphcast ok"
  else
    echo "[$(date -Is)] graphcast FAILED" >&2
    rc=1
  fi
else
  echo "[$(date -Is)] no card with ${GC_NEED_GB}GB free for GraphCast — skipping it this round"
fi

echo "NWP_RAN=${RAN# }"   # machine-readable; run_remote.sh pulls only what ran
exit "$rc"
