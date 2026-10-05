#!/usr/bin/env bash
# Node Medic — install the SCOPED sudoers, replacing blanket NOPASSWD:ALL.
# HUMAN-RUN ON THE MEDIC:   sudo bash provisioning/security/apply_sudoers.sh
#
# Safety model (a malformed sudoers file locks out ALL sudo — physical recovery):
#   1. validate the repo file on a TEMP copy with `visudo -c` BEFORE touching /etc
#   2. back up every existing /etc/sudoers.d file we change
#   3. install the scoped file, then REMOVE any blanket `nodemedic ... NOPASSWD:ALL`
#   4. re-validate the WHOLE ruleset with `visudo -c`; if it fails, auto-restore
#   5. confirm `nodemedic` can no longer run `ALL` but CAN run a whitelisted cmd
set -euo pipefail

SRC_DEFAULT="$(cd "$(dirname "$0")/../.." && pwd)/provisioning/sudoers.d/nodemedic"
SRC="${1:-$SRC_DEFAULT}"
DST="/etc/sudoers.d/010-nodemedic"
BLANKET_GLOB="/etc/sudoers.d/010-nodemedic-nopasswd"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="/root/nodemedic-sudoers-backup-$STAMP"

[ "$(id -u)" = 0 ] || { echo "run with sudo/root" >&2; exit 1; }
[ -f "$SRC" ] || { echo "source not found: $SRC" >&2; exit 1; }

echo "== 1. validate the repo file on a temp copy =="
TMP="$(mktemp)"; trap 'rm -f "$TMP"' EXIT
# THIS machine's backlight device goes into the brightness rule (readiness
# ledger #16): the repo file pins the developer's panel, and a medic with
# another display got a slider that did nothing. Rendered before validation.
bash "$(dirname "$0")/render_sudoers.sh" "$SRC" "$TMP"
chmod 0440 "$TMP"
visudo -cf "$TMP" || { echo "REPO FILE INVALID — nothing changed." >&2; exit 1; }

echo "== 2. back up existing sudoers.d to $BACKUP =="
mkdir -p "$BACKUP"; chmod 700 "$BACKUP"
cp -a /etc/sudoers.d/. "$BACKUP/" 2>/dev/null || true
# Snapshot what nodemedic can do now (for the record / rollback comparison).
sudo -l -U nodemedic > "$BACKUP/sudo-l.before.txt" 2>&1 || true

echo "== 3. install scoped file + remove blanket NOPASSWD:ALL =="
install -o root -g root -m 0440 "$TMP" "$DST"
# Remove the known blanket file and ANY other sudoers.d file granting
# nodemedic unrestricted NOPASSWD:ALL (so no leftover keeps root wide open).
for f in $BLANKET_GLOB; do [ -e "$f" ] && rm -f "$f"; done
for f in /etc/sudoers.d/*; do
    [ "$f" = "$DST" ] && continue
    [ -f "$f" ] || continue
    if grep -Eq '^[[:space:]]*nodemedic[[:space:]]+ALL=\(ALL\)[[:space:]]+NOPASSWD:[[:space:]]*ALL' "$f"; then
        echo "   removing blanket grant in $f"
        rm -f "$f"
    fi
done

echo "== 4. re-validate the WHOLE ruleset =="
if ! visudo -c >/dev/null; then
    echo "!! /etc/sudoers now INVALID — auto-restoring backup !!" >&2
    rm -f "$DST"
    cp -a "$BACKUP/." /etc/sudoers.d/ 2>/dev/null || true
    visudo -c >/dev/null && echo "restored OK" || echo "RESTORE ALSO FAILED — fix via recovery console" >&2
    exit 1
fi

echo "== 5. verify the new policy =="
AFTER="$(sudo -l -U nodemedic 2>&1 || true)"
echo "$AFTER" > "$BACKUP/sudo-l.after.txt"
if echo "$AFTER" | grep -Eq 'NOPASSWD:[[:space:]]*ALL[[:space:]]*$'; then
    echo "WARNING: nodemedic can STILL run ALL — a blanket grant remains somewhere:" >&2
    grep -rl 'NOPASSWD:.*ALL' /etc/sudoers /etc/sudoers.d/ 2>/dev/null >&2 || true
    exit 1
fi
if ! echo "$AFTER" | grep -q '/usr/bin/tee /sys/class/backlight'; then
    echo "WARNING: whitelisted backlight command not present in nodemedic's rules." >&2
    exit 1
fi

echo
echo "OK — scoped sudoers installed at $DST ; blanket NOPASSWD:ALL removed."
echo "Backup + before/after sudo -l saved in $BACKUP"
echo "Rollback:  sudo bash provisioning/security/rollback_sudoers.sh $BACKUP"
