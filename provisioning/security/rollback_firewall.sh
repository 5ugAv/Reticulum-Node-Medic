#!/usr/bin/env bash
# Node Medic — remove the SSH firewall table (and its persistence).
# HUMAN-RUN ON THE MEDIC:  sudo bash provisioning/security/rollback_firewall.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo/root" >&2; exit 1; }

systemctl stop nodemedic-fw-revert.timer 2>/dev/null || true
systemctl reset-failed nodemedic-fw-revert.timer 2>/dev/null || true

nft delete table inet nodemedic_ssh 2>/dev/null && echo "live table removed" || echo "no live table"
rm -f /etc/nftables.d/nodemedic-ssh.nft && echo "persistence removed" || true
echo "rollback OK — SSH exposure restriction lifted."
