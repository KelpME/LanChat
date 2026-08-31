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

# Derive the LAN subnet from the interface that carries the default route
# (the real network uplink). Prefer that over the first global interface so a
# VPN/tunnel that happens to enumerate first can't be picked. Falls back to the
# first non-virtual, non-/32 global IPv4 subnet.
SUBNET="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}' \
  | xargs -r -I{} ip -o -4 addr show to {}/32 2>/dev/null \
  | awk '{print $4}' | head -1)"
if [ -z "$SUBNET" ]; then
  # No default-route source: take the first usable (non-/32,/31) global subnet,
  # excluding virtual interfaces.
  SUBNET="$(ip -o -4 addr show scope global 2>/dev/null \
    | awk '$2 !~ /^(docker|br-|veth|virbr|vmnet|vboxnet|tailscale|wg|tun|tap)/ {print $4}' \
    | grep -vE '^127\.' | grep -vE '/3[12]$' | head -1)"
fi
if [ -z "$SUBNET" ]; then
  echo "! Could not auto-detect your LAN subnet. Please edit this script and set SUBNET manually." >&2
  exit 1
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
  # Add real rules. Requires a base chain named 'input' that accepts; if the
  # user's nftables setup differs, this may need adjusting.
  for port in 4812 4814; do
    nft add rule inet filter input ip saddr "$SUBNET" tcp dport "$port" accept 2>/dev/null \
      || nft add rule ip filter input ip saddr "$SUBNET" tcp dport "$port" accept 2>/dev/null
    nft add rule inet filter input ip saddr "$SUBNET" udp dport "$port" accept 2>/dev/null \
      || nft add rule ip filter input ip saddr "$SUBNET" udp dport "$port" accept 2>/dev/null
  done
  echo "  nftables rules added (inet/ip filter input). Verify: sudo nft list ruleset"
else
  echo "! No supported firewall detected. If you use one (iptables/nftables), allow"
  echo "  inbound TCP+UDP on ports 4812 and 4814 from your LAN subnet ($SUBNET)."
fi

echo "Done. Restart lanchat (or the shell) so it re-checks inbound reachability."
