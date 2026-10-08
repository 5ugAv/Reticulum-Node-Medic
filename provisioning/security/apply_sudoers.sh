#!/usr/bin/env bash
# Node Medic — install the SCOPED sudoers, replacing blanket NOPASSWD:ALL.
# HUMAN-RUN ON THE MEDIC:   sudo bash provisioning/security/apply_sudoers.sh
#
# A CLONE is scoped by its parent's clone flow, through apply_all.sh:
#   apply_sudoers.sh --user pi --self-revert 20 <rendered-source>
#   apply_sudoers.sh --user pi confirm
#
# Options (before the optional SRC):
#   --user NAME        the app account to scope. Default nodemedic (the original
#                      medic); a clone's account is pi.
#   --self-revert MIN  arm a REBOOT-DURABLE self-revert: unless `confirm` runs,
#                      or ~NAME/.nodemedic-sudo-confirmed appears, within MIN
#                      minutes (and at every boot until then) /etc/sudoers.d is
#                      restored from this run's backup. OFF unless asked for, so
#                      a human re-run behaves exactly as it always has.
#
# Safety model (a malformed sudoers file locks out ALL sudo — physical recovery):
#   1. validate the repo file on a TEMP copy with `visudo -c` BEFORE touching /etc
#   2. back up every existing /etc/sudoers.d file we change
#   3. install the scoped file, then REMOVE any blanket `<user> ... NOPASSWD:ALL`:
#      the original medic's 010-nodemedic-nopasswd, Raspberry Pi OS's per-user
#      010_<user>-nopasswd (the card bake writes one for a clone's pi), and any
#      other file whose rule grants this user unrestricted NOPASSWD
#      (cloud-init's 90-cloud-init-users, a hand-made file)
#   4. re-validate the WHOLE ruleset with `visudo -c`; if it fails, auto-restore
#   5. confirm the user can no longer run `ALL` but CAN run a whitelisted cmd
#   6. (--self-revert only) arm the timed, reboot-durable restore
set -euo pipefail

NM_USER="nodemedic"
REVERT_MIN=""
while [ $# -gt 0 ]; do
    case "$1" in
        --user) NM_USER="${2:?--user needs a name}"; shift 2;;
        --self-revert) REVERT_MIN="${2:?--self-revert needs minutes}"; shift 2;;
        --) shift; break;;
        -*) echo "unknown option: $1" >&2; exit 1;;
        *) break;;
    esac
done
# A login name: lower case, digits, _ and -, not starting with a digit or -.
# (The letters are spelled out: a range like a-z follows the locale's
# collation in some shells and lets capitals through.)
case "$NM_USER" in
    ""|-*|[0123456789]*|*[!abcdefghijklmnopqrstuvwxyz0123456789_-]*)
        echo "refusing an odd user name: $NM_USER" >&2; exit 1;;
esac
[ "${#NM_USER}" -le 32 ] || { echo "refusing an over-long user name" >&2; exit 1; }
if [ -n "$REVERT_MIN" ]; then
    case "$REVERT_MIN" in
        *[!0-9]*) echo "--self-revert takes whole minutes" >&2; exit 1;;
    esac
    [ "$REVERT_MIN" -ge 1 ] || { echo "--self-revert needs at least 1 minute" >&2; exit 1; }
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
DST="/etc/sudoers.d/010-nodemedic"
REVERT_HELPER="/usr/local/sbin/nodemedic-sudo-revert"

[ "$(id -u)" = 0 ] || { echo "run with sudo/root" >&2; exit 1; }
USER_HOME="$(getent passwd "$NM_USER" | cut -d: -f6)"
[ -n "$USER_HOME" ] || { echo "no such user: $NM_USER" >&2; exit 1; }
# Where the (scoped) user can confirm WITHOUT sudo — a sentinel in /run or any
# root-only place cannot be created once the scoping is in force (the lesson of
# the original medic's own hand-made sudo self-revert, 2026-07-26).
CONFIRM_SENTINEL="$USER_HOME/.nodemedic-sudo-confirmed"

# ---- confirm subcommand: cancel a pending self-revert ----------------------
if [ "${1:-}" = "confirm" ]; then
    install -o "$NM_USER" -g "$(id -gn "$NM_USER")" -m 0644 /dev/null "$CONFIRM_SENTINEL"
    systemctl disable --now nodemedic-sudo-revert.timer 2>/dev/null || true
    systemctl disable --now nodemedic-sudo-revert.service 2>/dev/null || true
    systemctl reset-failed nodemedic-sudo-revert.timer nodemedic-sudo-revert.service 2>/dev/null || true
    rm -f /etc/systemd/system/nodemedic-sudo-revert.timer \
          /etc/systemd/system/nodemedic-sudo-revert.service "$REVERT_HELPER"
    systemctl daemon-reload 2>/dev/null || true
    echo "Confirmed. Self-revert cancelled — the scoped sudoers for $NM_USER is permanent."
    exit 0
fi

SRC_DEFAULT="$(cd "$HERE/../.." && pwd)/provisioning/sudoers.d/nodemedic"
SRC="${1:-$SRC_DEFAULT}"
# The PID keeps two runs in one second from sharing (and overwriting) a backup.
STAMP="$(date +%Y%m%d-%H%M%S)-$$"
BACKUP="/root/nodemedic-sudoers-backup-$STAMP"

[ -f "$SRC" ] || { echo "source not found: $SRC" >&2; exit 1; }

echo "== 1. validate the repo file on a temp copy (for $NM_USER) =="
TMP="$(mktemp)"; trap 'rm -f "$TMP"' EXIT
# THIS machine's backlight device goes into the brightness rule (readiness
# ledger #16): the repo file pins the developer's panel, and a medic with
# another display got a slider that did nothing. The app user is rendered in
# the same pass (the source names nodemedic; a clone runs as pi). The empty
# third argument means "this machine's backlight". Rendered before validation.
bash "$HERE/render_sudoers.sh" "$SRC" "$TMP" "" "$NM_USER"
chmod 0440 "$TMP"
visudo -cf "$TMP" || { echo "REPO FILE INVALID — nothing changed." >&2; exit 1; }

echo "== 2. back up existing sudoers.d to $BACKUP =="
mkdir -p "$BACKUP"; chmod 700 "$BACKUP"
cp -a /etc/sudoers.d/. "$BACKUP/" 2>/dev/null || true
# For apply_all.sh, which rolls back to exactly this backup.
echo "BACKUP_DIR=$BACKUP"
# Snapshot what the user can do now (for the record / rollback comparison).
sudo -l -U "$NM_USER" > "$BACKUP/sudo-l.before.txt" 2>&1 || true

echo "== 3. install scoped file + remove blanket NOPASSWD:ALL =="
install -o root -g root -m 0440 "$TMP" "$DST"
# The known blanket files by name: the original medic's hyphen form, and the
# underscore form Raspberry Pi OS (and the Node Medic card bake) writes.
for f in "/etc/sudoers.d/010-${NM_USER}-nopasswd" "/etc/sudoers.d/010_${NM_USER}-nopasswd"; do
    if [ -e "$f" ]; then
        echo "   removing blanket grant file $f"
        rm -f "$f"
    fi
done
# ...and ANY other sudoers.d file granting this user unrestricted NOPASSWD:ALL
# (so no leftover keeps root wide open) — with or without the (ALL:ALL) runas
# form and the spaces people put around '=' and ':'. "ALL" must end the rule
# (or be followed by a comma or a comment), so an alias such as ALLOWED_CMDS
# is not mistaken for it.
BLANKET_RE="^[[:space:]]*${NM_USER}[[:space:]]+ALL[[:space:]]*=[[:space:]]*\\(ALL([[:space:]]*:[[:space:]]*ALL)?\\)[[:space:]]+NOPASSWD:[[:space:]]*ALL([[:space:]]*[,#]|[[:space:]]*\$)"
for f in /etc/sudoers.d/*; do
    [ "$f" = "$DST" ] && continue
    [ -f "$f" ] || continue
    if grep -Eq "$BLANKET_RE" "$f"; then
        echo "   removing blanket grant in $f"
        rm -f "$f"
    fi
done

echo "== 4. re-validate the WHOLE ruleset =="
if ! visudo -c >/dev/null; then
    echo "!! /etc/sudoers now INVALID — auto-restoring backup !!" >&2
    rm -f "$DST"
    cp -a "$BACKUP/." /etc/sudoers.d/ 2>/dev/null || true
    rm -f /etc/sudoers.d/sudo-l.before.txt /etc/sudoers.d/sudo-l.after.txt 2>/dev/null || true
    visudo -c >/dev/null && echo "restored OK" || echo "RESTORE ALSO FAILED — fix via recovery console" >&2
    exit 1
fi

echo "== 5. verify the new policy =="
AFTER="$(sudo -l -U "$NM_USER" 2>&1 || true)"
echo "$AFTER" > "$BACKUP/sudo-l.after.txt"
if echo "$AFTER" | grep -Eq 'NOPASSWD:[[:space:]]*ALL[[:space:]]*$'; then
    echo "WARNING: $NM_USER can STILL run ALL — a blanket grant remains somewhere:" >&2
    grep -rl 'NOPASSWD:.*ALL' /etc/sudoers /etc/sudoers.d/ 2>/dev/null >&2 || true
    exit 1
fi
if ! echo "$AFTER" | grep -q '/usr/bin/tee /sys/class/backlight'; then
    echo "WARNING: whitelisted backlight command not present in $NM_USER's rules." >&2
    exit 1
fi

if [ -n "$REVERT_MIN" ]; then
    echo "== 6. arm self-revert (T-$REVERT_MIN min, and again at every boot) =="
    rm -f "$CONFIRM_SENTINEL"
    # REBOOT-DURABLE, like apply_sshd.sh's: a transient unit dies with a power
    # cut while the scoped file in /etc survives, which would leave an
    # unconfirmed scoping in force with no way back. A real service that runs
    # at every boot AND on the deadline timer, until confirmed.
    cat > "$REVERT_HELPER" <<EOF
#!/bin/bash
# Installed by apply_sudoers.sh --self-revert. Restores /etc/sudoers.d from the
# pre-scoping backup unless the scoping was confirmed.
if [ -e "$CONFIRM_SENTINEL" ]; then
    logger -t nodemedic 'sudo scoping confirmed — self-revert standing down'
else
    rm -f "$DST"
    cp -a "$BACKUP/." /etc/sudoers.d/ 2>/dev/null || true
    rm -f /etc/sudoers.d/sudo-l.before.txt /etc/sudoers.d/sudo-l.after.txt
    if visudo -c >/dev/null 2>&1; then
        logger -t nodemedic 'sudo scoping self-reverted (never confirmed)'
    else
        logger -t nodemedic 'sudo self-revert: sudoers INVALID after restore — needs the recovery console'
    fi
fi
# Either way this check is done: disarm so it cannot fire again.
systemctl disable --now nodemedic-sudo-revert.timer 2>/dev/null || true
systemctl disable nodemedic-sudo-revert.service 2>/dev/null || true
EOF
    chmod 0755 "$REVERT_HELPER"

    cat > /etc/systemd/system/nodemedic-sudo-revert.service <<EOF
[Unit]
Description=Node Medic: restore the pre-scoping sudoers unless the scoping was confirmed
[Service]
Type=oneshot
ExecStart=$REVERT_HELPER
[Install]
WantedBy=multi-user.target
EOF

    cat > /etc/systemd/system/nodemedic-sudo-revert.timer <<EOF
[Unit]
Description=Node Medic: sudo scoping self-revert deadline (T-${REVERT_MIN}min)
[Timer]
OnActiveSec=${REVERT_MIN}min
AccuracySec=5s
Unit=nodemedic-sudo-revert.service
[Install]
WantedBy=timers.target
EOF

    systemctl daemon-reload
    systemctl enable nodemedic-sudo-revert.service >/dev/null 2>&1
    systemctl enable --now nodemedic-sudo-revert.timer >/dev/null 2>&1
fi

echo
echo "OK — scoped sudoers installed at $DST for $NM_USER ; blanket NOPASSWD:ALL removed."
echo "Backup + before/after sudo -l saved in $BACKUP"
if [ -n "$REVERT_MIN" ]; then
    echo "NOT YET permanent: restored in $REVERT_MIN min (and at every boot) unless confirmed:"
    echo "  sudo bash $HERE/apply_sudoers.sh --user $NM_USER confirm"
    echo "  (or, once scoped, as $NM_USER without sudo:  touch $CONFIRM_SENTINEL)"
fi
echo "Rollback:  sudo bash $HERE/rollback_sudoers.sh --user $NM_USER $BACKUP"
