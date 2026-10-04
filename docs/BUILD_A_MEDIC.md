# Build a medic — from a blank Raspberry Pi 5 to a running Node Medic

Written 2026-10-04 against commit `edd7e950`, from the scripts and modules in
this repository — nothing below is a step the repository does not actually
support. Where the path has a hole, the hole is named under **Known gaps**
instead of being papered over with an invented command.

There are two ways a medic comes to exist. The one this file describes is
**from source**: a person with a Pi 5 and this repository. The other is
**Clone** — an existing medic imaging a card and provisioning the new Pi over a
cable (BUILD ▸ *Clone this device*, `workflows/clone.py`); what a parent medic
must carry for that is at the end.

## What the code assumes about the hardware

- **Raspberry Pi 5.** The clone ladder refuses anything else
  (`workflows/clone.py` `verify_target_pi5`); the boot script writes
  `/boot/firmware/config.txt`, the current Raspberry Pi OS layout.
- **A 5-inch DSI touchscreen, 720 x 1280 portrait.** The front-page poster is
  painted at that size (`ui/home_zones.py`) and the app assumes a portrait panel
  (`ui/app.py`; `RNM_WINDOWED=1` gives a 1280 x 720 development window
  instead). The touch controller the code knows is the **Goodix** one: the
  touch provider is chosen by finding it in sysfs (`ui/touch_input.py`), and
  the clone's udev rule names "Goodix Capacitive TouchScreen".
- **The medic's own radio: a Heltec Wireless Tracker flashed as an RNode with
  GPS passthrough** (`docs/TRACKER_GNSS.md`, `monitor/serial_splitter.py`).
  `scripts/setup_boot.sh` finds it as
  `/dev/serial/by-id/*Espressif*JTAG*-if00`; any other RNode board can be used
  by passing `RNODE_BY_ID=/dev/serial/by-id/...` to that script, but then there
  is no GPS and no offline clock source.
- **A USB SD-card reader.** Every Pi card the medic writes — node or clone —
  goes through its own reader: `provisioning/pi_imager.py` refuses any target
  that is not a present, removable USB disk, and the sudoers policy pins `dd`
  to `/dev/sd*`.
- **Power.** `setup_boot.sh` sets `usb_max_current_enable=1` so the radio gets
  full USB-A current. A Waveshare UPS (INA219 over I2C) is optional; the battery
  gauge and low-battery shutdown read it when present (`monitor/ups.py`).

## What the code assumes about the operating system

- **Raspberry Pi OS, 64-bit, with the desktop.** The UI runs fullscreen over
  the labwc desktop session and is started by an XDG autostart entry
  (`scripts/nodemedic-ui.desktop` → `scripts/start_ui.sh`). The kiosk
  adjustments made to that desktop on the live medic — and how to undo them —
  are in `docs/MEDIC_KIOSK.md`; they are **not** applied by anything in the repo.
- **A user named `nodemedic`, uid 1000, home `/home/nodemedic`.** The path
  `/home/nodemedic/reticulum-tool` is hard-coded in `scripts/setup_boot.sh`,
  `scripts/start_ui.sh`, `scripts/nodemedic-ui.desktop`,
  `scripts/world-map-fill.service` and `scripts/rnode-splitter.service`;
  `scripts/restart_ui.sh` assumes `/run/user/1000`.
- **Python 3.13 on aarch64.** The pinned package set in
  `assets/requirements.txt` is "the set proven to install offline on Python
  3.13 / aarch64"; the wheelhouse the medic builds for clones is cp313.
- **Reticulum running as system services.** `rnsd.service` and `lxmd.service`
  are expected to exist (`scripts/setup_boot.sh` writes a drop-in for
  `rnsd.service`; `workflows/node_mode.py` restarts both; the medic's `lxmd`
  unit runs `lxmd -s`, per `docs/CHAT.md`), with `~/.reticulum/config`
  carrying an `RNodeInterface` whose `port =` line the boot script repoints at
  the splitter. **Neither unit file nor a medic config template is in this
  repository** — see Known gaps.

## Steps

Each line is one action. Do them in order, on the Pi, in a terminal (or over
SSH once it is on your Wi-Fi).

- Write Raspberry Pi OS (64-bit, with desktop) to the medic's own card, with the
  user named `nodemedic`, and boot it.
- Connect it to Wi-Fi and open a terminal as `nodemedic`.
- Clone the repository to the path the scripts expect:
  `git clone https://github.com/5ugAv/Reticulum-Node-Medic.git /home/nodemedic/reticulum-tool`
- Install the Python stack (the same command the clone ladder runs when it is
  online; `~/.local/bin` is where the console tools land and `start_ui.sh` puts
  it on the app's PATH):
  `cd /home/nodemedic/reticulum-tool && pip3 install --break-system-packages --user -r assets/requirements.txt`
- Install the root card-writing helper (the clone ladder's own command):
  `sudo install -D -m 755 -o root -g root assets/scripts/prepare_card.py /usr/local/lib/nodemedic/prepare-card`
  The imager compares the installed copy byte-for-byte with the repo copy
  before every card write and refuses when they differ, so repeat this line
  after any update that changes `assets/scripts/prepare_card.py`.
- Install the scoped sudo policy (validated with `visudo -c` on a temp copy
  first; it whitelists exactly the privileged calls the app makes — backlight,
  the service restarts, `dd` to USB cards, the card helper, the cable-link
  address, the gpsd files — and removes any blanket `NOPASSWD:ALL`):
  `sudo bash provisioning/security/apply_sudoers.sh`
- Give the medic its own SSH key (the one it puts on every card it images, so
  it can log in to the Pi it just made):
  `ssh-keygen -q -t ed25519 -N '' -f ~/.ssh/id_ed25519 -C nodemedic`
- Keep NetworkManager off the cable-birth link (without this, NM runs DHCP on
  `usb0` and wedges every Pi birth over USB; Self Diagnose checks for the file):
  `sudo install -m 644 scripts/nodemedic-usb0-unmanaged.conf /etc/NetworkManager/conf.d/99-nodemedic-usb0.conf`
- Get `rnsd` and `lxmd` running as systemd services with an `RNodeInterface`
  in `~/.reticulum/config` — **by hand; the repository does not do this step**
  (Known gaps, first item).
- Plug the medic's own radio into a USB-A port.
- Run the boot installer:
  `sudo bash scripts/setup_boot.sh`
  It adds `usb_max_current_enable=1` to `config.txt`, installs the serial
  splitter service with this radio's by-id path filled in, orders `rnsd` after
  the splitter and points its `port =` at `/tmp/rnode-jonesey` (backing the
  config up as `config.pre-splitter.bak`), and installs the UI autostart entry.
  It refuses, and says so, if it cannot find the radio.
- Optional — the branded boot splash (fetches Plymouth packages with apt, so it
  needs internet): `sudo bash scripts/setup_splash.sh`
- Optional — the resumable world-map download at every boot:
  `sudo cp scripts/world-map-fill.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now world-map-fill`
- Optional — key-only SSH and the firewall, each with its own self-revert
  timer: `provisioning/security/README.md`, steps 2 and 3.
- Reboot: `sudo reboot`

## First boot

The UI waits up to two minutes for `rnsd`'s shared instance before starting
(`scripts/start_ui.sh`), so that it attaches as a client and never becomes the
shared-instance server by accident. A medic that has never finished its setup
opens the **setup walkthrough** (`provisioning/first_use.py`): the security
choices first, then a tour that opens each mode by its painted word, then the
front page. Settings can run it again for the next keeper.

Then, while the medic is still on Wi-Fi:

- Settings ▸ **Field readiness** ▸ *Prepare for the field*. It fetches the
  pinned RNode firmware bundle (1.86), the phone messenger APKs, the Python
  wheelhouse for building nodes offline (when empty) and the `.deb` cache a
  clone needs for its screen (when empty), then re-reads the disk and says what
  is still missing. (`python3 scripts/refresh_deb_cache.py` is the manual route
  to the same deb cache.)
- MAPS ▸ *Download offline map* over the area the nodes will be in. Tiles live
  in `~/.reticulum-node-medic/maps/`, outside the repo tree.
- Copy a Raspberry Pi OS Lite (64-bit) image onto the medic as
  `~/pi_os_lite.img.xz`. Without it no card can be written, and the imager
  says so rather than downloading one.
- BUILD one RTNode-2400-NM board once with Wi-Fi on: the first build fetches
  the PlatformIO packages and the Arduino esp32 core that later offline builds
  reuse. Field readiness reports both halves of that toolchain.
- The medic's own GPS and clock source come from its Tracker. The screen that
  flashes and wires it (PROBE ▸ *Birth the GPS Tracker — this medic's
  firstborn*, also a step of the first-use tour) installs `gpsd` with apt when
  it is absent, so run it while online — and see the second Known gap.

## Known gaps in this path

Stated plainly, from the repository as it is on 2026-10-04:

- **No `rnsd.service` / `lxmd.service` unit files and no Reticulum config
  template for the medic itself.** `assets/configs/` holds templates for the
  *nodes* the medic builds (transport, Pi 5, Pi Zero, Meshtastic). The boot
  script, `workflows/node_mode.py`, Self Diagnose and the vault scripts all
  assume both units already exist on the medic. Someone building from source
  writes them by hand today.
- **The medic's own radio firmware is not in this repository.** The Tracker
  passthrough build (`workflows/rnode_flash.py` `TRACKER_BUILD_DIR`) points at a
  firmware fork under the developer's home directory, and the RTNode-2400-NM
  trees (`workflows/rtnode_build.py` `RTNODE_PROJECT_DIR`, `TECHO_PROJECT_DIR`)
  likewise. From a fresh checkout the firstborn step, and every custom-board
  RNode birth (Wireless Tracker, MeshPocket, EoRa-S3), fail with a stated
  reason (readiness ledger #43, #168, #120).
- **Two system packages the UI relies on are installed by nothing here**:
  `wlr-randr` (Settings ▸ Display ▸ *Fix screen colours*,
  `provisioning/screen_fix.py`) and `libmtdev.so.1` (multitouch — without it
  the app falls back to a single-finger mouse provider and Self Diagnose says
  so, `ui/touch_input.py`).
- **The kiosk tweak is manual.** Removing the desktop panel so nothing floats
  over the app is a root edit recorded in `docs/MEDIC_KIOSK.md`, not a script.
- **The carried caches start empty on a fresh checkout.** `assets/firmware/`,
  `assets/packages/` (wheels and debs), `assets/maps/`, `assets/apps/` and
  `assets/fonts/` are gitignored; until Field readiness has run online the medic
  cannot flash a radio, build a node offline, hand a phone a messenger, or clone
  itself (ledger #115, #154).
- **The two routes to a medic do not match.** From source you get the desktop
  session with an XDG autostart; the clone ladder installs a different
  arrangement on a Lite image — a `cage` kiosk service owning tty1
  (`reticulum-node-medic.service`), a Goodix rebind unit, a touch-only udev
  rule, a hidden cursor theme and a recovery boot order. Nobody has reconciled
  them; both run the same `main.py`.

## What a parent medic must carry to clone

The clone ladder (`workflows/clone.py`, shown one row per step on the Clone
screen) moves these from the parent to the child. If the parent lacks one, the
corresponding step fails with a message rather than faking it:

- the tool tree itself, minus `.git` and caches;
- the offline RNode firmware cache at `~/.config/rnodeconf/update`;
- the toolchains and trees in `CARRIED_TREES`: `~/.arduino15`, `~/.local/bin`
  (arduino-cli, esptool, rnodeconf, adafruit-nrfutil, pio), `~/Arduino`, the Pi
  OS image `~/pi_os_lite.img.xz`, and the Tracker firmware fork — all required;
  the RNode, MeshPocket and RTNode-2400 trees and build assets — optional;
- the Python wheelhouse in `assets/packages/` and the `.deb` cache in
  `assets/packages/debs` (the `cage` display stack, Dire Wolf and ALSA);
- the medic's Kivy config (the doubled-tap cure), its offline maps, its
  monitoring records and its fleet roster;
- the card helper source (`assets/scripts/prepare_card.py`), installed on the
  child, and a fresh SSH keypair generated **on** the child;
- a **fresh Reticulum identity** generated on the child — the parent's is never
  copied — plus a lineage stamp and a trust record for the child.

What stood in the way of the clone test is the first section of
`docs/READINESS_LEDGER.md` — closed on 2026-10-04; the test itself is still
to be run.

## Updating a medic

- On the medic: `cd ~/reticulum-tool && git pull && bash scripts/restart_ui.sh`
- From a laptop: sync exactly the tracked files and nothing else, then restart —
  `README.md` ▸ *Updating the tool*. A partial sync has taken the UI down
  before; after an rsync, `git log` on the medic does not describe what is
  running.
- `scripts/restart_ui.sh` refuses while a card write, a flash, an `rnodeconf`
  run, a DFU, an arduino-cli upload or the UI's own busy marker is live; wait,
  or `FORCE=1` only when the running instance is the thing that is broken.
- If a new privileged command was added to the code, the live sudoers lags the
  repo until `sudo bash provisioning/security/apply_sudoers.sh` is run again.
