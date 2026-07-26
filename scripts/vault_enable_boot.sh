#!/bin/bash
# Encrypt-at-rest: install the BOOT GATE.  HUMAN-RUN, after vault_migrate.sh.
#
#   sudo bash scripts/vault_enable_boot.sh
#
# Wires rnsd + lxmd to Require the nodemedic-vault.service unlock unit, so on
# every boot they wait until the operator unlocks the vault on the touchscreen.
# Reversible: scripts/vault_rollback.sh removes all of this.
set -euo pipefail
TOOL="$(cd "$(dirname "$0")/.." && pwd)"

command -v systemd-ask-password >/dev/null || echo "warning: systemd-ask-password missing"

echo "== installing vault unlock unit =="
cp "$TOOL/scripts/nodemedic-vault.service" /etc/systemd/system/nodemedic-vault.service

echo "== gating rnsd + lxmd behind the vault =="
mkdir -p /etc/systemd/system/rnsd.service.d /etc/systemd/system/lxmd.service.d
cp "$TOOL/scripts/rnsd-vault-override.conf" /etc/systemd/system/rnsd.service.d/vault.conf
cp "$TOOL/scripts/lxmd-vault-override.conf" /etc/systemd/system/lxmd.service.d/vault.conf

systemctl daemon-reload
systemctl enable nodemedic-vault.service

echo ""
echo "Boot gate installed. On next boot rnsd/lxmd wait for the vault passphrase."
echo "Make sure the touchscreen unlock agent runs at login (see ui/vault_unlock.py)."
echo "Reboot to test:  sudo reboot"
