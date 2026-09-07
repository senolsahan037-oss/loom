#!/usr/bin/env bash
# The subset of Loom's suites that needs neither Ableton Live nor the author's
# own projects: pure logic, fixtures, the extension protocol against a fake
# consumer, and the MCP server's protocol surface. The full suite is
# scripts/check_all.sh and needs a real Ableton install.
#
# Every suite ends as passed, failed or skipped, and the summary counts them.
# A suite whose dependencies are missing is skipped and says why; it is never
# counted as passed.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASSED=(); FAILED=(); SKIPPED=()

banner() {
  echo
  echo "=============================================="
  echo " $1"
  echo "=============================================="
}

run() {
  local label="$1"; shift
  banner "$label"
  if "$@"; then PASSED+=("$label"); else FAILED+=("$label"); fi
}

skip() {
  local label="$1" why="$2"
  banner "$label: SKIPPED ($why)"
  SKIPPED+=("$label ($why)")
}

run "Reader contract (one owner per fact)" python3 "$ROOT/tests/test_reader_contract.py"
run "Prompt parsing"            node "$ROOT/ArrangementGPS/tests/promptMood.test.js"
run "Presetor"                  python3 "$ROOT/Presetor/tests/test_presetor.py"
run "AISoundDesigner"           python3 "$ROOT/AISoundDesigner/tests/test_sounddesigner.py"
run "Live project open verification" python3 "$ROOT/mcp_server/tests/test_live_project.py"
run "Extension-only MCP path (fake extension bridge)" python3 "$ROOT/mcp_server/tests/test_extension_path.py"
# The extension's own queue code as the consumer: needs node and the
# extension's node_modules (tsx). The SDK tarball is NOT needed for this --
# bridge.ts imports only SDK types.
EXT="$ROOT/extension"
if command -v node >/dev/null 2>&1 && [ -x "$EXT/node_modules/.bin/tsx" ]; then
  run "Real extension consumer (bridge.ts over a fake Live)" python3 "$ROOT/mcp_server/tests/test_bridge_consumer_real.py"
  run "Extension bridge (headless, tsx)" sh -c "cd \"$EXT\" && npx tsx tests/bridge.test.ts"
else
  skip "Real extension consumer (bridge.ts over a fake Live)" "needs node and npm install in extension/"
  skip "Extension bridge (headless, tsx)" "needs node and npm install in extension/"
fi
# The audio engines (Mix Check, crate agent) and the Sensei suite need Python
# 3.11+ and pytest; the audio ones also need the audio stack.
if python3 -c 'import sys, pytest; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  run "Musical time contract (Sensei)" sh -c "cd \"$ROOT/Sensei\" && python3 -m pytest tests/test_time_contract.py tests/test_midi_variation_engine.py tests/test_genre_synthesis.py tests/test_kit_resolver.py tests/test_kit_build_regressions.py -q"
else
  skip "Musical time contract (Sensei)" "needs Python 3.11+ with pytest"
fi
if python3 -c 'import sys, pytest, librosa, soundfile, pyloudnorm; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  run "Mix Check"                 sh -c "cd \"$ROOT/MixAnalyzer\" && python3 -m pytest tests/ -q"
  run "Crate agent"               sh -c "cd \"$ROOT/SampleAgent\" && python3 -m pytest tests/ -q"
else
  skip "Mix Check" "needs Python 3.11+ with pytest, librosa, soundfile, pyloudnorm"
  skip "Crate agent" "needs Python 3.11+ with pytest, librosa, soundfile, pyloudnorm"
fi
run "MCP protocol conformance"  python3 "$ROOT/mcp_server/tests/test_mcp_protocol.py"

echo
echo "=============================================="
echo " Summary: ${#PASSED[@]} passed, ${#FAILED[@]} failed, ${#SKIPPED[@]} skipped"
echo "=============================================="
for s in "${PASSED[@]:-}";  do [ -n "$s" ] && echo "  passed   $s"; done
for s in "${SKIPPED[@]:-}"; do [ -n "$s" ] && echo "  skipped  $s"; done
for s in "${FAILED[@]:-}";  do [ -n "$s" ] && echo "  FAILED   $s"; done
[ "${#FAILED[@]}" -eq 0 ]
