#!/usr/bin/env bash
# Node Medic — enable key-only SSH, WITHOUT locking yourself out.
# HUMAN-RUN ON THE MEDIC (keep your current SSH session OPEN the whole time):
#
#   sudo bash provisioning/security/apply_sshd.sh          # apply + arm self-revert
#   sudo bash provisioning/security/apply_sshd.sh confirm  # after a NEW key login works
#
# Anti-lockout design:
#   * REFUSE to proceed unless ~nodemedic/.ssh/authorized_keys exists AND a fresh
#     key-only login (ssh -o BatchMode=yes ... true) actually succeeds.
#   * validate with `sshd -t`, then confirm `sshd -T` reports passwordauth = no.
#   * `reload` (not restart) sshd so THIS session is never dropped.
#   * arm a self-revert: in REVERT_MIN minutes the drop-in is removed + sshd
#     reloaded UNLESS you have run `... confirm`. So a mistake fixes itself.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/sshd_config.d/01-nodemedic-hardening.conf"
DST="/etc/ssh/sshd_config.d/01-nodemedic-hardening.conf"
USER_NAME="${SUDO_USER:-nodemedic}"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"
AK="$USER_HOME/.ssh/authorized_keys"
CONFIRM_SENTINEL="/run/nodemedic-ssh-confirmed"
REVERT_MIN="${REVERT_MIN:-10}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="/root/nodemedic-sshd-backup-$STAMP"

[ "$(id -u)" = 0 ] || { echo "run with sudo/root" >&2; exit 1; }

# ---- confirm subcommand: cancel the pending self-revert --------------------
if [ "${1:-}" = "confirm" ]; then
    touch "$CONFIRM_SENTINEL"
    systemctl stop nodemedic-ssh-revert.timer 2>/dev/null || true
    systemctl reset-failed nodemedic-ssh-revert.timer 2>/dev/null || true
    echo "Confirmed. Self-revert cancelled — key-only SSH is now permanent."
    echo "(Optional) make the firewall persistent too: apply_firewall.sh confirm"
    exit 0
fi

echo "== pre-flight: key auth MUST work before we disable passwords =="
[ -f "$SRC" ] || { echo "drop-in source missing: $SRC" >&2; exit 1; }
[ -s "$AK" ]  || { echo "REFUSING: $AK is missing/empty — install a key first." >&2; exit 1; }
echo "   authorized_keys present ($(wc -l <"$AK") key line(s))"

# Prove key-only auth succeeds RIGHT NOW (BatchMode disables any password prompt,
# so success means a key was accepted). Loopback keeps it local + fast.
if sudo -u "$USER_NAME" ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
        -o ConnectTimeout=8 "$USER_NAME@localhost" true 2>/dev/null; then
    echo "   key-only login to localhost SUCCEEDED"
else
    echo "REFUSING: key-only ssh to localhost failed — fix key auth before locking" >&2
    echo "  test manually:  ssh -o BatchMode=yes $USER_NAME@localhost true" >&2
    exit 1
fi

echo "== back up current sshd config to $BACKUP =="
mkdir -p "$BACKUP"; chmod 700 "$BACKUP"
cp -a /etc/ssh/sshd_config "$BACKUP/" 2>/dev/null || true
cp -a /etc/ssh/sshd_config.d "$BACKUP/" 2>/dev/null || true
sshd -T > "$BACKUP/sshd-T.before.txt" 2>/dev/null || true

echo "== install drop-in + validate =="
install -o root -g root -m 0644 "$SRC" "$DST"
if ! sshd -t; then
    echo "!! sshd -t FAILED — removing drop-in, no change applied !!" >&2
    rm -f "$DST"; exit 1
fi

# Effective-value check: catches drop-in ORDERING mistakes (cloud-init winning).
EFF="$(sshd -T)"
echo "$EFF" > "$BACKUP/sshd-T.after.txt"
if ! echo "$EFF" | grep -qi '^passwordauthentication no'; then
    echo "!! effective passwordauthentication is NOT 'no' (ordering?) — reverting !!" >&2
    echo "$EFF" | grep -i passwordauth >&2
    rm -f "$DST"; exit 1
fi
echo "   effective: $(echo "$EFF" | grep -iE 'passwordauth|permitroot|kbdinteractive' | tr '\n' ' ')"

echo "== arm self-revert (T-$REVERT_MIN min) then reload sshd =="
rm -f "$CONFIRM_SENTINEL"
# Transient timer: unless you run `... confirm` (which drops the sentinel), the
# rollback runs and re-enables password auth so you can never be permanently out.
systemd-run --unit=nodemedic-ssh-revert --on-active="${REVERT_MIN}min" \
    /usr/bin/env bash -c \
    "[ -e '$CONFIRM_SENTINEL' ] || { rm -f '$DST'; sshd -t && systemctl reload ssh; logger -t nodemedic 'ssh hardening self-reverted (never confirmed)'; }" \
    >/dev/null
systemctl reload ssh

cat <<EOF

Key-only SSH is LIVE but NOT YET permanent.
  * Your current session stays open (sshd was reloaded, not restarted).
  * NOW, from another terminal, open a FRESH session:
        ssh $USER_NAME@$(hostname).local
    It must succeed with your key (no password prompt).
  * If it works:   sudo bash provisioning/security/apply_sshd.sh confirm
  * If it FAILS / you do nothing: password auth auto-restores in $REVERT_MIN min.
Manual rollback anytime:  sudo bash provisioning/security/rollback_sshd.sh
EOF
