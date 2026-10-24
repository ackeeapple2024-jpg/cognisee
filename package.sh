#!/bin/bash
# Zip this folder for sharing: ../cognisee.zip
# Leaves out everything the recipient's ./setup.sh rebuilds (venv, models)
# and anything machine- or session-specific.

set -e
cd "$(dirname "$0")"
name="cognisee"
out="$(cd .. && pwd)/${name}.zip"

rm -f "$out"
# zip from the parent so the archive unpacks into a single cognisee/ folder
tmp="$(mktemp -d)"
ln -s "$(pwd)" "$tmp/$name"
(cd "$tmp" && zip -rq "$out" "$name/" \
    -x "$name/venv/*" \
    -x "$name/models/*" \
    -x "*/__pycache__/*" "*.pyc" \
    -x "$name/.cognisee.lock" \
    -x "$name/.claude/*" \
    -x "*.wav" "$name/camera_index_*_sample.jpg" "$name/doctor_camera_frame.jpg" \
    -x "*.DS_Store")
rm -rf "$tmp"

echo "Wrote $out ($(du -h "$out" | cut -f1))"
echo "Recipient: unzip, cd cognisee, ./setup.sh"
