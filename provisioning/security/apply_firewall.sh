#!/usr/bin/env bash
# Node Medic — restrict SSH exposure with nftables, WITHOUT locking yourself out.
# HUMAN-RUN ON THE MEDIC (keep your SSH session open):
#
#   sudo bash provisioning/security/apply_firewall.sh          # load + arm self-revert
#   sudo bash provisioning/security/apply_firewall.sh confirm  # after a NEW login works
#
# Option (before `confirm`): --user NAME — the account named in the "log in
# again" hint (default: your login name, else nodemedic). The rules themselves
# name no user.
#
# Loads ONLY the nodemedic_ssh table (see nftables/nodemedic-ssh.nft) — it never
# touches any other firewall rules. Because the ruleset ACCEPTS established/related
# first, your current session survives the load. A self-revert (flush our table)
# fires in REVERT_MIN minutes unless you `... confirm`. `confirm` also persists it.
set -euo pipefail

NM_USER=""
while [ $# -gt 0 ]; do
    case "$1" in
        --user) NM_USER="${2:?--user needs a name}"; shift 2;;
        --) shift; break;;
        -*) echo "unknown option: $1" >&2; exit 1;;
        *) break;;
    esac
done
case "$NM_USER" in
    -*|[0123456789]*|*[!abcdefghijklmnopqrstuvwxyz0123456789_-]*)
        echo "refusing an odd user name: $NM_USER" >&2; exit 1;;
esac

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/nftables/nodemedic-ssh.nft"
PERSIST="/etc/nftables.d/nodemedic-ssh.nft"
CONFIRM_SENTINEL="/run/nodemedic-fw-confirmed"
REVERT_MIN="${REVERT_MIN:-10}"

[ "$(id -u)" = 0 ] || { echo "run with sudo/root" >&2; exit 1; }
command -v nft >/dev/null || { echo "nft not installed (apt install nftables)" >&2; exit 1; }
[ -f "$SRC" ] || { echo "ruleset missing: $SRC" >&2; exit 1; }

if [ "${1:-}" = "confirm" ]; then
    touch "$CONFIRM_SENTINEL"
    systemctl stop nodemedic-fw-revert.timer 2>/dev/null || true
    systemctl reset-failed nodemedic-fw-revert.timer 2>/dev/null || true
    echo "== persist the ruleset (survive reboot) =="
    mkdir -p /etc/nftables.d
    install -o root -g root -m 0644 "$SRC" "$PERSIST"
    # Ensure the main nftables config includes /etc/nftables.d and is enabled.
    if ! grep -q '/etc/nftables.d' /etc/nftables.conf 2>/dev/null; then
        echo 'include "/etc/nftables.d/*.nft"' >> /etc/nftables.conf
    fi
    systemctl enable --now nftables.service 2>/dev/null || true
    echo "Confirmed + persisted. SSH is now firewalled to LAN/private sources."
    exit 0
fi

echo "== dry-run the ruleset (syntax) =="
nft -c -f "$SRC"

echo "== load the nodemedic_ssh table (established sessions keep working) =="
nft delete table inet nodemedic_ssh 2>/dev/null || true
nft -f "$SRC"
echo "   loaded. current SSH sources allowed: loopback, RFC1918, 10.55.0.0/29."

echo "== arm self-revert (T-$REVERT_MIN min) =="
rm -f "$CONFIRM_SENTINEL"
# An earlier, unconfirmed apply may still have its revert pending. Its unit name
# is this one's, so systemd-run would refuse and leave THIS load with no revert
# armed at all; stop it first (the table was just reloaded, so re-arming from
# now is exactly right).
systemctl stop nodemedic-fw-revert.timer 2>/dev/null || true
systemctl reset-failed nodemedic-fw-revert.timer nodemedic-fw-revert.service 2>/dev/null || true
systemd-run --unit=nodemedic-fw-revert --on-active="${REVERT_MIN}min" \
    /usr/bin/env bash -c \
    "[ -e '$CONFIRM_SENTINEL' ] || { nft delete table inet nodemedic_ssh 2>/dev/null; logger -t nodemedic 'ssh firewall self-reverted (never confirmed)'; }" \
    >/dev/null

cat <<EOF

Firewall LIVE but NOT persistent yet (gone on reboot / or in $REVERT_MIN min).
  * From another machine ON THE SAME LAN, open a FRESH session:
        ssh ${NM_USER:-$(logname 2>/dev/null || echo nodemedic)}@$(hostname).local
  * If it works:  sudo bash $HERE/apply_firewall.sh confirm
  * If it fails / you do nothing: the rule auto-flushes in $REVERT_MIN min.
Manual rollback:  sudo bash $HERE/rollback_firewall.sh
EOF
