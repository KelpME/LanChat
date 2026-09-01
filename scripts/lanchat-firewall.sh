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
    # One pkexec root invocation does the whole action so the user is prompted
    # exactly once. Everything inside runs as root via ufw.
    pkexec bash -c '
      set -e
      PORT='"$PORT"'
      LAN='"$LAN"'
      ACTION='"$ACTION"'
      if [ "$ACTION" = "open" ]; then
        ufw status numbered 2>/dev/null | grep -q "4812/udp.*ALLOW" || ufw allow from "$LAN" to any port "$PORT" proto udp
        ufw status numbered 2>/dev/null | grep -q "4812/tcp.*ALLOW" || ufw allow from "$LAN" to any port "$PORT" proto tcp
      else
        ufw delete allow from "$LAN" to any port "$PORT" proto udp 2>/dev/null || true
        ufw delete allow from "$LAN" to any port "$PORT" proto tcp 2>/dev/null || true
      fi
    '
    echo "lanchat: port $PORT $ACTION (from $LAN)"
    ;;
  *)
    echo "usage: lanchat-firewall.sh open|close" >&2
    exit 2
    ;;
esac
