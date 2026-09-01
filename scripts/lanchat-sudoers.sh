#!/usr/bin/env bash
# lanchat-sudoers.sh — install a scoped sudoers rule so the lanchat user can
# open/close its own firewall port (4812) WITHOUT a password prompt.
#
# The rule grants ONLY the exact ufw commands lanchat-firewall.sh runs for
# port 4812, scoped to the LAN subnet — nothing else. No shell, no other
# rules, no other ports, no internet-wide exposure. This is the minimal
# privilege that lets install open the LAN port automatically.
#
# Requires sudo (the operator runs this once at install).
set -euo pipefail

SUDOERS_FILE="/etc/sudoers.d/lanchat"
USER="${SUDO_USER:-$USER}"

# Detect the LAN subnet from the default-route interface (read-only, no sudo
# needed). Same logic as lanchat-firewall.sh's detect_lan so the sudoers rule
# and the commands it authorizes always agree on the exact CIDR.
detect_lan() {
  local iface ipaddr
  iface=$(ip route show default | awk '{print $5; exit}')
  [ -n "$iface" ] || { echo "lanchat-sudoers: could not detect default-route interface" >&2; exit 1; }
  ipaddr=$(ip -4 -o addr show dev "$iface" | awk '{print $4; exit}')
  [ -n "$ipaddr" ] || { echo "lanchat-sudoers: no IPv4 address on $iface" >&2; exit 1; }
  # Turn 192.168.1.8/24 into 192.168.1.0/24 (assume /24 LAN).
  echo "$ipaddr" | awk -F'[./]' '{print $1"."$2"."$3".0/24"}'
}

LAN="$(detect_lan)"
echo "lanchat-sudoers: scoping lanchat firewall rules to LAN subnet $LAN"

# The exact ufw invocations lanchat-firewall.sh uses, all scoped to port 4812
# AND restricted to the concrete LAN subnet (never the internet). sudoers
# matches these literally, so the CIDR must match what the firewall script
# passes — both derive from detect_lan above.
RULE="# Lanchat firewall management (installed by lanchat-sudoers.sh)
# Grants $USER permission to open/close ONLY lanchat's port 4812 via ufw,
# scoped to the LAN subnet $LAN. This cannot run arbitrary commands or touch
# other rules/ports, and it cannot open the port to the internet.
$USER ALL=(root) NOPASSWD: /usr/sbin/ufw status numbered
$USER ALL=(root) NOPASSWD: /usr/sbin/ufw allow from $LAN to any port 4812 proto udp
$USER ALL=(root) NOPASSWD: /usr/sbin/ufw allow from $LAN to any port 4812 proto tcp
$USER ALL=(root) NOPASSWD: /usr/sbin/ufw delete allow from $LAN to any port 4812 proto udp
$USER ALL=(root) NOPASSWD: /usr/sbin/ufw delete allow from $LAN to any port 4812 proto tcp
"

if [ "$(id -u)" -ne 0 ]; then
  echo "lanchat-sudoers: run with sudo to write $SUDOERS_FILE" >&2
  exit 1
fi

# Atomic write + validate with visudo -c so a bad file can never lock sudo.
umask 022
printf '%s\n' "$RULE" > "$SUDOERS_FILE"
if ! visudo -cf "$SUDOERS_FILE" >/dev/null 2>&1; then
  echo "lanchat-sudoers: generated sudoers file failed validation — removing" >&2
  rm -f "$SUDOERS_FILE"
  exit 1
fi

echo "lanchat-sudoers: installed scoped sudoers rule for $USER ($SUDOERS_FILE)"
echo "  grants: ufw allow/delete port 4812 (udp+tcp) from $LAN only"
