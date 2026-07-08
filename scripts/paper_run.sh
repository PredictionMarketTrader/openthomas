#!/usr/bin/env bash
# Supervised paper-trading run: restarts the loop on crashes, stops for real
# on the drawdown kill-switch (exit code 3), logs everything.
#
#   nohup scripts/paper_run.sh >/dev/null 2>&1 &
#
set -u
# Private per-machine secrets (e.g. a local vLLM --api-key) live outside the repo.
[ -f "$HOME/.openthomas/env" ] && . "$HOME/.openthomas/env"
LOG="${OPENTHOMAS_LOG:-$HOME/.openthomas/agent.log}"
mkdir -p "$(dirname "$LOG")"
echo "[$(date -Is)] supervisor start (pid $$)" >>"$LOG"

while true; do
  openthomas run >>"$LOG" 2>&1
  code=$?
  if [ "$code" -eq 3 ]; then
    echo "[$(date -Is)] drawdown kill-switch — supervisor stopping. Review the journal before resuming." >>"$LOG"
    exit 3
  fi
  echo "[$(date -Is)] loop exited code=$code; restarting in 120s" >>"$LOG"
  sleep 120
done
