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
PIDFILE="${OPENTHOMAS_PIDFILE:-$HOME/.openthomas/supervisor.pid}"
# Is this pid our supervisor? Two questions, both asked of argv *elements* rather than
# of the flattened command line:
#
#   argv[0] is a shell, and some later element IS the script.
#
# `pgrep -f` matches a substring of the whole command line, so it counts `vim
# scripts/paper_run.sh`, a grep, or an agent session running `cp scripts/paper_run.sh
# …` as a live supervisor — a failure that is silent and permanent, because the
# watchdog then believes all is well and quietly stops guarding. Requiring argv[0] to
# be a shell rejects the editor and the grep; requiring a whole-element match rejects
# the `bash -c '…long script…'` shape, where the path is buried inside one argument.
# The live box has one of each right now: the supervisor, and the shell that launched
# it whose -c argument still mentions the path.
#
# Element boundaries are read with bash's own NUL-delimited read rather than any
# `grep -z`, whose meaning is not portable — GNU grep spells null-data that way, but
# ugrep (which is `grep` on one of our boxes) spells *decompress* that way. Both
# happen to answer correctly today; neither is a contract worth resting this on. An
# argv element can also contain newlines, so anything that splits on them lets one
# argument masquerade as several — which is exactly how this check's first draft
# mistook its own test harness for the supervisor.
#
# Reading /proc also proves liveness, and re-checking identity defeats pid reuse: a
# pidfile left behind by a hard kill, or surviving a reboot, can name a stranger.
#
# Assumes the documented invocation, `nohup scripts/paper_run.sh &`, whose shebang
# leaves argv as [bash, scripts/paper_run.sh].
is_supervisor() {
  local pid="${1:-}" arg first=1
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  [ -r "/proc/$pid/cmdline" ] || return 1
  while IFS= read -r -d '' arg; do
    if [ "$first" = 1 ]; then
      first=0
      # A glob, so the trailing ".sh" needs no regex escaping.
      case "${arg##*/}" in sh|bash|dash) ;; *) return 1 ;; esac
      continue
    fi
    case "$arg" in */paper_run.sh|paper_run.sh) return 0 ;; esac
  done < "/proc/$pid/cmdline"
  return 1
}

# The pidfile is the precise answer, once the running supervisor is new enough to
# have written one.
if [ -r "$PIDFILE" ] && is_supervisor "$(head -n1 "$PIDFILE" 2>/dev/null)"; then
  exit 0  # supervisor alive, nothing to do
fi

# No usable pidfile — a supervisor predating the pidfile still runs from memory, and
# starting a second one would put two agents with separate journals on the same
# bankroll, which has happened here before. So fall back to a scan, but verify each
# candidate: pgrep is only a cheap prefilter, and /proc is the authority.
#
# Both halves of that matter on the live box, where the pgrep pattern currently
# matches two processes: the supervisor itself (`bash scripts/paper_run.sh`) and the
# shell that launched it, whose single -c argument still mentions the path. Trusting
# pgrep alone would count the launcher as a supervisor; skipping the scan would miss
# the real one and start a duplicate.
for pid in $(pgrep -f "[p]aper_run\.sh" 2>/dev/null); do
  is_supervisor "$pid" && exit 0
done

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
