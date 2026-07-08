#!/bin/bash
# RTNode-2400 — One-Step Setup Script (carried by Reticulum-Node-Medic)
#
# This script does everything: installs the tools it needs, downloads the
# firmware, builds it, and flashes it onto your board. You don't need to
# install VS Code or find any port names yourself — this figures it out.
#
# Just double-click this file (or run it in Terminal), and follow the
# on-screen prompts.
#
# NOTE: this is the human-friendly standalone flasher. The tool's Build mode
# (workflows/rtnode_build.py) performs the equivalent programmatically.

set -e
set -o pipefail          # so a failed git step in a pipe isn't silently swallowed

BRANCH="feature/neopixel-status-led"
REPO_URL="https://github.com/5ugAv/RTNode-2400.git"
BUILD_ENV="heltec_V4_boundary-local"
PROJECT_DIR="$HOME/Desktop/RTNode2400"
CLT_WAIT_MAX=60          # ~5 min at 5s/iter before we give up waiting on the popup

echo ""
echo "=================================================="
echo "  RTNode-2400 — One-Step Setup"
echo "=================================================="
echo ""

# ------------------------------------------------
# STEP 0 — Basic checks (works on any Mac, any chip)
# ------------------------------------------------

echo "Checking your computer is ready..."
echo ""

if ! xcode-select -p >/dev/null 2>&1; then
    echo "First-time setup: installing a one-time Apple tool called 'Command Line Tools'."
    echo "A small window will pop up — click 'Install' and agree to the terms."
    echo "This can take several minutes. This script will wait and then keep going."
    echo ""
    xcode-select --install >/dev/null 2>&1 || true
    # Wait for the tools, but DON'T loop forever if the user cancels the popup.
    waited=0
    while ! xcode-select -p >/dev/null 2>&1; do
        if [ "$waited" -ge "$CLT_WAIT_MAX" ]; then
            echo ""
            echo "Command Line Tools weren't installed (the install may have been"
            echo "cancelled). Please run this script again and click 'Install' when"
            echo "the popup appears."
            exit 1
        fi
        sleep 5
        waited=$((waited + 1))
    done
    echo "Command Line Tools installed. Continuing automatically..."
    echo ""
fi

if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: git still isn't available even after Command Line Tools."
    echo "Please restart Terminal and try running this script again."
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 was not found — this is unusual, it normally comes built in."
    echo "To restore it, run this in Terminal:  xcode-select --install"
    echo "then run this setup script again."
    exit 1
fi

echo "Basic tools OK."
echo ""

# ------------------------------------------------
# STEP 1 — Install PlatformIO (the tool that builds the firmware)
# ------------------------------------------------

if command -v pio >/dev/null 2>&1; then
    echo "PlatformIO already installed."
    PIO="pio"
elif python3 -m platformio --version >/dev/null 2>&1; then
    echo "PlatformIO already installed."
    PIO="python3 -m platformio"
else
    echo "Installing PlatformIO (this only happens once, may take a minute)..."
    python3 -m pip install --user --upgrade platformio --break-system-packages 2>/dev/null \
        || python3 -m pip install --user --upgrade platformio
    echo "PlatformIO installed."
    # Use the module form after a fresh install — the 'pio' command may not be
    # on PATH yet, but 'python3 -m platformio' always works in the same Python.
    PIO="python3 -m platformio"
fi
echo ""

# ------------------------------------------------
# STEP 2 — Get the firmware code (download once, or refresh if already there)
# ------------------------------------------------

if [ -d "$PROJECT_DIR" ]; then
    echo "Found an existing copy of the code at $PROJECT_DIR"
    echo "Refreshing it to the latest version..."
    cd "$PROJECT_DIR"
    # Force the local copy to match the remote branch exactly, so a stray local
    # edit can never cause a silent stale/failed build.
    git fetch origin "$BRANCH"
    git checkout -f "$BRANCH"
    git reset --hard "origin/$BRANCH"
else
    echo "Downloading the firmware code to your Desktop..."
    git clone -b "$BRANCH" "$REPO_URL" "$PROJECT_DIR"
    cd "$PROJECT_DIR"
fi
echo ""

# ------------------------------------------------
# STEP 3 — Build the firmware
# ------------------------------------------------

echo "Building the firmware (this can take a minute or two the first time)..."
echo ""
$PIO run -e "$BUILD_ENV"
echo ""
echo "Build complete."
echo ""

# ------------------------------------------------
# STEP 4 — Find the board automatically (Intel or Apple Silicon)
# ------------------------------------------------

find_port() {
    # Match only real USB-serial / ESP32 board ports. Matching by name pattern
    # (rather than excluding "Bluetooth") means a paired Bluetooth device — which
    # also appears under /dev/cu.* — can never be picked by mistake. Heltec V4
    # enumerates as usbmodem*; other boards use usbserial / wchusbserial /
    # SLAB_USBtoUART.
    ls /dev/cu.usbmodem* /dev/cu.usbserial* /dev/cu.wchusbserial* /dev/cu.SLAB_USBtoUART* 2>/dev/null | head -1
}

echo "=================================================="
echo "  Connect your Heltec V4 board now"
echo "=================================================="
echo ""
read -p "Plug the board into this computer with a USB-C cable, then press ENTER... "

PORT=""
for i in 1 2 3 4 5; do
    PORT=$(find_port)
    if [ -n "$PORT" ]; then
        break
    fi
    echo "Still looking for the board... (waiting a moment)"
    sleep 2
done

if [ -z "$PORT" ]; then
    echo ""
    echo "Couldn't find the board automatically."
    echo "Try a different USB cable (some are power-only) and run this"
    echo "script again."
    exit 1
fi

echo "Found board on: $PORT"
echo ""

# ------------------------------------------------
# STEP 5 — Flash the firmware
# ------------------------------------------------

echo "Flashing the firmware onto the board..."
echo ""
$PIO run -e "$BUILD_ENV" -t upload --upload-port "$PORT"
echo ""

echo "=================================================="
echo "  Done! Your board is flashed and ready."
echo "=================================================="
echo ""
echo "Next: on your phone or laptop, connect to the WiFi network called"
echo "'RTNode-Setup' and go to http://10.0.0.1 in a browser to finish"
echo "setting up WiFi and LoRa — see the guide document for exactly what"
echo "to enter there."
echo ""
echo "If you've wired up the RGB status LED (DIN -> GPIO47, VCC -> 3V3,"
echo "GND -> GND), it should now be lighting up as the board works."
echo ""
