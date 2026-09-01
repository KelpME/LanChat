#!/usr/bin/env bash
# lanchat-firewall-root.sh — open/close lanchat's port (4812) to the LAN via
# ufw. Runs as ROOT (invoked through pkexec by lanchat-firewall.sh).
#
# Kept as a tiny standalone script so the polkit authorization prompt shows a
# SHORT command (the script path + a few short args) instead of a huge inline
# `bash -c 'ufw ...; ufw ...'` string, which got truncated in the prompt.
#
# Usage: lanchat-firewall-root.sh <open|close> <lan-subnet> <port>
set -euo pipefail

ACTION="${1:-open}"
LAN="${2:-}"
PORT="${3:-4812}"
[ -n "$LAN" ] || { echo "lanchat: no LAN subnet given" >&2; exit 1; }

case "$ACTION" in
  open)
    ufw status numbered 2>/dev/null | grep -q "$PORT/udp.*ALLOW" || ufw allow from "$LAN" to any port "$PORT" proto udp
    ufw status numbered 2>/dev/null | grep -q "$PORT/tcp.*ALLOW" || ufw allow from "$LAN" to any port "$PORT" proto tcp
    ;;
  close)
    ufw delete allow from "$LAN" to any port "$PORT" proto udp 2>/dev/null || true
    ufw delete allow from "$LAN" to any port "$PORT" proto tcp 2>/dev/null || true
    ;;
  *)
    echo "usage: lanchat-firewall-root.sh open|close <lan-subnet> <port>" >&2
    exit 2
    ;;
esac
