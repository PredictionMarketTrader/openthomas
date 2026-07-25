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
# Our own chatter goes to a separate file and never into $LOG. Writing it there
# would walk the kill-switch line out of the 20-line window scanned below: one
# message per tick means that after 20 ticks an intentional halt looks like an
# ordinary crash, and we would restart an agent a human was supposed to review
# first. Keeping $LOG free of our writes makes it quiescent once the supervisor
# exits, so its tail stays a stable record of why it stopped.
WLOG="${OPENTHOMAS_WATCHDOG_LOG:-$HOME/.openthomas/watchdog.log}"

if pgrep -f "[p]aper_run\.sh" >/dev/null; then
  exit 0  # supervisor alive, nothing to do
fi

if [ -f "$LOG" ] && tail -n 20 "$LOG" | grep -q "drawdown kill-switch"; then
  echo "[$(date -Is)] watchdog: supervisor down on the drawdown kill-switch — not auto-restarting, needs manual review" >>"$WLOG"
  exit 0
fi

echo "[$(date -Is)] watchdog: supervisor not running — restarting" >>"$WLOG"
nohup scripts/paper_run.sh >/dev/null 2>&1 &
disown
