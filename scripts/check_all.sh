#!/usr/bin/env bash
# Full verification of Loom -- WITHOUT opening Ableton Live.
#   1) prompt parsing, plan extraction, instrument coverage, typecheck,
#      arrangement builder   (scripts/check_arrangement.sh)
#   2) the Presetor and AISoundDesigner evidence layers
#   3) the extension-only MCP path (Python fake), the REAL extension consumer
#      (bridge.ts over a fake Live), then every one of the MCP server's 44
#      tools, over real stdio
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"$ROOT/scripts/check_arrangement.sh"

echo
echo "=============================================="
echo " Reader contract (one owner per fact)"
echo "=============================================="
python3 "$ROOT/tests/test_reader_contract.py"

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
echo " Extension-only MCP path (fake extension bridge)"
echo "=============================================="
python3 "$ROOT/mcp_server/tests/test_extension_path.py"

echo
echo "=============================================="
echo " Live project open verification (fake log + crash dir)"
echo "=============================================="
python3 "$ROOT/mcp_server/tests/test_live_project.py"

echo
echo "=============================================="
echo " Real extension consumer (bridge.ts over a fake Live)"
echo "=============================================="
python3 "$ROOT/mcp_server/tests/test_bridge_consumer_real.py"

echo
echo "=============================================="
echo " MCP protocol conformance"
echo "=============================================="
python3 "$ROOT/mcp_server/tests/test_mcp_protocol.py"

echo
echo "=============================================="
echo " MCP tools (44 tools, real stdio)"
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
echo " MixAnalyzer (pytest, committed fixtures)"
echo "=============================================="
(cd "$ROOT/MixAnalyzer" && python3 -m pytest tests/ -q)

echo
echo "=============================================="
echo " SampleAgent (pytest, committed fixtures)"
echo "=============================================="
(cd "$ROOT/SampleAgent" && python3 -m pytest tests/ -q)

echo
echo "=============================================="
echo " Shared dataset"
echo "=============================================="
(cd "$ROOT/Sensei/DatasetRoot" && python3 -m pytest tests/ -q)

echo
echo "=============================================="
echo " All passed. Live was never opened."
echo "=============================================="
