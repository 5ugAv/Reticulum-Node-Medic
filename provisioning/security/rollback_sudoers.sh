#!/usr/bin/env bash
# Node Medic — roll back the scoped sudoers to the pre-apply state.
# HUMAN-RUN ON THE MEDIC:
#   sudo bash provisioning/security/rollback_sudoers.sh [BACKUP_DIR]
#
# With a BACKUP_DIR (printed by apply_sudoers.sh, e.g. /root/nodemedic-sudoers-
# backup-YYYYmmdd-HHMMSS) it restores /etc/sudoers.d exactly as it was. With no
# argument it uses the newest such backup. As a last resort (no backup found) it
# re-grants the original blanket rule so you are not locked out.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo/root" >&2; exit 1; }

BACKUP="${1:-$(ls -1dt /root/nodemedic-sudoers-backup-* 2>/dev/null | head -1 || true)}"
DST="/etc/sudoers.d/010-nodemedic"

if [ -n "$BACKUP" ] && [ -d "$BACKUP" ]; then
    echo "restoring /etc/sudoers.d from $BACKUP"
    rm -f "$DST"
    cp -a "$BACKUP/." /etc/sudoers.d/ 2>/dev/null || true
    # Drop the record files the backup dir also holds, if they landed in sudoers.d.
    rm -f /etc/sudoers.d/sudo-l.before.txt /etc/sudoers.d/sudo-l.after.txt 2>/dev/null || true
else
    echo "no backup dir found — re-granting the original blanket rule as a fallback"
    TMP="$(mktemp)"; printf 'nodemedic ALL=(ALL) NOPASSWD:ALL\n' > "$TMP"; chmod 0440 "$TMP"
    visudo -cf "$TMP" && install -o root -g root -m 0440 "$TMP" /etc/sudoers.d/010-nodemedic-nopasswd
    rm -f "$TMP" "$DST"
fi

visudo -c >/dev/null && echo "rollback OK, sudoers valid" || {
    echo "sudoers INVALID after rollback — fix via recovery console" >&2; exit 1; }
