#!/usr/bin/env bash
# lanchat-sudoers.sh — install a scoped sudoers rule so the lanchat user can
# open/close its own firewall port (4812) WITHOUT a password prompt.
#
# The rule grants ONLY the exact ufw commands lanchat-firewall.sh runs for
# port 4812 — nothing else. No shell, no other rules, no other ports. This is
# the minimal privilege that lets install open the LAN port automatically.
#
# Requires sudo (the operator runs this once at install).
set -euo pipefail

SUDOERS_FILE="/etc/sudoers.d/lanchat"
USER="${SUDO_USER:-$USER}"

# The exact ufw invocations lanchat-firewall.sh uses, all scoped to port 4812.
# %r = the LAN subnet placeholder; the rule allows ANY LAN CIDR so the script
# can detect it at runtime. Only the specific "allow/delete ... port 4812"
# forms are permitted.
RULE="# Lanchat firewall management (installed by lanchat-sudoers.sh)
# Grants $USER permission to open/close ONLY lanchat's port 4812 via ufw.
# This cannot run arbitrary commands or touch other rules/ports.
$USER ALL=(root) NOPASSWD: /usr/sbin/ufw status numbered
$USER ALL=(root) NOPASSWD: /usr/sbin/ufw allow from * to any port 4812 proto udp
$USER ALL=(root) NOPASSWD: /usr/sbin/ufw allow from * to any port 4812 proto tcp
$USER ALL=(root) NOPASSWD: /usr/sbin/ufw delete allow from * to any port 4812 proto udp
$USER ALL=(root) NOPASSWD: /usr/sbin/ufw delete allow from * to any port 4812 proto tcp
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
echo "  grants: ufw allow/delete port 4812 (udp+tcp) only"
