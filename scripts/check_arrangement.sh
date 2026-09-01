#!/usr/bin/env bash
# Full verification of the arrangement builder -- WITHOUT opening Ableton Live.
# Everything except Live's own behaviour is proven here: section extraction,
# locator placement, clip timing, tiling and repeatability.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXT="$ROOT/Sensei/extensions/sensei-midi-writer"

echo "=============================================="
echo " 1/5  Prompt cozumleme (promptMood.js)"
echo "=============================================="
( cd "$ROOT/ArrangementGPS" && node tests/promptMood.test.js )

echo
echo "=============================================="
echo " 2/5  Plan cikarimi (ArrangementGPSBuilder.py)"
echo "=============================================="
python3 "$ROOT/AbletonScripts/ArrangementGPSBuilder/test_plan_extraction.py"

echo
echo "=============================================="
echo " 3/5  Enstruman kapsami (Sensei katalogu)"
echo "=============================================="
python3 "$ROOT/scripts/check_instrument_coverage.py"

echo
echo "=============================================="
echo " 4/5  Typecheck (tsc --noEmit)"
echo "=============================================="
( cd "$EXT" && npx tsc --noEmit && echo "tsc temiz" )

echo
echo "=============================================="
echo " 5/5  Arrangement kurucusu (sahte Live)"
echo "=============================================="
( cd "$EXT" && npm run --silent test:arrangement )

echo
echo "=============================================="
echo " All passed. Live was never opened."
echo "=============================================="
