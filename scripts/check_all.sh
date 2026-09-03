#!/usr/bin/env bash
# Full verification of Loom -- WITHOUT opening Ableton Live.
#   1) prompt parsing, plan extraction, instrument coverage, typecheck,
#      arrangement builder   (scripts/check_arrangement.sh)
#   2) the Presetor and AISoundDesigner evidence layers
#   3) every one of the MCP server's 33 tools, over real stdio
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"$ROOT/scripts/check_arrangement.sh"

echo
echo "=============================================="
echo " Presetor (device chain evidence + transplant)"
echo "=============================================="
python3 "$ROOT/Presetor/tests/test_presetor.py"

echo
echo "=============================================="
echo " AISoundDesigner (measured sound palette)"
echo "=============================================="
python3 "$ROOT/AISoundDesigner/tests/test_sounddesigner.py"

echo
echo "=============================================="
echo " Bridge command layer (fake Live)"
echo "=============================================="
python3 "$ROOT/AbletonScripts/Loom/test_bridge_ops.py"

echo
echo "=============================================="
echo " Live bridge (real remote script + MCP)"
echo "=============================================="
python3 "$ROOT/mcp_server/tests/test_live_bridge.py"

echo
echo "=============================================="
echo " MCP protocol conformance"
echo "=============================================="
python3 "$ROOT/mcp_server/tests/test_mcp_protocol.py"

echo
echo "=============================================="
echo " MCP tools (33 tools, real stdio)"
echo "=============================================="
python3 "$ROOT/mcp_server/tests/test_mcp_tools.py"

echo
echo "=============================================="
echo " Sensei (pytest)"
echo "=============================================="
(cd "$ROOT/Sensei" && python3 -m pytest tests/ -q)

echo
echo "=============================================="
echo " AIMixMaster (pytest, committed fixtures)"
echo "=============================================="
(cd "$ROOT/AIMixMaster" && python3 -m pytest tests/ -q)

echo
echo "=============================================="
echo " Shared dataset"
echo "=============================================="
(cd "$ROOT/Sensei/DatasetRoot" && python3 -m pytest tests/ -q)

echo
echo "=============================================="
echo " ArrangementGPS plan extraction"
echo "=============================================="
python3 "$ROOT/AbletonScripts/ArrangementGPSBuilder/test_plan_extraction.py"

echo
echo "=============================================="
echo " All passed. Live was never opened."
echo "=============================================="
