#!/bin/bash
# Encrypt-at-rest: MIGRATE the real key dirs INTO the vault.  HUMAN-RUN, ONCE.
#
#   sudo bash scripts/vault_migrate.sh
#
# Order of operations (all reversible):
#   1. stop rnsd + lxmd + the UI so nothing holds the key files open
#   2. mount the (empty) vault
#   3. copy .lxmd / .reticulum / .reticulum-node-medic in, rename the originals
#      aside as *.pre-vault.bak (NOT deleted), and symlink the originals into
#      the vault  -> rnsd/lxmd/app find the same paths
#   4. leave services stopped; the human reboots (or runs the enable steps) so
#      they come up gated behind the vault unlock.
#
# The heavy lifting (copy + backup + symlink) is provisioning/vault.py so the
# same, unit-tested code path runs here and in the test suite.
set -euo pipefail

USER_HOME="${SUDO_USER:+/home/$SUDO_USER}"; USER_HOME="${USER_HOME:-$HOME}"
OWNER="${SUDO_USER:-$USER}"
TOOL="$(cd "$(dirname "$0")/.." && pwd)"

echo "== stopping services that hold the keys open =="
systemctl stop lxmd rnsd 2>/dev/null || true
# Best-effort stop the UI (it opens ~/.reticulum-node-medic).
pkill -f "python3 .*main.py" 2>/dev/null || true

echo "== mounting the vault (you'll be asked for the passphrase) =="
bash "$TOOL/scripts/vault_mount.sh"

echo "== migrating key dirs into the vault =="
# Run as the owner so files inside the vault are owned correctly.
sudo -u "$OWNER" env HOME="$USER_HOME" python3 -m provisioning.vault apply-migration

echo ""
echo "Migration done. Originals kept as *.pre-vault.bak in $USER_HOME."
echo "Verify the mesh comes back, THEN remove the backups by hand:"
echo "    rm -rf $USER_HOME/.lxmd.pre-vault.bak $USER_HOME/.reticulum.pre-vault.bak $USER_HOME/.reticulum-node-medic.pre-vault.bak"
echo ""
echo "Next: install the boot gate ->  sudo bash scripts/vault_enable_boot.sh"
