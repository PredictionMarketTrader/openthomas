# Sourceable helper for running NWP on a GPU box we share with other tenants.
#
# The compute box is not ours alone: training jobs come and go, and CUDA indices
# reshuffle across reboots. So the NWP jobs choose a card at run time rather than
# trusting a UUID baked into config, which would otherwise send inference at
# whatever tenant owns that index today.
#
# We never evict anyone to make room. If no card is free the caller just comes
# back later — cheap, when the thing we're waiting to compute is triggered by a
# once-a-day reanalysis file rather than a deadline.
#
# Logging goes to stderr; stdout is the UUID a caller captures.

# Echo the UUID of the emptiest GPU with at least $1 GB free, or nothing at all if
# no card qualifies.
#
# "Nothing" means empty stdout, NOT a non-zero exit — the last stage is a cut that
# succeeds on empty input. Callers must test the string:
#
#   uuid="$(nwp_free_gpu 36)"
#   [ -n "$uuid" ] || { echo "no card free"; exit 0; }
#
# UUIDs rather than indices: CUDA_VISIBLE_DEVICES accepts them, and they survive
# another tenant's job starting or ending mid-run and renumbering the devices.
# Kept to a single awk rather than `... | sort -rn | head -1`: under `set -o
# pipefail` a head that exits early can SIGPIPE its upstream, which would abort a
# caller running with `set -e`. The `|| true` holds the contract from the other
# side — a box with no nvidia-smi at all reports "no card free", not a crash.
nwp_free_gpu() {
  local need_gb="${1:?need_gb}" out
  out="$(nvidia-smi --query-gpu=uuid,memory.total,memory.used \
                    --format=csv,noheader,nounits 2>/dev/null \
         | awk -F', *' -v need="$need_gb" '
             { free = ($2 - $3) / 1024
               if (free >= need && free > best) { best = free; uuid = $1 } }
             END { if (uuid != "") print uuid }')" || true
  printf '%s\n' "$out"
}
