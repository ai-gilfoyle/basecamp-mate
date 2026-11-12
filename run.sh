#!/bin/sh
# Timer entry point: renew the Basecamp OAuth token when it is near expiry, guarding the
# stored login (sync.py renew, login.py), then mirror the backlog. No model calls; see
# sync.py for the bounds. A run that cannot reach Basecamp (the boot window) is skipped.
# Usage: run.sh <firstmate home> <config.json>   (extra args, e.g. --dry-run, pass through)
[ $# -ge 2 ] || { echo "usage: run.sh <home> <config.json> [--dry-run]" >&2; exit 2; }
home=$1 config=$2; shift 2
here=$(cd "$(dirname "$0")" && pwd)
start=$(date +%s)
# The renewal and the sync share one 240s budget; the renewal is capped at 80s.
timeout 80 python3 "$here/sync.py" renew --home "$home" --config "$config" </dev/null
[ $? -eq 75 ] && exit 0  # Basecamp unreachable: the login is left alone and this run skipped
exec timeout $((240 - ($(date +%s) - start))) python3 "$here/sync.py" --home "$home" --config "$config" "$@"
