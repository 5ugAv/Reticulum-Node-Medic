#!/usr/bin/env bash
# Node Medic — undo the SSH hardening (re-enable password auth).
# HUMAN-RUN ON THE MEDIC:  sudo bash provisioning/security/rollback_sshd.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo/root" >&2; exit 1; }

DST="/etc/ssh/sshd_config.d/01-nodemedic-hardening.conf"

# Cancel any pending self-revert (we're reverting now anyway). Both the deadline
# TIMER and the boot-time SERVICE must be disabled + removed, or the enabled
# service would keep firing on future boots.
systemctl disable --now nodemedic-ssh-revert.timer 2>/dev/null || true
systemctl disable --now nodemedic-ssh-revert.service 2>/dev/null || true
systemctl reset-failed nodemedic-ssh-revert.timer nodemedic-ssh-revert.service 2>/dev/null || true
rm -f /etc/systemd/system/nodemedic-ssh-revert.timer \
      /etc/systemd/system/nodemedic-ssh-revert.service \
      /usr/local/sbin/nodemedic-ssh-revert
systemctl daemon-reload 2>/dev/null || true

rm -f "$DST"
if sshd -t; then
    systemctl reload ssh
    echo "rollback OK — drop-in removed, sshd reloaded."
    echo "effective now: $(sshd -T | grep -i passwordauthentication)"
else
    echo "sshd -t failed after removing drop-in — investigate /etc/ssh before reload" >&2
    exit 1
fi
