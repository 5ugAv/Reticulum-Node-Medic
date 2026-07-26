#!/bin/bash
# Encrypt-at-rest: CREATE the LUKS2-on-a-file vault.  HUMAN-RUN on the medic.
#
#   sudo bash scripts/vault_create.sh
#
# Creates a 256 MiB file container, LUKS2-formats it with an argon2id passphrase
# KDF, puts an ext4 filesystem inside, and leaves it CLOSED. It does NOT migrate
# any data (that's vault_migrate.sh) and does NOT touch the real key dirs.
#
# Canonical parameters live in provisioning/vault.py — keep these in sync.
# Idempotent-ish: refuses if the container already exists (never clobbers).
set -euo pipefail

USER_HOME="${SUDO_USER:+/home/$SUDO_USER}"; USER_HOME="${USER_HOME:-$HOME}"
CONTAINER="$USER_HOME/.nodemedic-vault.img"
MAPPER="nodemedic_vault"
SIZE_MB=256
ARGON2_MEM_KIB=262144   # 256 MiB memory cost
ARGON2_PAR=4
ITER_MS=2000

command -v cryptsetup >/dev/null || { echo "cryptsetup not installed: sudo apt install cryptsetup"; exit 1; }

if [ -e "$CONTAINER" ]; then
    echo "REFUSING: $CONTAINER already exists. Delete it by hand if you really mean to recreate." >&2
    exit 1
fi

echo "== allocating $SIZE_MB MiB container at $CONTAINER =="
# fallocate is instant but a sparse hole; dd zero-fills so power-loss can't
# expose stale disk blocks in a not-yet-written region.
dd if=/dev/zero of="$CONTAINER" bs=1M count="$SIZE_MB" status=progress
chown "${SUDO_USER:-$USER}" "$CONTAINER"
chmod 600 "$CONTAINER"

LOOP="$(losetup --find --show "$CONTAINER")"
echo "== loop device: $LOOP =="
cleanup() { cryptsetup close "$MAPPER" 2>/dev/null || true; losetup -d "$LOOP" 2>/dev/null || true; }
trap cleanup EXIT

echo "== LUKS2 format (argon2id). You will be asked for a NEW passphrase. =="
echo "   Choose something you can type on the touchscreen at every boot."
cryptsetup luksFormat --type luks2 --cipher aes-xts-plain64 --key-size 512 \
    --hash sha256 --pbkdf argon2id --pbkdf-memory "$ARGON2_MEM_KIB" \
    --pbkdf-parallel "$ARGON2_PAR" --iter-time "$ITER_MS" --batch-mode --verify-passphrase \
    "$LOOP"

echo "== opening + making ext4 filesystem =="
cryptsetup open --type luks2 "$LOOP" "$MAPPER"
# ext4 keeps a journal -> crash-consistent across the medic's abrupt power loss.
mkfs.ext4 -q -L nodemedic_vault "/dev/mapper/$MAPPER"

echo "== closing (leaving vault at rest) =="
# trap cleanup handles close + loop detach
echo ""
echo "Vault created: $CONTAINER"
echo "Next: sudo bash scripts/vault_migrate.sh   (moves the real key dirs in)"
