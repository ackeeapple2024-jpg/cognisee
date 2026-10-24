#!/bin/bash
# Run CogniSee for lid-closed ("clamshell") operation -- no real monitor needed.
#
# Requires (see README.md "Lid-closed / clamshell operation"):
#   1. A cheap HDMI/DisplayPort "dummy plug" connected to the Mac, OR a real
#      external display. macOS will not stay awake with the lid closed
#      without one -- this is enforced by the OS/firmware, not something
#      caffeinate alone can override.
#   2. The Mac connected to power.
#
# `caffeinate -d -i -s` prevents display sleep, idle sleep, and system sleep
# while main.py runs; it does NOT override the lid-close sleep trigger by
# itself -- that's what the dummy plug is for.

set -e
cd "$(dirname "$0")"
source venv/bin/activate

echo "Starting CogniSee in headless/clamshell mode."
echo "Make sure a dummy display plug (or real external display) is connected"
echo "and the Mac is on power before closing the lid."
echo ""

exec caffeinate -d -i -s python3 -u main.py
