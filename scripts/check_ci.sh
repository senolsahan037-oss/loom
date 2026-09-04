#!/usr/bin/env bash
# The subset of Loom's suites that needs neither Ableton Live nor the author's
# own projects: pure logic, fixtures, and the MCP server's protocol surface.
# The full suite is scripts/check_all.sh and needs a real Ableton install.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

run() {
  echo
  echo "=============================================="
  echo " $1"
  echo "=============================================="
  shift
  "$@"
}

run "Prompt parsing"            node "$ROOT/ArrangementGPS/tests/promptMood.test.js"
run "Plan extraction"           python3 "$ROOT/AbletonScripts/ArrangementGPSBuilder/test_plan_extraction.py"
run "Bridge command layer"      python3 "$ROOT/AbletonScripts/Loom/test_bridge_ops.py"
run "Presetor"                  python3 "$ROOT/Presetor/tests/test_presetor.py"
run "AISoundDesigner"           python3 "$ROOT/AISoundDesigner/tests/test_sounddesigner.py"
# The audio engines (Mix Check, crate agent) need Python 3.11+ and the audio
# stack (numpy, scipy, librosa, soundfile, pyloudnorm). Where those are
# absent the suites are reported as skipped, never as passed.
if python3 -c 'import sys, pytest, librosa, soundfile, pyloudnorm; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  run "Mix Check"                 sh -c "cd \"$ROOT/MixAnalyzer\" && python3 -m pytest tests/ -q"
  run "Crate agent"               sh -c "cd \"$ROOT/SampleAgent\" && python3 -m pytest tests/ -q"
else
  echo
  echo "=============================================="
  echo " Mix Check / Crate agent: SKIPPED (need Python 3.11+ with pytest, librosa, soundfile, pyloudnorm)"
  echo "=============================================="
fi
run "MCP protocol conformance"  python3 "$ROOT/mcp_server/tests/test_mcp_protocol.py"

echo
echo "=============================================="
echo " All machine-independent suites passed."
echo "=============================================="
