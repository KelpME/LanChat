#!/usr/bin/env bash
# Lanchat — one-time firewall setup.
#
# Lanchat is peer-to-peer: every machine must accept inbound connections from
# the LAN. A deny-inbound firewall (UFW / firewalld) silently blocks this, which
# shows up as "I can send messages but never receive replies."
#
# This script opens the lanchat ports (TCP+UDP 4812, HTTP 4814) for your local
# subnet. Run it once with sudo on each machine running lanchat. Idempotent.
#
#   sudo bash scripts/setup-firewall.sh
set -euo pipefail

# Derive the local subnet from the default route's interface (e.g. 192.168.1.0/24).
SUBNET="$(ip -o -4 addr show scope global 2>/dev/null | awk '$2 !~ /^(docker|br-|veth|virbr|tailscale|wg|tun|tap)/ {print $4}' | grep -vE '^127\.' | head -1 || true)"
if [ -z "$SUBNET" ]; then
  SUBNET="192.168.1.0/24"
  echo "! Could not auto-detect subnet; defaulting to $SUBNET. Edit this script if wrong."
fi

echo "Opening lanchat ports for subnet: $SUBNET"

if command -v ufw >/dev/null 2>&1; then
  echo "[ufw]"
  for port in 4812 4814; do
    ufw allow in from "$SUBNET" to any port "$port" proto tcp
    ufw allow in from "$SUBNET" to any port "$port" proto udp
  done
  echo "  ufw rules added. Verify: sudo ufw status verbose"
elif command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active firewalld >/dev/null 2>&1; then
  echo "[firewalld]"
  for port in 4812 4814; do
    firewall-cmd --permanent --add-port="$port/tcp"
    firewall-cmd --permanent --add-port="$port/udp"
  done
  firewall-cmd --reload
  echo "  firewalld rules added. Verify: sudo firewall-cmd --list-ports"
elif command -v nft >/dev/null 2>&1; then
  echo "[nftables]"
  echo "  nftables detected. Add rules manually for ports 4812/4814 (TCP+UDP) from $SUBNET."
else
  echo "! No supported firewall detected. If you use one (iptables/nftables), allow"
  echo "  inbound TCP+UDP on ports 4812 and 4814 from your LAN subnet ($SUBNET)."
fi

echo "Done. Restart lanchat (or the shell) so it re-checks inbound reachability."
