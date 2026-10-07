#!/usr/bin/env bash
# Node Medic — roll back the scoped sudoers to the pre-apply state.
# HUMAN-RUN ON THE MEDIC:
#   sudo bash provisioning/security/rollback_sudoers.sh [--user NAME] [BACKUP_DIR]
#
# With a BACKUP_DIR (printed by apply_sudoers.sh, e.g. /root/nodemedic-sudoers-
# backup-YYYYmmdd-HHMMSS-PID) it restores /etc/sudoers.d exactly as it was. With
# no argument it uses the newest such backup. As a last resort (no backup found)
# it re-grants the original blanket rule to NAME (default nodemedic — the
# original medic's account; a clone's is pi) so you are not locked out.
# A pending self-revert (apply_sudoers.sh --self-revert) is cancelled first:
# this IS the revert.
set -euo pipefail

NM_USER="nodemedic"
while [ $# -gt 0 ]; do
    case "$1" in
        --user) NM_USER="${2:?--user needs a name}"; shift 2;;
        --) shift; break;;
        -*) echo "unknown option: $1" >&2; exit 1;;
        *) break;;
    esac
done
case "$NM_USER" in
    ""|-*|[0123456789]*|*[!abcdefghijklmnopqrstuvwxyz0123456789_-]*)
        echo "refusing an odd user name: $NM_USER" >&2; exit 1;;
esac

[ "$(id -u)" = 0 ] || { echo "run with sudo/root" >&2; exit 1; }

BACKUP="${1:-$(ls -1dt /root/nodemedic-sudoers-backup-* 2>/dev/null | head -1 || true)}"
DST="/etc/sudoers.d/010-nodemedic"

# Cancel any pending self-revert (we are reverting now anyway). Both the deadline
# TIMER and the boot-time SERVICE must go, or the enabled service would restore
# a stale backup on some later boot.
systemctl disable --now nodemedic-sudo-revert.timer 2>/dev/null || true
systemctl disable --now nodemedic-sudo-revert.service 2>/dev/null || true
systemctl reset-failed nodemedic-sudo-revert.timer nodemedic-sudo-revert.service 2>/dev/null || true
rm -f /etc/systemd/system/nodemedic-sudo-revert.timer \
      /etc/systemd/system/nodemedic-sudo-revert.service \
      /usr/local/sbin/nodemedic-sudo-revert
systemctl daemon-reload 2>/dev/null || true

if [ -n "$BACKUP" ] && [ -d "$BACKUP" ]; then
    echo "restoring /etc/sudoers.d from $BACKUP"
    rm -f "$DST"
    cp -a "$BACKUP/." /etc/sudoers.d/ 2>/dev/null || true
    # Drop the record files the backup dir also holds, if they landed in sudoers.d.
    rm -f /etc/sudoers.d/sudo-l.before.txt /etc/sudoers.d/sudo-l.after.txt 2>/dev/null || true
else
    echo "no backup dir found — re-granting the original blanket rule to $NM_USER as a fallback"
    TMP="$(mktemp)"; printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$NM_USER" > "$TMP"; chmod 0440 "$TMP"
    visudo -cf "$TMP" && install -o root -g root -m 0440 "$TMP" "/etc/sudoers.d/010-${NM_USER}-nopasswd"
    rm -f "$TMP" "$DST"
fi

visudo -c >/dev/null && echo "rollback OK, sudoers valid" || {
    echo "sudoers INVALID after rollback — fix via recovery console" >&2; exit 1; }
