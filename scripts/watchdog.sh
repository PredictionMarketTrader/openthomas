#!/usr/bin/env bash
# Cron watchdog: restarts the paper-trading supervisor (scripts/paper_run.sh)
# if it isn't running — covers machine reboots (paper_run.sh has no autostart
# by design) and any accidental kill of the supervisor itself. Does NOT
# restart after the drawdown kill-switch: that halt is intentional and needs
# a human to review the journal first (see paper_run.sh).
#
#   crontab: */5 * * * * bash /path/to/openthomas/scripts/watchdog.sh
set -u
cd "$(dirname "$0")/.." || exit 1

LOCK=/tmp/openthomas-watchdog.lock
exec 9>"$LOCK"
flock -n 9 || exit 0  # a previous run is still mid-restart; skip this tick

LOG="${OPENTHOMAS_LOG:-$HOME/.openthomas/agent.log}"
# Our own chatter goes to a separate file and never into $LOG, so $LOG stays
# quiescent once the supervisor exits and its tail remains a stable record of why.
WLOG="${OPENTHOMAS_WATCHDOG_LOG:-$HOME/.openthomas/watchdog.log}"
# Explicit state written by paper_run.sh on a kill-switch exit, and cleared when a
# human restarts it. This is the authoritative signal.
HALT="${OPENTHOMAS_HALT_FILE:-$HOME/.openthomas/halted}"

if pgrep -f "[p]aper_run\.sh" >/dev/null; then
  exit 0  # supervisor alive, nothing to do
fi

if [ -f "$HALT" ]; then
  echo "[$(date -Is)] watchdog: halt flag present — kill-switch stop, not auto-restarting; review the journal, then start paper_run.sh" >>"$WLOG"
  exit 0
fi

# Second line of defence for the case where this watchdog is newer than the
# paper_run.sh beside it, which would halt without writing the flag. Restarting an
# agent the kill-switch deliberately stopped is the one mistake here worth being
# paranoid about, so a stale pairing should fail closed rather than open.
if [ -f "$LOG" ] && tail -n 20 "$LOG" | grep -q "drawdown kill-switch"; then
  echo "[$(date -Is)] watchdog: kill-switch in the log but no halt flag — not auto-restarting; check that paper_run.sh is current" >>"$WLOG"
  exit 0
fi

echo "[$(date -Is)] watchdog: supervisor not running — restarting" >>"$WLOG"
nohup scripts/paper_run.sh >/dev/null 2>&1 &
disown
