#!/usr/bin/env bash
# Arrangement kurucusunun tam dogrulamasi -- Ableton Live ACILMADAN.
# Live'in kendi davranisi disinda her sey burada kanitlanir: bolum cikarimi,
# locator yerlesimi, klip zamanlamasi, doseme, tekrar calistirilabilirlik.
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
echo " 4/5  Tip kontrolu (tsc --noEmit)"
echo "=============================================="
( cd "$EXT" && npx tsc --noEmit && echo "tsc temiz" )

echo
echo "=============================================="
echo " 5/5  Arrangement kurucusu (sahte Live)"
echo "=============================================="
( cd "$EXT" && npm run --silent test:arrangement )

echo
echo "=============================================="
echo " Hepsi gecti. Live acilmadi."
echo "=============================================="
