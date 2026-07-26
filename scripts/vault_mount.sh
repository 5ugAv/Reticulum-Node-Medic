#!/bin/bash
# Encrypt-at-rest: UNLOCK + MOUNT the vault.  HUMAN- or SERVICE-run.
#
#   sudo bash scripts/vault_mount.sh          # prompts for the passphrase (tty)
#   printf '%s' "$PASS" | sudo bash scripts/vault_mount.sh --stdin
#
# --stdin reads the passphrase from stdin (how nodemedic-vault.service / the
# Kivy unlock screen feed it). On success the decrypted filesystem is at
# ~/.nodemedic-vault and the daemon-gate (nodemedic-vault.service) is satisfied.
set -euo pipefail

USER_HOME="${SUDO_USER:+/home/$SUDO_USER}"; USER_HOME="${USER_HOME:-$HOME}"
CONTAINER="$USER_HOME/.nodemedic-vault.img"
MAPPER="nodemedic_vault"
MOUNT="$USER_HOME/.nodemedic-vault"
OWNER="${SUDO_USER:-$USER}"

[ -e "$CONTAINER" ] || { echo "no vault at $CONTAINER — run vault_create.sh first" >&2; exit 1; }

# Already mounted? Nothing to do (idempotent for the boot service).
if mountpoint -q "$MOUNT"; then echo "already mounted at $MOUNT"; exit 0; fi

LOOP="$(losetup --find --show "$CONTAINER")"
detach_loop() { losetup -d "$LOOP" 2>/dev/null || true; }

if [ "${1:-}" = "--stdin" ]; then
    cryptsetup open --type luks2 --key-file - "$LOOP" "$MAPPER" || { detach_loop; exit 1; }
else
    cryptsetup open --type luks2 "$LOOP" "$MAPPER" || { detach_loop; exit 1; }
fi

install -d -o "$OWNER" -g "$OWNER" "$MOUNT"
# noatime: fewer metadata writes (the medic loses power abruptly; SD wear + the
# unflushed-write window both matter). errors=remount-ro to fail safe.
mount -o noatime,errors=remount-ro "/dev/mapper/$MAPPER" "$MOUNT"
chown "$OWNER":"$OWNER" "$MOUNT"
echo "vault mounted at $MOUNT"
