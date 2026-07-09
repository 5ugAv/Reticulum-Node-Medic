#!/bin/bash
echo ""
echo "============================================="
echo "   Heltec Wireless Tracker RNode Installer   "
echo "   Script 2 of 2 — Flash Your Board          "
echo "============================================="
echo ""

# ─── CHECK PREREQUISITES ──────────────────────────────────────────────────────
PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
PYBIN="$HOME/Library/Python/$PYVER/bin"
export PATH="$PATH:$PYBIN"

# One FQBN, used for BOTH compile and upload. (If these differ, arduino-cli
# looks in a different build directory on upload and can't find the binary.)
FQBN="esp32:esp32:esp32s3:CDCOnBoot=cdc"

if ! command -v rnodeconf &>/dev/null; then
    echo "Error: rnodeconf not found."
    echo "Please run Script 1 first to set up your Mac, then try again."
    echo ""
    exit 1
fi

if ! command -v arduino-cli &>/dev/null; then
    echo "Error: arduino-cli not found."
    echo "Please run Script 1 first to set up your Mac, then try again."
    echo ""
    exit 1
fi

if [ ! -d "$HOME/RNode_Firmware" ]; then
    echo "Error: RNode firmware source not found."
    echo "Please run Script 1 first to set up your Mac, then try again."
    echo ""
    exit 1
fi

if ! grep -q "PRODUCT_HELTEC_WIRELESS_TRACKER" "$HOME/RNode_Firmware/Boards.h"; then
    echo "Error: Firmware source has not been patched correctly."
    echo "Please run Script 1 first to set up your Mac, then try again."
    echo ""
    exit 1
fi

# ─── FIND THE BOARD (SAFELY) ──────────────────────────────────────────────────
# This script wipes and re-provisions the selected board's EEPROM. If more than
# one USB board is attached, picking "the first one" could wipe the WRONG board
# (e.g. an RTNode/Heltec node already in service). So: require EXACTLY one.
# Portable (works on macOS bash 3.2 — no mapfile/readarray).
find_boards() { ls /dev/cu.usbmodem* 2>/dev/null; }
count_boards() { find_boards | grep -c '/dev/'; }

echo "---------------------------------------------"
echo "Looking for your board..."
echo "---------------------------------------------"
sleep 2
BOARD_COUNT=$(count_boards)
if [ "$BOARD_COUNT" -eq 0 ]; then
    echo ""
    echo "Error: No board found."
    echo "Please plug your Heltec Wireless Tracker into"
    echo "your Mac via USB-C and try again."
    echo ""
    exit 1
fi
if [ "$BOARD_COUNT" -gt 1 ]; then
    echo ""
    echo "STOP: More than one USB board is connected:"
    find_boards | sed 's/^/   /'
    echo ""
    echo "This script ERASES and re-provisions the selected board's EEPROM."
    echo "To avoid flashing the wrong board, unplug every USB board EXCEPT the"
    echo "Heltec Wireless Tracker you want to flash, then run this script again."
    echo ""
    exit 1
fi
PORT=$(find_boards | head -1)
echo "Board found on port: $PORT"
echo ""

# ─── ANTENNA WARNING ─────────────────────────────────────────────────────────
echo "IMPORTANT: Make sure your 915 MHz LoRa antenna"
echo "is attached to the board before continuing."
echo "Running the board without an antenna can damage it."
echo ""
read -rp "Press Enter once the antenna is attached..."
echo ""

# ─── BOOTLOADER MODE ─────────────────────────────────────────────────────────
echo "============================================="
echo "  Put the board into bootloader mode before flashing."
echo ""
echo "  The Heltec Wireless Tracker has two buttons:"
echo "    USER (also printed PRG)  and  RST."
echo ""
echo "  1. Hold down the USER (PRG) button"
echo "  2. While holding USER, press and release RST once"
echo "  3. Release USER"
echo "     The board is now in bootloader (download) mode."
echo ""
echo "  Note: this board uses the ESP32-S3 native USB (no UART chip), so if"
echo "  flashing fails, unplug it, hold USER, plug back in, then release USER,"
echo "  and try again."
echo "============================================="
echo ""
read -rp "Press Enter once the board is in bootloader mode..."
echo ""

# ─── COMPILE FIRMWARE ────────────────────────────────────────────────────────
echo "---------------------------------------------"
echo "Compiling firmware..."
echo "This will take a minute or two. Please wait."
echo "---------------------------------------------"
echo ""
cd "$HOME/RNode_Firmware" || exit 1
arduino-cli compile \
    --fqbn "$FQBN" \
    -e \
    --build-property "build.partitions=no_ota" \
    --build-property "upload.maximum_size=2097152" \
    --build-property "compiler.cpp.extra_flags=\"-DBOARD_MODEL=0x52\""
COMPILE_RESULT=$?

if [ $COMPILE_RESULT -ne 0 ]; then
    echo ""
    echo "Error: Firmware compilation failed."
    echo "Please run Script 1 again to repair your setup,"
    echo "then try this script again."
    echo ""
    exit 1
fi
echo ""
echo "Firmware compiled successfully."
echo ""

# ─── FLASH FIRMWARE ──────────────────────────────────────────────────────────
echo "---------------------------------------------"
echo "Flashing firmware to board..."
echo "Please do not unplug the board."
echo "---------------------------------------------"
echo ""
arduino-cli upload \
    -p "$PORT" \
    --fqbn "$FQBN" \
    "$HOME/RNode_Firmware"
UPLOAD_RESULT=$?

if [ $UPLOAD_RESULT -ne 0 ]; then
    echo ""
    echo "Error: Flashing failed."
    echo ""
    echo "Please try the following:"
    echo "  1. Unplug the board"
    echo "  2. Wait 5 seconds"
    echo "  3. Plug it back in"
    echo "  4. Put it back into bootloader mode"
    echo "  5. Run this script again"
    echo ""
    exit 1
fi
echo ""
echo "Firmware flashed successfully."
echo ""

# ─── WAIT FOR BOARD TO RESTART ───────────────────────────────────────────────
echo "---------------------------------------------"
echo "Waiting for board to restart..."
echo "---------------------------------------------"
sleep 5
BOARD_COUNT=$(count_boards)
if [ "$BOARD_COUNT" -eq 0 ]; then
    echo ""
    echo "Board not detected after flashing."
    echo "Please unplug and replug the board, then run this script again."
    echo ""
    exit 1
fi
if [ "$BOARD_COUNT" -gt 1 ]; then
    echo ""
    echo "STOP: More than one USB board is now connected. To avoid provisioning"
    echo "the wrong board, unplug the others and re-run this script."
    echo ""
    exit 1
fi
PORT=$(find_boards | head -1)
echo "Board restarted on port: $PORT"
echo ""

# ─── PROVISION EEPROM ────────────────────────────────────────────────────────
echo "---------------------------------------------"
echo "Provisioning board identity..."
echo "---------------------------------------------"
echo ""
if ! "$PYBIN/rnodeconf" "$PORT" --eeprom-wipe; then
    echo ""
    echo "Error: EEPROM wipe failed. Unplug and replug the board, then re-run."
    echo ""
    exit 1
fi
sleep 3
"$PYBIN/rnodeconf" "$PORT" -r --product cb --model ca --platform 0x80 --hwrev 1
PROVISION_RESULT=$?

if [ $PROVISION_RESULT -ne 0 ]; then
    echo ""
    echo "Error: Board provisioning failed."
    echo "Please unplug and replug the board and run this script again."
    echo ""
    exit 1
fi
echo ""

# ─── VERIFY ──────────────────────────────────────────────────────────────────
echo "---------------------------------------------"
echo "Verifying board..."
echo "---------------------------------------------"
echo ""
sleep 3
"$PYBIN/rnodeconf" "$PORT" -i
echo ""

# ─── DONE ────────────────────────────────────────────────────────────────────
echo "============================================="
echo "  Your Heltec Wireless Tracker is ready!"
echo ""
echo "  It is now a fully working RNode on the"
echo "  Reticulum mesh network."
echo ""
echo "  The best way to support this network is"
echo "  to keep your node powered up and running."
echo "  The more nodes that are active, the better"
echo "  the network is for everyone."
echo ""
echo "  Share the guide. Keep your node running."
echo "  Communication for the people, by the people."
echo "============================================="
echo ""
