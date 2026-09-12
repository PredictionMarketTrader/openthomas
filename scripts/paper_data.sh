#!/usr/bin/env bash
# Daily paper-data pipeline (docs/EXPERIMENTS.md): grow the leak-free record,
# freeze the replay window, run the offline experiments, publish the lot.
#
#   crontab: 30 10 * * * bash /path/to/openthomas/scripts/paper_data.sh
#
# Runs after the previous day's CLI reports and ACIS actuals exist (local
# morning), and after the 00Z/12Z bulletins the baselines read are archived.
# Every step is idempotent; a failed step logs and the next one still runs,
# because a missing HF push must not stop the data from being frozen.
set -u
cd "$(dirname "$0")/.." || exit 1
[ -f "$HOME/.openthomas/env" ] && . "$HOME/.openthomas/env"
# HF_TOKEN may live in the interactive shell's rc rather than the agent env.
if [ -z "${HF_TOKEN:-}" ] && [ -f "$HOME/.bashrc" ]; then
  eval "$(grep -E '^export HF_TOKEN=' "$HOME/.bashrc" || true)"
fi
export HF_TOKEN="${HF_TOKEN:-}"

BIN="${OPENTHOMAS_BIN:-$(pwd)/.venv/bin/openthomas}"
LOG="${OPENTHOMAS_PAPER_LOG:-$HOME/.openthomas/paper-data.log}"
OUT="${OPENTHOMAS_PAPER_DIR:-$HOME/.openthomas/paper}"
# The paper's pre-registered window START (docs/EXPERIMENTS.md). The end
# rolls forward daily (yesterday); the frozen file is named by its end date.
START="${OPENTHOMAS_PAPER_START:-2026-07-09}"
END="$(date -d yesterday +%F 2>/dev/null || date -v-1d +%F)"
mkdir -p "$OUT"
log() { echo "[$(date -Is)] $*" >>"$LOG"; }

log "paper-data start (window $START → $END)"

# 1. Grow the as-of record: NWP previous-runs (92-day reach), settlements, NBM / GFS MOS.
"$BIN" hindcast --days 92 >>"$LOG" 2>&1 || log "hindcast failed (continuing)"

# 2. Freeze the replay rows for the window — consensus, and each official baseline (E5).
ROWS="$OUT/rows-$START-to-$END.jsonl"
"$BIN" replay --start "$START" --end "$END" --rows-out "$ROWS" >>"$LOG" 2>&1 \
  || log "replay (consensus) failed"
for SRC in nbm gfsmos; do
  "$BIN" replay --start "$START" --end "$END" --source "$SRC" \
    --rows-out "$OUT/rows-$SRC-$START-to-$END.jsonl" >>"$LOG" 2>&1 || log "replay ($SRC) failed"
done

# 3. Offline experiments on the frozen rows (E1/E2/E6/E7, plus E5 when the baselines exist).
if [ -s "$ROWS" ]; then
  SOURCES=""
  for SRC in nbm gfsmos; do
    F="$OUT/rows-$SRC-$START-to-$END.jsonl"
    [ -s "$F" ] && SOURCES="${SOURCES:+$SOURCES,}$F"
  done
  "$BIN" ablate --rows-in "$ROWS" --out "$OUT/results-$START-to-$END.json" \
    ${SOURCES:+--sources "$SOURCES"} >>"$LOG" 2>&1 || log "ablate failed"
  # E4 is a model call per row; weekly is enough (Sundays), and it is cached.
  if [ "$(date +%u)" = 7 ] && [ "${OPENTHOMAS_PAPER_LLM:-1}" = 1 ]; then
    "$BIN" ablate --rows-in "$ROWS" --out "$OUT/results-llm-$START-to-$END.json" \
      --llm-deltas 0,0.05,0.10,0.15,0.25,1.0 --no-simulate >>"$LOG" 2>&1 || log "ablate --llm failed"
  fi
fi

# 4. Lineage export for E8, and the bias-stability table for E9.
"$BIN" improve --history --export "$OUT/lineage.json" >>"$LOG" 2>&1 || log "lineage export failed"

# 5. Publish: the as-of record plus everything frozen above.
if [ -n "$HF_TOKEN" ]; then
  "$BIN" push-forecasts --replay-dir "$OUT" >>"$LOG" 2>&1 || log "push-forecasts failed"
else
  log "HF_TOKEN unset — skipping push-forecasts"
fi
log "paper-data done"
