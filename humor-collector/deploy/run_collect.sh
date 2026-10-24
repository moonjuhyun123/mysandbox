#!/usr/bin/env bash
# Wrapper used by cron / systemd. Runs from the project directory with its virtualenv.
#   deploy/run_collect.sh                      -> recent popular posts, all sources
#   deploy/run_collect.sh --mode archive       -> next pages of the past-best lists
# Extra arguments are passed to `main.py collect`. Overlapping runs exit immediately (lock file).
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python main.py collect --source "${SOURCES:-all}" --limit "${LIMIT:-100}" "$@"
