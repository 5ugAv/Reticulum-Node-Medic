#!/usr/bin/env bash
# Node Medic — apply ALL THREE hardenings under one root-held dead-man switch,
# then confirm them or roll them back on a verdict from OUTSIDE the machine.
#
# This is what a parent medic's clone flow runs on a NEW medic
# (workflows/clone.py, harden_new_medic). It is not for use by hand: the
# original medic was hardened with the three apply scripts one at a time,
# SSH -> firewall -> sudoers, confirming each before the next.
#
# WHY A PROGRAM CANNOT FOLLOW THAT ORDER: confirming the SSH and firewall
# changes needs root, and the scoped sudoers takes root away from the app
# account. Checked from outside in the keeper's order — new key login, a
# whitelisted command runs, a non-whitelisted one is refused — the `confirm`
# commands could never be sent afterwards. So ONE root process, started while
# the account still has full sudo, holds the whole change open:
#
#   1. apply_sshd.sh     key-only SSH; its own reboot-durable self-revert armed
#   2. apply_firewall.sh tcp/22 from private ranges only; self-revert armed
#   3. apply_sudoers.sh  scoped sudo for --user; reboot-durable self-revert armed
#   4. status "applied", then WAIT for the verdict the checker writes after it
#      has looked from outside with NEW logins (plain files the scoped account
#      can make without sudo):
#        ~USER/.nodemedic-harden-confirm   -> the three `confirm`s run
#        ~USER/.nodemedic-harden-rollback  -> the three rollback scripts run
#      No verdict within --window seconds  -> roll back.
#
# A "confirm" can only make the hardening permanent; a "rollback" only does
# early what the deadline does anyway — so neither file is a way in. ANY
# failure on the way (an apply, a confirm) rolls back ALL THREE: the machine
# ends fully hardened or exactly as it was. If this process itself dies (power
# cut), each script's own self-revert fires at the next boot or its deadline;
# those inner timers are set to outlast this window, never to race it.
#
# Progress, for the checker: /run/nodemedic-harden/status (root-owned, world-
# readable), one line:  running | applied | confirmed | rolled-back: <reason>
# Everything the scripts printed: /var/log/nodemedic-harden.log
#
# usage, as root and detached from any login (the checker's SSH session must
# not be what keeps it alive):
#   systemd-run --unit=nodemedic-harden-<n> --collect \
#       /bin/bash <this dir>/apply_all.sh --user pi [--window 600]
# Expects its siblings beside it: apply_{sshd,firewall,sudoers}.sh, their
# rollback_*.sh, render_sudoers.sh, sshd_config.d/, nftables/ and the policy
# as sudoers.nodemedic (or --sudoers PATH) — all root-owned, so nothing run
# here as root can have been edited by the app account.
set -uo pipefail      # deliberately NOT -e: every path must end in a status line

HERE="$(cd "$(dirname "$0")" && pwd)"
NM_USER=""
WINDOW=600
SUDOERS_SRC="$HERE/sudoers.nodemedic"
while [ $# -gt 0 ]; do
    case "$1" in
        --user|--window|--sudoers)
            [ $# -ge 2 ] || { echo "$1 needs a value" >&2; exit 2; }
            case "$1" in
                --user) NM_USER="$2";;
                --window) WINDOW="$2";;
                --sudoers) SUDOERS_SRC="$2";;
            esac
            shift 2;;
        *) echo "unknown argument: $1" >&2; exit 2;;
    esac
done

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }

STATUS_DIR="/run/nodemedic-harden"
LOG="/var/log/nodemedic-harden.log"
install -d -m 0755 -o root -g root "$STATUS_DIR"
touch "$LOG" && chmod 0644 "$LOG"

status() {
    printf '%s\n' "$*" > "$STATUS_DIR/status.new"
    chmod 0644 "$STATUS_DIR/status.new"
    mv -f "$STATUS_DIR/status.new" "$STATUS_DIR/status"
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$LOG"
    logger -t nodemedic "harden: $*" 2>/dev/null || true
}

# One run at a time: a second supervisor would delete the first one's verdict.
exec 9>"$STATUS_DIR/lock"
if ! flock -n 9; then
    echo "another hardening run is in progress" >&2
    exit 1
fi

case "$NM_USER" in
    ""|-*|[0123456789]*|*[!abcdefghijklmnopqrstuvwxyz0123456789_-]*)
        status "rolled-back: refused an odd user name (nothing was changed)"; exit 1;;
esac
case "$WINDOW" in
    ""|*[!0-9]*) status "rolled-back: --window takes whole seconds (nothing was changed)"; exit 1;;
esac
if [ "$WINDOW" -lt 60 ] || [ "$WINDOW" -gt 3600 ]; then
    status "rolled-back: --window must be 60..3600 seconds (nothing was changed)"; exit 1
fi
USER_HOME="$(getent passwd "$NM_USER" | cut -d: -f6)"
if [ -z "$USER_HOME" ] || [ ! -d "$USER_HOME" ]; then
    status "rolled-back: no account called $NM_USER (nothing was changed)"; exit 1
fi
for f in apply_sshd.sh rollback_sshd.sh apply_firewall.sh rollback_firewall.sh \
         apply_sudoers.sh rollback_sudoers.sh render_sudoers.sh \
         sshd_config.d/01-nodemedic-hardening.conf nftables/nodemedic-ssh.nft; do
    if [ ! -f "$HERE/$f" ]; then
        status "rolled-back: $f is missing beside apply_all.sh (nothing was changed)"; exit 1
    fi
done
if [ ! -f "$SUDOERS_SRC" ]; then
    status "rolled-back: the sudo policy is missing (nothing was changed)"; exit 1
fi

CONFIRM="$USER_HOME/.nodemedic-harden-confirm"
ROLLBACK="$USER_HOME/.nodemedic-harden-rollback"
# A verdict left from an earlier run must not decide this one.
rm -f "$CONFIRM" "$ROLLBACK"

# The inner self-reverts must fire only AFTER this process has had its whole
# window to decide — never in the middle of the checker's look.
REVERT_MIN=$(( WINDOW / 60 + 10 ))

ATTEMPTED=""        # which of ssh / fw / sudo may have changed something
SUDO_BACKUP=""      # the backup apply_sudoers.sh made, for an exact rollback
LAST_OUT=""

run_logged() {      # name, command... ; output to the log, kept in LAST_OUT
    local name="$1" rc
    shift
    LAST_OUT="$("$@" 2>&1)"
    rc=$?
    { printf '== %s (exit %s) ==\n' "$name" "$rc"; printf '%s\n' "$LAST_OUT"; } >> "$LOG"
    return "$rc"
}

rollback_all() {
    local reason="$1"
    case " $ATTEMPTED " in
        *" sudo "*)
            # Only a run that got as far as its backup changed anything; one that
            # failed validating the policy left /etc/sudoers.d untouched.
            if [ -n "$SUDO_BACKUP" ]; then
                run_logged rollback_sudoers bash "$HERE/rollback_sudoers.sh" \
                    --user "$NM_USER" "$SUDO_BACKUP"
            fi;;
    esac
    case " $ATTEMPTED " in
        *" fw "*) run_logged rollback_firewall bash "$HERE/rollback_firewall.sh";;
    esac
    case " $ATTEMPTED " in
        *" ssh "*) run_logged rollback_sshd bash "$HERE/rollback_sshd.sh";;
    esac
    rm -f "$CONFIRM" "$ROLLBACK"
    status "rolled-back: $reason"
    exit 1
}

status "running"

ATTEMPTED="ssh"
run_logged apply_sshd env REVERT_MIN="$REVERT_MIN" bash "$HERE/apply_sshd.sh" \
        --user "$NM_USER" --caller-proved-key-login \
    || rollback_all "key-only login did not go in"

ATTEMPTED="ssh fw"
run_logged apply_firewall env REVERT_MIN="$REVERT_MIN" bash "$HERE/apply_firewall.sh" \
        --user "$NM_USER" \
    || rollback_all "the SSH firewall did not go in"

ATTEMPTED="ssh fw sudo"
run_logged apply_sudoers bash "$HERE/apply_sudoers.sh" --user "$NM_USER" \
        --self-revert "$REVERT_MIN" "$SUDOERS_SRC"
rc=$?
SUDO_BACKUP="$(printf '%s\n' "$LAST_OUT" | sed -n 's/^BACKUP_DIR=//p' | tail -n 1)"
[ "$rc" -eq 0 ] || rollback_all "the scoped sudo rules did not go in"

status "applied"

# Count turns rather than read the clock: NTP may step the clock mid-window.
turns=$(( WINDOW / 2 ))
i=0
while :; do
    if [ -e "$ROLLBACK" ]; then
        rollback_all "the checking medic found a problem"
    fi
    if [ -e "$CONFIRM" ]; then
        break
    fi
    i=$(( i + 1 ))
    if [ "$i" -ge "$turns" ]; then
        rollback_all "no word from the checking medic within ${WINDOW}s"
    fi
    sleep 2
done
rm -f "$CONFIRM" "$ROLLBACK"

run_logged confirm_sudoers bash "$HERE/apply_sudoers.sh" --user "$NM_USER" confirm \
    || rollback_all "the scoped sudo rules could not be made permanent"
run_logged confirm_firewall bash "$HERE/apply_firewall.sh" --user "$NM_USER" confirm \
    || rollback_all "the SSH firewall could not be made permanent"
run_logged confirm_sshd bash "$HERE/apply_sshd.sh" --user "$NM_USER" confirm \
    || rollback_all "key-only login could not be made permanent"

status "confirmed"
exit 0
