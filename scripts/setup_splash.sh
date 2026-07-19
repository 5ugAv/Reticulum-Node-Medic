#!/bin/bash
# Branded boot: RNS emblem + filling progress ring (Plymouth), rainbow splash
# off, kernel text silenced. Idempotent; run on the medic:
#   sudo bash scripts/setup_splash.sh
set -e
TOOL=/home/nodemedic/reticulum-tool
THEME=/usr/share/plymouth/themes/nodemedic
CONFIG=/boot/firmware/config.txt
CMDLINE=/boot/firmware/cmdline.txt

echo "== splash assets =="
apt-get install -y -qq python3-pil plymouth plymouth-themes >/dev/null 2>&1 || true
mkdir -p "$THEME"
(cd "$TOOL" && python3 scripts/make_splash.py "$THEME")

echo "== plymouth theme =="
cp "$TOOL/scripts/nodemedic.script" "$THEME/nodemedic.script"
cat > "$THEME/nodemedic.plymouth" <<'EOF'
[Plymouth Theme]
Name=Node Medic
Description=RNS emblem with boot-progress ring
ModuleName=script

[script]
ImageDir=/usr/share/plymouth/themes/nodemedic
ScriptFile=/usr/share/plymouth/themes/nodemedic/nodemedic.script
EOF
plymouth-set-default-theme nodemedic -R
echo "theme set (initramfs rebuilt)"

echo "== firmware rainbow off =="
grep -q "^disable_splash=1" "$CONFIG" || echo "disable_splash=1" >> "$CONFIG"

echo "== silent kernel (cmdline is ONE line - append tokens only) =="
cp "$CMDLINE" "$CMDLINE.pre-splash.bak"
for tok in quiet splash plymouth.ignore-serial-consoles logo.nologo loglevel=3; do
    grep -qw "$tok" "$CMDLINE" || sed -i "s/$/ $tok/" "$CMDLINE"
done
echo "cmdline: $(cat "$CMDLINE")"

echo ""
echo "Done. Reboot to see it:  sudo reboot"
