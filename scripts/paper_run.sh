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
# A kill-switch halt is recorded as a file, not only as a log line, because
# scripts/watchdog.sh has to tell an intentional halt from an ordinary crash — and a
# log line is a fragile contract between two programs (it previously got scrolled
# out of the window the watchdog scanned, which silently defeated the halt).
#
# Starting this supervisor IS the act of resuming, so clearing the flag here means
# the operator needs no extra ritual beyond the restart they were going to do
# anyway. The watchdog never starts us while the flag exists, so it cannot clear it
# by accident.
HALT="${OPENTHOMAS_HALT_FILE:-$HOME/.openthomas/halted}"
mkdir -p "$(dirname "$LOG")" "$(dirname "$HALT")"
rm -f "$HALT"
echo "[$(date -Is)] supervisor start (pid $$)" >>"$LOG"

while true; do
  openthomas run >>"$LOG" 2>&1
  code=$?
  if [ "$code" -eq 3 ]; then
    echo "[$(date -Is)] drawdown kill-switch — supervisor stopping. Review the journal before resuming." >>"$LOG"
    printf '%s\n' \
      "[$(date -Is)] halted by the drawdown kill-switch." \
      "Review the journal, then restart scripts/paper_run.sh — starting it clears this file." \
      >"$HALT"
    exit 3
  fi
  echo "[$(date -Is)] loop exited code=$code; restarting in 120s" >>"$LOG"
  sleep 120
done
