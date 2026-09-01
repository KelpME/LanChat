#!/usr/bin/env bash
# lanchat-firewall.sh — open/close lanchat's port (4812) to the LAN via ufw.
#
# Uses pkexec (polkit) so every open/close shows a password prompt and NO
# permanent sudoers rule is created. The LAN subnet is detected read-only
# first (no root), then a single root (pkexec) invocation performs the status
# check + rule change, so exactly ONE prompt appears per action.
#
# Usage: lanchat-firewall.sh open | close
set -euo pipefail

PORT=4812
ACTION="${1:-open}"

# Detect the LAN subnet from the default-route interface (read-only, no
# sudo/polkit needed). Portable, no hardcoded subnet.
detect_lan() {
  local iface ipaddr
  iface=$(ip route show default | awk '{print $5; exit}')
  [ -n "$iface" ] || { echo "lanchat: could not detect default-route interface" >&2; exit 1; }
  ipaddr=$(ip -4 -o addr show dev "$iface" | awk '{print $4; exit}')
  [ -n "$ipaddr" ] || { echo "lanchat: no IPv4 address on $iface" >&2; exit 1; }
  # Turn 192.168.1.8/24 into 192.168.1.0/24 (assume /24 LAN).
  echo "$ipaddr" | awk -F'[./]' '{print $1"."$2"."$3".0/24"}'
}

LAN="$(detect_lan)"
echo "lanchat: detected LAN subnet $LAN"

case "$ACTION" in
  open|close)
    if ! command -v pkexec >/dev/null 2>&1; then
      echo "lanchat: pkexec not available — cannot prompt for admin rights" >&2
      exit 1
    fi
    # Run a tiny helper script (not an inline bash -c) so the polkit password
    # prompt shows a short command instead of a huge ufw string that gets
    # truncated. The helper runs the whole action as root via ufw.
    helper="$(cd "$(dirname "$0")" && pwd)/lanchat-firewall-root.sh"
    pkexec "$helper" "$ACTION" "$LAN" "$PORT"
    echo "lanchat: port $PORT $ACTION (from $LAN)"
    ;;
  *)
    echo "usage: lanchat-firewall.sh open|close" >&2
    exit 2
    ;;
esac
