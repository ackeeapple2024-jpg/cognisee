#!/bin/bash
# Start CogniSee. Run ./setup.sh once first.
# For lid-closed operation use ./run_headless.sh instead.

set -e
cd "$(dirname "$0")"

if [[ ! -x venv/bin/python ]]; then
    echo "No venv yet -- run ./setup.sh first." >&2
    exit 1
fi

exec ./venv/bin/python -u main.py "$@"
