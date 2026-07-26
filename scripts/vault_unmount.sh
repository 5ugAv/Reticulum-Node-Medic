#!/bin/bash
# Encrypt-at-rest: UNMOUNT + LOCK the vault.  HUMAN- or SERVICE-run.
#
#   sudo bash scripts/vault_unmount.sh
#
# Stop rnsd + lxmd + the app BEFORE calling this, or their open file handles on
# the mounted keys will block the unmount (the script will report it as busy).
set -euo pipefail

USER_HOME="${SUDO_USER:+/home/$SUDO_USER}"; USER_HOME="${USER_HOME:-$HOME}"
MAPPER="nodemedic_vault"
MOUNT="$USER_HOME/.nodemedic-vault"

if mountpoint -q "$MOUNT"; then
    sync
    umount "$MOUNT" || { echo "unmount failed (still in use?). Stop rnsd/lxmd/app first." >&2; exit 1; }
    echo "unmounted $MOUNT"
fi

if [ -e "/dev/mapper/$MAPPER" ]; then
    cryptsetup close "$MAPPER"
    echo "locked $MAPPER"
fi

# Detach any loop device still backing the container.
USER_HOME_CONTAINER="$USER_HOME/.nodemedic-vault.img"
for l in $(losetup -j "$USER_HOME_CONTAINER" 2>/dev/null | cut -d: -f1); do
    losetup -d "$l" 2>/dev/null || true
done
echo "vault at rest (encrypted)."
