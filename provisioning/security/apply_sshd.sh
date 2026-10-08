#!/usr/bin/env bash
# Node Medic — enable key-only SSH, WITHOUT locking yourself out.
# HUMAN-RUN ON THE MEDIC (keep your current SSH session OPEN the whole time):
#
#   sudo bash provisioning/security/apply_sshd.sh          # apply + arm self-revert
#   sudo bash provisioning/security/apply_sshd.sh confirm  # after a NEW key login works
#
# Options (before `confirm`):
#   --user NAME   the account whose key must work. Default: the sudo caller, else
#                 nodemedic (the original medic). A clone's account is pi.
#   --caller-proved-key-login
#                 skip the loopback login test because the CALLER has just
#                 logged in with a key, non-interactively, from outside — the
#                 parent medic's clone flow (apply_all.sh), where every command
#                 arrives over a fresh `ssh -o BatchMode=yes` login. A clone's
#                 own key is not in its own authorized_keys, so the loopback
#                 test cannot pass there; the parent's login proves more (the
#                 real road works). authorized_keys must still exist and hold
#                 a key sshd can parse. Not for use by hand.
#
# Anti-lockout design:
#   * REFUSE to proceed unless ~USER/.ssh/authorized_keys exists AND a fresh
#     key-only login (ssh -o BatchMode=yes ... true) actually succeeds
#     (or the caller proved one, above).
#   * validate with `sshd -t`, then confirm `sshd -T` reports passwordauth = no.
#   * `reload` (not restart) sshd so THIS session is never dropped.
#   * arm a self-revert: in REVERT_MIN minutes the drop-in is removed + sshd
#     reloaded UNLESS you have run `... confirm`. So a mistake fixes itself.
set -euo pipefail

USER_NAME="${SUDO_USER:-nodemedic}"
CALLER_PROVED=0
while [ $# -gt 0 ]; do
    case "$1" in
        --user) USER_NAME="${2:?--user needs a name}"; shift 2;;
        --caller-proved-key-login) CALLER_PROVED=1; shift;;
        --) shift; break;;
        -*) echo "unknown option: $1" >&2; exit 1;;
        *) break;;
    esac
done
# A login name: lower case, digits, _ and -, not starting with a digit or -.
case "$USER_NAME" in
    ""|-*|[0123456789]*|*[!abcdefghijklmnopqrstuvwxyz0123456789_-]*)
        echo "refusing an odd user name: $USER_NAME" >&2; exit 1;;
esac

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/sshd_config.d/01-nodemedic-hardening.conf"
DST="/etc/ssh/sshd_config.d/01-nodemedic-hardening.conf"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"
[ -n "$USER_HOME" ] || { echo "no such user: $USER_NAME" >&2; exit 1; }
AK="$USER_HOME/.ssh/authorized_keys"
# The sentinel MUST survive a reboot and MUST be writable by the (possibly
# sudo-scoped) operator — /run is tmpfs AND root-only, so a reboot inside the
# window used to strand the hardening permanently with no revert armed. Same
# lesson as the sudo self-revert (~/.sudo-scope-confirmed).
CONFIRM_SENTINEL="$USER_HOME/.nodemedic-ssh-confirmed"
REVERT_HELPER="/usr/local/sbin/nodemedic-ssh-revert"
REVERT_MIN="${REVERT_MIN:-10}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="/root/nodemedic-sshd-backup-$STAMP"

[ "$(id -u)" = 0 ] || { echo "run with sudo/root" >&2; exit 1; }

# ---- confirm subcommand: cancel the pending self-revert --------------------
if [ "${1:-}" = "confirm" ]; then
    install -o "$USER_NAME" -g "$(id -gn "$USER_NAME")" -m 0644 /dev/null "$CONFIRM_SENTINEL"
    # Disable BOTH the deadline timer and the boot-time check, then clean up.
    systemctl disable --now nodemedic-ssh-revert.timer 2>/dev/null || true
    systemctl disable --now nodemedic-ssh-revert.service 2>/dev/null || true
    systemctl reset-failed nodemedic-ssh-revert.timer nodemedic-ssh-revert.service 2>/dev/null || true
    rm -f /etc/systemd/system/nodemedic-ssh-revert.timer \
          /etc/systemd/system/nodemedic-ssh-revert.service "$REVERT_HELPER"
    systemctl daemon-reload 2>/dev/null || true
    echo "Confirmed. Self-revert cancelled — key-only SSH is now permanent."
    echo "(Optional) make the firewall persistent too: apply_firewall.sh confirm"
    exit 0
fi

echo "== pre-flight: key auth MUST work before we disable passwords =="
[ -f "$SRC" ] || { echo "drop-in source missing: $SRC" >&2; exit 1; }
[ -s "$AK" ]  || { echo "REFUSING: $AK is missing/empty — install a key first." >&2; exit 1; }
echo "   authorized_keys present ($(wc -l <"$AK") key line(s))"

if [ "$CALLER_PROVED" = 1 ]; then
    # The caller's own fresh BatchMode login is the proof (see the header).
    # Still refuse a file sshd could not use: ssh-keygen -l lists the keys an
    # authorized_keys holds and fails when there is none it can parse.
    if ! ssh-keygen -l -f "$AK" >/dev/null 2>&1; then
        echo "REFUSING: $AK holds no key sshd can use — install a key first." >&2
        exit 1
    fi
    echo "   key-only login proven by the caller (a fresh BatchMode login from outside)"
# Prove key-only auth succeeds RIGHT NOW (BatchMode disables any password prompt,
# so success means a key was accepted). Loopback keeps it local + fast.
# -n is REQUIRED: without it ssh inherits and consumes this script's stdin, so
# driving apply_sshd.sh from a heredoc/pipe (e.g. a scripted clone bring-up)
# silently eats every remaining command.
elif sudo -u "$USER_NAME" ssh -n -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
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

echo "== arm self-revert (T-$REVERT_MIN min, and again at every boot) =="
rm -f "$CONFIRM_SENTINEL"
# REBOOT-DURABLE self-revert. A transient `systemd-run` unit dies with the
# reboot while the drop-in in /etc survives — so an unconfirmed apply plus a
# power cut (routine on a no-RTC solar node) used to leave key-only SSH
# permanently in force with NO safety net. Instead: a real enabled service that
# runs BOTH on a deadline timer and at every boot, until you confirm.
cat > "$REVERT_HELPER" <<EOF
#!/bin/bash
# Installed by apply_sshd.sh. Reverts the SSH hardening unless confirmed.
if [ -e "$CONFIRM_SENTINEL" ]; then
    logger -t nodemedic 'ssh hardening confirmed — self-revert standing down'
else
    rm -f "$DST"
    if sshd -t; then systemctl reload ssh; fi
    logger -t nodemedic 'ssh hardening self-reverted (never confirmed)'
fi
# Either way this check is done: disarm so it cannot fire again.
systemctl disable --now nodemedic-ssh-revert.timer 2>/dev/null || true
systemctl disable nodemedic-ssh-revert.service 2>/dev/null || true
EOF
chmod 0755 "$REVERT_HELPER"

cat > /etc/systemd/system/nodemedic-ssh-revert.service <<EOF
[Unit]
Description=Node Medic: revert SSH hardening unless the operator confirmed
After=ssh.service network.target
[Service]
Type=oneshot
ExecStart=$REVERT_HELPER
[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/nodemedic-ssh-revert.timer <<EOF
[Unit]
Description=Node Medic: SSH hardening self-revert deadline (T-${REVERT_MIN}min)
[Timer]
OnActiveSec=${REVERT_MIN}min
AccuracySec=5s
Unit=nodemedic-ssh-revert.service
[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
# The SERVICE is enabled (runs at every boot until confirmed) and the TIMER is
# started (the in-session deadline). Either path reverts if unconfirmed.
systemctl enable nodemedic-ssh-revert.service >/dev/null 2>&1
systemctl enable --now nodemedic-ssh-revert.timer >/dev/null 2>&1
systemctl reload ssh

cat <<EOF

Key-only SSH is LIVE but NOT YET permanent.
  * Your current session stays open (sshd was reloaded, not restarted).
  * NOW, from another terminal, open a FRESH session:
        ssh $USER_NAME@$(hostname).local
    It must succeed with your key (no password prompt).
  * If it works:   sudo bash $HERE/apply_sshd.sh --user $USER_NAME confirm
  * If it FAILS / you do nothing: password auth auto-restores in $REVERT_MIN min
    — AND on every reboot until you confirm, so a power cut can't strand you.
Manual rollback anytime:  sudo bash $HERE/rollback_sshd.sh
EOF
