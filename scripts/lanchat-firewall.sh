#!/usr/bin/env bash
# lanchat-firewall.sh — open lanchat's port (4812) to the LAN via ufw.
#
# Lanchat needs UDP 4812 (discovery) and TCP 4812 (persistent messaging)
# reachable inbound FROM the LAN. This opens them, scoped to the LAN subnet
# only (never the internet).
#
# LAN detection uses `ip` (read-only, no sudo). The ufw commands run via a
# scoped sudoers rule at /etc/sudoers.d/lanchat that permits the owning user
# to run exactly these ufw invocations without a password.
#
# Usage: lanchat-firewall.sh open | close
set -euo pipefail

PORT=4812
ACTION="${1:-open}"

# Detect the LAN subnet from the default-route interface. Portable, no
# hardcoded subnet. This is read-only (no sudo needed).
detect_lan() {
  local iface
  iface=$(ip route show default | awk '{print $5; exit}')
  [ -n "$iface" ] || { echo "lanchat: could not detect default-route interface" >&2; exit 1; }
  local ipaddr
  ipaddr=$(ip -4 -o addr show dev "$iface" | awk '{print $4; exit}')
  [ -n "$ipaddr" ] || { echo "lanchat: no IPv4 address on $iface" >&2; exit 1; }
  # Turn 192.168.1.8/24 into 192.168.1.0/24 (assume /24 LAN).
  echo "$ipaddr" | awk -F'[./]' '{print $1"."$2"."$3".0/24"}'
}

LAN="$(detect_lan)"
echo "lanchat: detected LAN subnet $LAN"

case "$ACTION" in
  open)
    echo "lanchat: opening $PORT udp+tcp from $LAN"
    # These exact commands are what the sudoers rule permits (no password).
    if ! sudo -n ufw status numbered 2>/dev/null | grep -q "4812/udp.*ALLOW"; then
      sudo -n ufw allow from "$LAN" to any port $PORT proto udp
    else
      echo "lanchat: udp rule already present"
    fi
    if ! sudo -n ufw status numbered 2>/dev/null | grep -q "4812/tcp.*ALLOW"; then
      sudo -n ufw allow from "$LAN" to any port $PORT proto tcp
    else
      echo "lanchat: tcp rule already present"
    fi
    echo "lanchat: firewall open ($PORT udp+tcp from $LAN)"
    ;;
  close)
    echo "lanchat: closing $PORT udp+tcp from $LAN"
    sudo -n ufw delete allow from "$LAN" to any port $PORT proto udp 2>/dev/null || true
    sudo -n ufw delete allow from "$LAN" to any port $PORT proto tcp 2>/dev/null || true
    echo "lanchat: firewall closed"
    ;;
  *)
    echo "usage: lanchat-firewall.sh open|close" >&2
    exit 2
    ;;
esac
