#!/usr/bin/env bash
# Loom'in tamaminin dogrulanmasi -- Ableton Live ACILMADAN.
#   1) prompt cozumleme, plan cikarimi, enstruman kapsami, tip kontrolu,
#      arrangement kurucusu   (scripts/check_arrangement.sh)
#   2) Presetor ve AISoundDesigner kanit katmanlari
#   3) MCP sunucusunun 24 aracinin hepsi, gercek stdio uzerinden
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"$ROOT/scripts/check_arrangement.sh"

echo
echo "=============================================="
echo " Presetor (cihaz zinciri kaniti + transplant)"
echo "=============================================="
python3 "$ROOT/Presetor/tests/test_presetor.py"

echo
echo "=============================================="
echo " AISoundDesigner (olculmus ses paleti)"
echo "=============================================="
python3 "$ROOT/AISoundDesigner/tests/test_sounddesigner.py"

echo
echo "=============================================="
echo " Kopru komut katmani (sahte Live)"
echo "=============================================="
python3 "$ROOT/AbletonScripts/SenseiRemote/test_bridge_ops.py"

echo
echo "=============================================="
echo " Canli kopru (gercek remote script + MCP)"
echo "=============================================="
python3 "$ROOT/mcp_server/tests/test_live_bridge.py"

echo
echo "=============================================="
echo " MCP protokol uyumu"
echo "=============================================="
python3 "$ROOT/mcp_server/tests/test_mcp_protocol.py"

echo
echo "=============================================="
echo " MCP araclari (27 arac, gercek stdio)"
echo "=============================================="
python3 "$ROOT/mcp_server/tests/test_mcp_tools.py"

echo
echo "=============================================="
echo " Hepsi gecti. Live acilmadi."
echo "=============================================="
