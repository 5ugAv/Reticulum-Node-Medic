#!/bin/bash
# Make the medic boot straight into working order (task #38):
#   - full USB-A power for the attached RNode (usb_max_current_enable=1)
#   - the serial splitter as a service, ordered BEFORE rnsd
#   - rnsd pointed at the splitter's virtual port (LoRa + GPS on one cable)
#   - the touchscreen UI autostarting into the desktop session
# Idempotent; run on the medic with:  sudo bash scripts/setup_boot.sh
set -e
TOOL=/home/nodemedic/reticulum-tool
CONFIG=/boot/firmware/config.txt

echo "== USB power =="
if ! grep -q "^usb_max_current_enable=1" "$CONFIG"; then
    echo "usb_max_current_enable=1" >> "$CONFIG"
    echo "added usb_max_current_enable=1 (takes effect after reboot)"
else
    echo "already set"
fi

echo "== splitter service =="
# The unit ships with a placeholder, not a serial: the by-id path carries the
# board's MAC (a permanent fingerprint of one device), and a cloned medic has
# different hardware. Resolve THIS medic's radio now, and refuse rather than
# install a unit pointing at a port that does not exist.
if [ -z "$RNODE_BY_ID" ]; then
    RNODE_BY_ID=$(ls /dev/serial/by-id/*Espressif*JTAG*-if00 2>/dev/null | head -1)
fi
if [ -z "$RNODE_BY_ID" ] || [ ! -e "$RNODE_BY_ID" ]; then
    echo "Cannot find the medic's own radio under /dev/serial/by-id."
    echo "Plug it in, or run with:  RNODE_BY_ID=/dev/serial/by-id/... sudo -E bash $0"
    exit 1
fi
echo "splitter will own: $RNODE_BY_ID"
sed "s|__RNODE_BY_ID__|$RNODE_BY_ID|" "$TOOL/scripts/rnode-splitter.service" \
    > /etc/systemd/system/rnode-splitter.service
if grep -q "__RNODE_BY_ID__" /etc/systemd/system/rnode-splitter.service; then
    echo "placeholder was not substituted — refusing to continue"
    exit 1
fi
mkdir -p /etc/systemd/system/rnsd.service.d
cp "$TOOL/scripts/rnsd-splitter-override.conf" /etc/systemd/system/rnsd.service.d/splitter.conf
systemctl daemon-reload
systemctl enable rnode-splitter.service

echo "== rnsd -> virtual port =="
RCONF=/home/nodemedic/.reticulum/config
if ! grep -q "port = /tmp/rnode-jonesey" "$RCONF"; then
    cp "$RCONF" "$RCONF.pre-splitter.bak"
    sed -i "s|^\(\s*port = \).*|\1/tmp/rnode-jonesey|" "$RCONF"
    echo "rnsd port -> /tmp/rnode-jonesey (backup: config.pre-splitter.bak)"
else
    echo "already pointed at the splitter"
fi

echo "== UI autostart =="
install -d -o nodemedic -g nodemedic /home/nodemedic/.config/autostart
install -o nodemedic -g nodemedic -m 644 \
    "$TOOL/scripts/nodemedic-ui.desktop" /home/nodemedic/.config/autostart/
chmod +x "$TOOL/scripts/start_ui.sh"

echo ""
echo "Done. Reboot to bring it all up:  sudo reboot"
