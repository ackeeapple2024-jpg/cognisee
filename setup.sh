#!/bin/bash
# One-time setup for CogniSee on an Apple Silicon Mac.
#
#   ./setup.sh
#
# Creates ./venv, installs the exact package versions from requirements.lock,
# and downloads the models config.py actually uses (~5 GB), so the first
# real run doesn't stall on downloads. Safe to re-run.

set -e
cd "$(dirname "$0")"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
    echo "CogniSee requires macOS on Apple Silicon (Apple Silicon)." >&2
    exit 1
fi

# requirements.lock was verified on Python 3.10 -- prefer it, accept 3.11/3.12
PY=""
for candidate in python3.10 python3.11 python3.12; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PY="$candidate"
        break
    fi
done
if [[ -z "$PY" ]]; then
    echo "Python 3.10 not found. Install it from https://www.python.org/downloads/ (3.10.x, macOS installer), then re-run." >&2
    exit 1
fi
echo "Using $($PY --version) at $(command -v $PY)"

if [[ ! -d venv ]]; then
    "$PY" -m venv venv
fi
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.lock

echo ""
echo "Downloading models (first time only, ~5 GB)..."
./venv/bin/python download_models.py

echo ""
echo "Setup done. Next:"
echo "  1. Plug in the camera, connect Bluetooth headset and the clicker (see README 'Quickstart')."
echo "  2. ./venv/bin/python doctor.py    # checks hardware + permissions"
echo "  3. ./run.sh                       # starts CogniSee"
