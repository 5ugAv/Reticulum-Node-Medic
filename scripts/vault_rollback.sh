#!/bin/bash
# Encrypt-at-rest: ROLL BACK to plaintext.  HUMAN-RUN. Fully reversible; must
# NOT brick the mesh — it restores the real key dirs exactly where they were.
#
#   sudo bash scripts/vault_rollback.sh
#
# Steps:
#   1. stop rnsd + lxmd + the UI
#   2. remove the boot gate (so services start without the vault again)
#   3. mount the vault, copy every dir back to its real path, drop the symlinks
#   4. unmount + lock the vault (the .img file is left on disk for you to delete)
#   5. re-harden permissions
# After this the medic is back to the pre-encryption state.
set -euo pipefail

USER_HOME="${SUDO_USER:+/home/$SUDO_USER}"; USER_HOME="${USER_HOME:-$HOME}"
OWNER="${SUDO_USER:-$USER}"
TOOL="$(cd "$(dirname "$0")/.." && pwd)"

echo "== stopping services =="
systemctl stop lxmd rnsd 2>/dev/null || true
pkill -f "python3 .*main.py" 2>/dev/null || true

echo "== removing boot gate =="
rm -f /etc/systemd/system/rnsd.service.d/vault.conf \
      /etc/systemd/system/lxmd.service.d/vault.conf \
      /etc/systemd/system/nodemedic-vault.service
systemctl daemon-reload
systemctl disable nodemedic-vault.service 2>/dev/null || true

echo "== mounting vault to copy data back out =="
bash "$TOOL/scripts/vault_mount.sh"

echo "== restoring real key dirs =="
sudo -u "$OWNER" env HOME="$USER_HOME" python3 -m provisioning.vault revert-migration

echo "== locking + unmounting vault =="
bash "$TOOL/scripts/vault_unmount.sh"

echo "== re-hardening permissions =="
sudo -u "$OWNER" env HOME="$USER_HOME" python3 -c "from provisioning.harden import harden_permissions; print('rehardened:', harden_permissions())" || true

echo ""
echo "Rollback complete. The medic runs plaintext-at-rest again."
echo "The encrypted container is still on disk; delete it when satisfied:"
echo "    rm -f $USER_HOME/.nodemedic-vault.img"
echo "Then reboot:  sudo reboot"
