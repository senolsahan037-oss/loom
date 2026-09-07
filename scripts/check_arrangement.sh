#!/usr/bin/env bash
# The arrangement chain WITHOUT opening Ableton Live: prompt parsing, the
# instrument coverage of the plan against Sensei's catalogue, the extension's
# typecheck and its bridge (the arrangement writer IS the bridge's
# write_arrangement_clip; project_build in the MCP drives it).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXT="$ROOT/extension"

echo "=============================================="
echo " 1/4  Prompt cozumleme (promptMood.js)"
echo "=============================================="
( cd "$ROOT/ArrangementGPS" && node tests/promptMood.test.js )

echo
echo "=============================================="
echo " 2/4  Enstruman kapsami (Sensei katalogu)"
echo "=============================================="
python3 "$ROOT/scripts/check_instrument_coverage.py"

echo
echo "=============================================="
echo " 3/4  Typecheck (tsc --noEmit)"
echo "=============================================="
( cd "$EXT" && npx tsc --noEmit && echo "tsc temiz" )

echo
echo "=============================================="
echo " 4/4  Extension koprusu (sahte Live, gercek kuyruk kodu)"
echo "=============================================="
( cd "$EXT" && npm run --silent test:bridge )

echo
echo "=============================================="
echo " All passed. Live was never opened."
echo "=============================================="
