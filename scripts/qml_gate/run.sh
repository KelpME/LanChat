#!/usr/bin/env bash
# Headless QML gate harness (steps of plans/PANEL-MODULARIZATION.md).
#
# /usr/bin/qml CANNOT load Quickshell modules (they are qrc-compiled into the
# quickshell binary). The gate runs `quickshell -p` against the live Wayland
# session instead. Setup it performs:
#   - bench dir with Commons/,Ui/ symlinked AT THE ROOT (qs.* imports resolve
#     relative to the -p root, not a qs/ subdir)
#   - shared/ = repo shared/ + stub Lanchat singleton (no daemon spawn)
#   - bench shell instantiates the component under test with stub inputs,
#     populates the model one frame late via Qt.callLater, asserts, Qt.exit(0|1)
#
# Usage: scripts/qml_gate/run.sh            (uses the committed bench shells)
# Known benign noise: modelData-undefined TypeErrors during the pre-populate
# frame — classify via A/B against a git-HEAD copy before treating as regression.
set -euo pipefail
BENCH=/tmp/qsgate/benchshell
REPO="$(cd "$(dirname "$0")/../.." && pwd)"

mkdir -p "$BENCH/shared"
ln -sfn /usr/share/omarchy/shell/Commons "$BENCH/Commons"
ln -sfn /usr/share/omarchy/shell/Ui "$BENCH/Ui"
cp "$REPO"/scripts/qml_gate/shell*.qml "$BENCH"/
cp "$REPO"/scripts/qml_gate/shared/qmldir "$BENCH"/shared/
cp "$REPO"/shared/ChatMessage.qml "$REPO"/shared/RoomMessage.qml "$BENCH"/shared/ 2>/dev/null || true
cp "$REPO"/shared/ComposeBox.qml "$BENCH"/shared/ 2>/dev/null || true
cp "$REPO"/shared/PeerList.qml "$BENCH"/shared/ 2>/dev/null || true
cp "$REPO"/shared/RoomListSection.qml "$BENCH"/shared/ 2>/dev/null || true
cp "$REPO"/shared/ChatThread.qml "$REPO"/shared/RoomView.qml "$BENCH"/shared/ 2>/dev/null || true
cp "$REPO"/scripts/qml_gate/shared/Lanchat.qml "$BENCH"/shared/
cp "$REPO"/shared/Lanchat.qml "$BENCH"/shared/Lanchat.real.qml 2>/dev/null || true
cp "$REPO"/shared/SettingsPanel.qml "$BENCH"/shared/
# Whole-panel bench: runs the REAL Panel.qml, copied next to the shell so
# panelbench's Qt.createComponent("Panel.qml") resolves inside the bench dir.
cp "$REPO"/scripts/qml_gate/panelbench.qml "$BENCH"/
cp "$REPO"/Panel.qml "$BENCH"/

for sh in "$BENCH"/shell*.qml "$BENCH"/panelbench.qml; do
  echo "=== $sh ==="
  WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-1}" XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}" \
    timeout 30 quickshell -p "$sh" 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | \
    { grep -E "BENCH|TypeError|ReferenceError|Unable to assign|is not defined|ERROR" | sort | uniq -c | sort -rn | head -12 || true; }
done
