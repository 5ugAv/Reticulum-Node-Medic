# Build your own Node Medic

**Status.** The path on this page ran end to end on 2026-10-09 in a virtual
machine shaped like a fresh Pi 5 card, in 19 minutes (the appendix says
exactly what that proved and what it could not). No run on a real Raspberry
Pi 5 has been reported yet; on one, expect one to two hours, most of it
compiling. If you do the first, open an issue and attach `~/medic_setup.log`.

A medic can also come from another medic: an existing one images a card and
fills it over a cable (BUILD ▸ *Clone this device*). That needs a medic first;
this page is for when there is none.

## What you are building

A Raspberry Pi 5 in a case, with a touchscreen, a small LoRa radio board that
is also its GPS, and a USB card reader. On it runs Node Medic: it builds LoRa
mesh nodes from bare boards, watches them over the air, diagnoses and repairs
them, messages over the mesh, and can clone itself onto another Pi 5. Words
this page uses:

- **medic** — this device. **node** — a LoRa board (or a Pi with one) placed
  in the field; the medic builds them.
- **RNode** — the radio firmware a node runs; **RTNode-2400-NM** — the medic's
  build of a standalone-node firmware. The medic carries both.
- **birth** — building a node; **adopt** — enrolling one built elsewhere;
  **kin** — the nodes this medic, or a medic it trusts, built.
- **clone** — a copy of a medic made by a medic. **the reference medic** —
  Node Medic 1, the first one, whose versions the setup installs.
- **rnsd / lxmd** — Reticulum's network and messaging services, which the
  medic runs once its radio is set up.
- **the walkthrough** — the screens the medic opens the first time it starts.

## Parts list — one medic

Tick what you have. Sellers and prices vary by country; the band of the radio
does not — pick the variant for where the medic will live (*Which band*,
below).

**The medic itself**

* [ ] **Raspberry Pi 5**, 8 GB — every medic so far is built on one; the
  software does not check the size, and no other size has been tried
* [ ] **Raspberry Pi Active Cooler** — the case is designed around it, and the
  setup's compile hour wants it
* [ ] **Raspberry Pi Touch Display 2, 5-inch** (720 × 1280) — the size the
  case is designed for and the one built with. In its box: the 22-to-15-way
  ribbon a Pi 5 needs, the GPIO power lead, eight M2.5 screws. (The 7-inch has
  the same resolution and the same touch over the same ribbon in Raspberry
  Pi's documentation, but has not been tried.)
* [ ] **microSD card, 64 GB** — the setup downloads and builds about 14 GB on
  top of the OS; 32 GB is the practical minimum
* [ ] **Power supply, 5 V / 5 A USB-C** — the official Raspberry Pi 27 W.
  Not a 3 A one: on this bench a 3 A supply browned out a medic and took a USB
  controller with it, and the setup turns on full USB current for the radio
* [ ] **Heltec Wireless Tracker** — the 902–928 MHz variant for Australia,
  New Zealand and the Americas (863–870 MHz for Europe). The V1.1 board; the
  Rev 2.0 (V2) is supported from the next release. In its box: the board, a
  LoRa antenna, a battery lead, pin headers. Its built-in ceramic GNSS antenna
  is all the medic needs
* [ ] **A short USB-A to USB-C cable** — a data cable, not a charge-only one
  (a charge-only cable looks exactly like dead firmware). The Tracker lives
  on one of the Pi's USB ports; in the printed case that is port 1
* [ ] **A USB microSD card reader** — every node card and clone card the
  medic writes goes through it (the software refuses any other target)

**To set it up** (not part of the medic)

* [ ] A computer with a card reader and **Raspberry Pi Imager**
  (<https://www.raspberrypi.com/software/>), to write the medic's own card
* [ ] A USB keyboard, or SSH from that computer
* [ ] Internet for the setup, once (Wi-Fi or Ethernet); the finished medic
  needs none

**Optional**

* [ ] **The 3D-printed case** — [Node Medic case on Cults 3D](https://cults3d.com/en/3d-model/gadget/node-medic-case),
  designed around the parts above and the UPS below, with the assembly
  photos
* [ ] **Waveshare UPS Module 3S** and three 18650 cells — field power; the
  battery gauge appears on the front page by itself when the module is there
* [ ] **An Ethernet cable** — only for cloning a second medic over a cable
* [ ] **A better 915 MHz antenna** than the one in the Tracker's box, with an
  IPEX (U.FL) plug — the radio must never run without one
* [ ] **An external GNSS antenna** — advanced: the Tracker's external GNSS
  socket is not connected as shipped; using it means moving a 1 mm 0 Ω link
  on the board, which is microsoldering. The ceramic antenna works as the
  board comes ([`TRACKER_GNSS.md`](TRACKER_GNSS.md))

Every LoRa node the medic builds needs its own board and antenna:
[`BOARD_SHOPPING_LIST.md`](BOARD_SHOPPING_LIST.md).

### Which band

The medic's radio defaults are 915.125 MHz — Australia, New Zealand, the
Americas. A European medic needs the 863–870 MHz Tracker and its defaults
changed in Settings ▸ *Default radio parameters* before it builds any node.
Only 915 MHz has been used in the field.

## Before you start

- A computer with a card reader and Raspberry Pi Imager (Windows, macOS or
  Linux).
- Internet for the whole setup. It downloads and builds about 14 GB on top of
  the OS — the figure its own free-space checks allow for, not a measurement —
  so not on a metered connection.
- A USB keyboard, or SSH from that computer.
- One to two hours.
- Know that the setup changes the Pi's boot chip (the recovery boot order) and
  system files (`config.txt`, services, udev, NetworkManager). `--check`
  previews what is missing and changes nothing.

## Assemble

With the Pi unpowered:

- Fit the Active Cooler.
- Plug the display's ribbon into one of the Pi's two DISP connectors and its
  power lead onto the GPIO header: the red lead on pin 2, the black on pin 6.
  Both leads come in the display's box, and Raspberry Pi OS detects the
  display with no settings.
- Push the 915 MHz antenna's U.FL plug onto the Tracker's socket marked
  **LoRa** — not the one marked GNSS. The radio must never be powered without
  it. Leave the Tracker unplugged for now.
- The card reader can go in at any time.
- Optional: the case and the UPS; the Cults page has the assembly photos.
  Wherever the Tracker ends up, its white ceramic square is its GPS antenna:
  it should face the sky, with no metal over it.

## Write the card

On your computer, in Raspberry Pi Imager:

- Device: Raspberry Pi 5. Operating system: under *Raspberry Pi OS (other)*,
  **Raspberry Pi OS Lite (64-bit)**. Any current Lite release passes (Debian
  13, "trixie"; an older "bookworm" card is refused).
- Storage: the medic's card.
- When Imager offers to customise the OS, set: a hostname, a user name and
  password (this user gets the passwordless sudo the setup needs), your Wi-Fi
  and its country, your time zone, and **enable SSH** if you will type over
  the network.
- Write it.

(The 2026-06-18 Lite release the manifest pins is for the cards the medic
writes later, for nodes and clones; the setup downloads it for you.)

## First power-on and the setup

- Card in, power on. The login prompt appears on the touchscreen; log in there
  with the keyboard, or from your computer: `ssh <user>@<hostname>.local`.
- Install git and tmux (Lite has neither):
  `sudo apt update && sudo apt install -y git tmux`
- Get Node Medic, at the path it reads:
  `git clone https://github.com/5ugAv/Reticulum-Node-Medic.git ~/reticulum-tool`
- Over SSH, start `tmux` first, so a dropped connection does not stop the run
  (`tmux attach` picks it back up).
- Run the setup:
  `python3 ~/reticulum-tool/scripts/setup_medic.py`
- Wait. It prints one line per step, 31 in all. A line can sit for up to an
  hour with nothing after it while a firmware image compiles — leave it; the
  log (`~/medic_setup.log`) fills in when each command ends. Success ends with
  the line `Node Medic is set up.`
- If it stops — a failed download, a power cut, a step marked `FAILED` — put
  right what that line says and run the same command again. Finished steps
  are skipped; downloads resume.
  `python3 ~/reticulum-tool/scripts/setup_medic.py --check` lists what is
  still missing and changes nothing.
- `sudo reboot` (or start the setup with `--reboot` and it restarts by itself
  once every step is done).

## The walkthrough

The medic starts on its own screen and, the first time, opens a short tour,
one screen per mode (its lock-your-records half is switched off in this
version; records are locked later from Settings ▸ *Encrypt my records*).

The tour's first page sets up the medic's own radio and position finder:

- Unplug everything else from the Pi's USB ports, except the keyboard.
- Check the 915 MHz antenna is on the Tracker's LoRa socket, then plug the
  Tracker in.
- Tap the picture of your board: the Wireless Tracker, or a Heltec T114 with
  its GPS module.
- Press nothing on the board. Press *Set up its radio*.
- The medic writes its radio software onto the board, records it as its own
  radio (never flashed again by accident), wires its radio services around
  it, restarts itself, and checks that the radio and the GPS answer. The GPS
  needs sky: indoors it may report no fix yet.
- *Skip for now* leaves it for Settings ▸ *This medic's radio and position
  finder*.

## After the walkthrough

- MAPS ▸ *Download offline map* over the area your nodes will be in. The
  setup only started the world overview (zoom 0–8, about 87,000 tiles), which
  keeps filling in the background while the medic is online; the detail of
  your own area is your choice.
- Settings ▸ *Field readiness* says what the medic still lacks for the field.
- Settings ▸ *Encrypt my records* locks its records.
- Build your first node: [`WHICH_NODE_TO_BUILD.md`](WHICH_NODE_TO_BUILD.md).

## Optional

- **The UPS.** As on Node Medic 2: the Waveshare UPS Module 3S powers the Pi,
  charges from its own 12.6 V 2 A supply, and reports its cells to the medic
  over three wires (SDA, SCL and GND to the Pi's pins 3, 5 and 6). The
  battery gauge appears on the front page by itself, and the medic shuts down
  cleanly on an empty pack ([`MITOSIS_READINESS.md`](MITOSIS_READINESS.md)
  has the details).
- **An external GNSS antenna.** Not needed: the ceramic square works. The
  Tracker's GNSS socket is not connected as shipped; using it means moving a
  1 mm 0 Ω link on the board — microsoldering — after which an external
  antenna is required, not optional ([`TRACKER_GNSS.md`](TRACKER_GNSS.md)).
- **Lock it down**, once everything works. Put your computer's public key into
  `~/.ssh/authorized_keys` on the medic first — key-only SSH locks out
  everyone without one — then run the three scripts in
  [`../provisioning/security/README.md`](../provisioning/security/README.md)
  with `--user <your user>` (without it they assume `nodemedic`, the first
  medic's account). Until then the user keeps Raspberry Pi OS's passwordless
  sudo.

## If something goes wrong

- **A black screen, or wallpaper with nothing on it** (the app has died):
  from your computer,
  `ssh <user>@<hostname>.local 'cd ~/reticulum-tool && bash scripts/ui_busy_guard.sh && sudo systemctl restart reticulum-node-medic'`.
  The guard refuses while the medic is writing a card or flashing a board;
  wait and try again.
- **Scrambled or shifted colours on the panel:** Settings ▸ Display ▸ *Fix
  screen colours*; or power off, wait ten seconds, power on.
- **The setup refused to start:** its sentence says why — not a Pi 5, not a
  64-bit Lite trixie card, not at `~/reticulum-tool`, or run with sudo.
- **Read the logs:** `~/medic_setup.log` (the setup), `~/ui.log` and
  `~/ui_crash.log` (the app).
- Report it: open an issue on GitHub with the log and the step's line.

## Updating

`cd ~/reticulum-tool && git pull`, run the setup again (it installs anything
whose pin moved and builds any missing image), then restart the app with the
command above. The details are in the appendix.

---

## Appendix — what the setup does, and what it assumes

Rewritten 2026-10-08 for `scripts/setup_medic.py`. Nothing below is a step the
repository does not do; where the path still has a hole, the hole is named
under **Known gaps** instead of being papered over.

There are two ways a medic comes to exist, and they end in the same place:

- **Clone** — an existing medic images a card for a new Pi 5 and fills it over
  a cable (BUILD ▸ *Clone this device*, `workflows/clone.py`). What a parent
  must carry for that is at the end of this file.
- **From GitHub** — a Pi 5, a fresh Raspberry Pi OS Lite card and this
  repository. `scripts/setup_medic.py` runs the clone's own ladder on the Pi
  itself, with the internet standing in for the parent
  (`workflows/medic_setup.py`). The result is configured like a clone: the
  same `cage` kiosk, the same packages, the same toolchains, firmware and
  caches, at the versions Node Medic 1 runs.

Every version and source the setup installs is in one file,
[`assets/medic_manifest.json`](../assets/medic_manifest.json): Node Medic 1's own
pins, read on that medic on 2026-10-08, each with the public place it comes
from. `tests/test_medic_setup.py` holds that file to the code: every firmware
folder the code builds from has a pinned source, every board BUILD flashes from
a pre-built image has a build recipe, every step of the clone is accounted for.

### What the code assumes about the hardware

The case these parts fit in — a 3D print, with the parts list and assembly
steps (Pi 5, Active Cooler, Touch Display 2, Waveshare UPS Module 3S, Heltec
Wireless Tracker) — is on Cults 3D:
[Node Medic case](https://cults3d.com/en/3d-model/gadget/node-medic-case).

- **Raspberry Pi 5.** The setup and the clone ladder both refuse anything else
  (`workflows/clone.py` `verify_target_pi5`, run by the setup too).
- **A Raspberry Pi Touch Display 2, 720 x 1280 portrait** — the 5-inch one on
  the keeper's medics; Raspberry Pi's documentation gives the 7-inch the same
  panel resolution and the same touch over the same ribbon, untried here.
  The front-page poster is
  painted at that size (`ui/home_zones.py`) and the app assumes a portrait panel
  (`ui/app.py`; `RNM_WINDOWED=1` gives a 1280 x 720 development window
  instead). The touch controller the code knows is the **Goodix** one: the
  touch provider is chosen by finding it in sysfs (`ui/touch_input.py`), and
  the kiosk's udev rule names "Goodix Capacitive TouchScreen".
- **The medic's own radio: a Heltec Wireless Tracker (or a Heltec T114 with
  its GPS module) flashed as an RNode with GPS passthrough** (`docs/TRACKER_GNSS.md`, `monitor/serial_splitter.py`). It
  is set up from the screen (`workflows/medic_radio.py`): the medic writes the
  Tracker image onto it, finds it by its `/dev/serial/by-id` name and wires the
  splitter, `rnsd` and `lxmd` around it. A Wireless Tracker V2 (Rev 2.0, with
  its 28 dBm amplifier) or a Heltec T114 with GPS can take that place instead;
  the first-radio screen shows the pictures and the keeper picks.
- **A USB SD-card reader.** Every Pi card the medic writes — node or clone —
  goes through its own reader: `provisioning/pi_imager.py` refuses any target
  that is not a present, removable USB disk.
- **Power.** The setup adds `usb_max_current_enable=1` (full USB-A current for
  the radio) and `dtparam=i2c_arm=on` (the UPS gauge) to
  `/boot/firmware/config.txt`, the two lines a clone's card is baked with
  (`assets/scripts/prepare_card.py` `MEDIC_CONFIG_LINES`). A Waveshare UPS
  (INA219 over I2C) is optional; the battery gauge and low-battery shutdown read
  it when present (`monitor/ups.py`).

### What the code assumes about the operating system

- **Raspberry Pi OS Lite (64-bit), trixie, on the medic's own card.** The
  setup refuses a desktop card (a display manager would fight the kiosk for the
  screen), another architecture, another Debian release or another Python. The
  image the medic itself writes for nodes and clones is pinned separately: the
  2026-06-18 Lite release (step 26, official download, SHA-256 checked), the
  release a clone's carried `.deb` plan is made against
  (`assets/clone_base/dpkg_status`).
- **Any user name.** The setup runs as whoever runs it (`id -un`, `$HOME`),
  as long as that user is not root and their home is `/home/<user>` (the kiosk
  unit is written for that). Raspberry Pi OS gives its first user passwordless
  sudo, which the setup needs for packages, units and the boot chip.
- **The repository at `~/reticulum-tool`.** The kiosk, the field caches and the
  services read that path; the setup refuses to run from anywhere else.
- **Python 3.13 on aarch64**, as Raspberry Pi OS trixie ships it.
- **The screen through the `cage` kiosk unit** (`reticulum-node-medic.service`,
  written by the clone's own `configure_autostart`), with a Goodix touch retry
  unit, a touch-only udev rule and an empty pointer theme.
- **Reticulum as system services, wired by the radio set-up.** The `rnsd` and
  `lxmd` units and `~/.reticulum/config` are written when the medic's own radio
  is set up on its screen (`workflows/medic_radio.py`: `rnsd_unit`,
  `lxmd_unit`, `reticulum_config`), exactly as on a clone. Until then the medic
  runs without a radio and says so.

### What the setup does

In order. "Clone step" names the step of the clone's ladder each one stands
for; **same** means the setup runs that very function from `workflows/clone.py`
on this machine.

| # | Step | How | Clone step |
|---|---|---|---|
| 1 | Check this is a Raspberry Pi 5 | `/proc/cpuinfo` | **same** `verify_target_pi5` |
| 2 | Check the system | 64-bit, trixie, Python 3.13, not root, `/home/<user>`, no desktop | — (a clone's card is written by its parent) |
| 3 | Check Node Medic is at `~/reticulum-tool` | — | `transfer_tool` |
| 4 | Set the clock from the internet | NTP on, wait for sync (a Pi 5 has no clock battery) | `carry_the_time` |
| 5 | Screen packages | apt: `wheelhouse.DISPLAY_PACKAGES` (cage, libGL, Xwayland, SDL2, wlr-randr) | `install_display_stack` |
| 6 | Tool packages | apt: `wheelhouse.APT_PACKAGES` (git, pip, gpsd, uhubctl, rpiboot, sshpass, acl, Dire Wolf, ALSA, pexpect) | `install_carried_packages` |
| 7 | Python packages | pip `--user`: the manifest's 30 pins plus the `assets/requirements.txt` lines for what Node Medic 1 takes from Debian | `install_dependencies` |
| 8 | Reticulum patch | `assets/patches/rns-1.3.7-medic.patch` onto rns 1.3.7 | `install_dependencies` |
| 9 | Touch: one provider, no pointer | Kivy's default `~/.kivy/config.ini` with `probesysfs` commented out and `show_cursor = 0` | `carry_touch_cure` |
| 10 | UPS I2C and full USB current | `config.txt` lines from `prepare_card.MEDIC_CONFIG_LINES` | — (a clone's card is baked with them) |
| 11 | Records | an empty `~/.reticulum-node-medic/registry.json` | `copy_monitoring_db` |
| 12 | Own Reticulum identity | `rnid --generate` | **same** `generate_fresh_identity` |
| 13 | Boot into Node Medic | cage kiosk unit, Goodix touch retry, touch-only udev rule, empty pointer theme, `i2c-dev`, NetworkManager kept off `usb0` | **same** `configure_autostart` |
| 14 | Recovery boot order | EEPROM `BOOT_ORDER=0xf321` (SD, network, RPIBOOT) | **same** `bake_recovery_bootorder` |
| 15 | Card-writing helper | `/usr/local/lib/nodemedic/prepare-card` | **same** `install_card_helper` |
| 16 | Radio set-up helper | `/usr/local/lib/nodemedic/radio-units`, the root helper the walkthrough's radio page runs | **same** `install_radio_helper` |
| 17 | Own SSH key | `~/.ssh/id_ed25519` | **same** `ensure_ssh_keypair` |
| 18 | arduino-cli 1.5.1 | official release, SHA-256 checked, into `~/.local/bin` | `carry_the_toolchain` |
| 19 | Board cores | esp32 2.0.17, adafruit:nrf52 1.7.0, Heltec_nRF52 1.6.0 (its UF2 step switched off), rakwireless:nrf52 1.3.3 | `carry_the_toolchain` |
| 20 | Arduino libraries | the manifest's 18, each at its pin, `--no-deps` | `carry_the_toolchain` |
| 21 | Firmware sources | six trees, each fetched at its pinned commit | `carry_the_toolchain` |
| 22 | Tracker, EoRa-S3, MeshPocket images | each board's own `RNodeBoard.compile_command` in its tree | `carry_the_toolchain` |
| 23 | Heltec V4 colour image | `HeltecV4RGBWorkflow`'s source patches and compile | `carry_the_toolchain` |
| 24 | PlatformIO | telemetry off, espressif32 6.13.0, every RTNode-2400 env's packages | `carry_the_toolchain` |
| 25 | RTNode-2400 compiles | one PlatformIO env and the three nRF52 Makefile targets, once | `carry_the_toolchain` |
| 26 | Raspberry Pi OS Lite image | the pinned 2026-06-18 image to `~/pi_os_lite.img.xz`, SHA-256 checked | `carry_the_toolchain` |
| 27 | Field caches | Settings ▸ Field readiness ▸ *Prepare for the field*, pressed from here: RNode firmware 1.86, the phone apps, the wheelhouse, this medic's own package versions frozen for its clones, the `.deb` plan | `transfer_firmware_cache`, `transfer_tool` |
| 28 | World map | `scripts/world-map-fill.service` for this user, started; it carries on in the background | `copy_offline_maps` |
| 29 | Check the finished medic | | **same** `final_verification` |
| 30 | Node Medic running | the kiosk unit active (true after the restart) | `confirm_tool_running` |
| 31 | Restart | only with `--reboot`, only when nothing failed, never while the medic is busy | `restart_into_tool` |

Three clone steps have nothing to do here: `copy_kin_roster` (there is no
fleet to inherit), `stamp_lineage` (there is no parent; About reads the git
checkout) and `record_child_trust` (it writes the parent's trust store).

The firmware sources (all published; `firmware_trees` in the manifest):

| Folder | Repository | Branch @ commit | Built here into |
|---|---|---|---|
| `~/RNode_Firmware` | 5ugAv/RNode_Firmware | `medic-cross` @ 2da8bc3 | the Heltec V4 colour image |
| `~/overlay_test/RNode_Firmware` | 5ugAv/RNode_Firmware | `jonesey` @ ae4a528 | the Heltec Wireless Tracker and Tracker V2 images |
| `~/EoRa-S3/RNode_Firmware_CE` | 5ugAv/RNode_Firmware | `eora-s3` @ b574a82 | the Ebyte EoRa-S3 image |
| `~/MeshPocket/RNode_Firmware_CE` | 5ugAv/HELTEC-MeshPocket-RNode | `main` @ 2a4fb1f | the Heltec MeshPocket and T114-with-GPS DFU packages |
| `~/RTNode-2400` | 5ugAv/RTNode-2400 | `techo-support` @ 3a09717 | RTNode-2400-NM for T-Echo, RAK4631, T114 |
| `~/rnm-assets/RTNode2400` | 5ugAv/RTNode-2400 | `feature/neopixel-status-led` @ 8cccdea | RTNode-2400-NM for the ESP32-S3 boards |

### Known gaps in this path

Stated plainly, from the repository as it is on 2026-10-09:

- **The setup has not yet been run end-to-end on a Pi 5.** It has run end to
  end once, on 2026-10-09, from the public repository, in a virtual machine
  built like a fresh card on a Pi 5: Debian trixie on arm64 with every package
  the 2026-06-18 Lite card carries, Raspberry Pi's own package server and
  sources, a first user with passwordless sudo, no git, no screen. Following
  the steps above word for word, every step passed in 19 minutes except the
  boot chip, which a virtual machine has not got, and the NetworkManager rule,
  which needs the NetworkManager folder every real card has (the machine had
  no NetworkManager; given the folder, the step passed). A second run skipped
  every finished step in two seconds. Node Medic then started under its own
  cage kiosk on a virtual screen, with no errors, and opened the setup
  walkthrough. That run found one fault, now fixed: a package source that apt
  names by anything but `http`/`https` (Debian's cloud images use
  `mirror+file:`) silently dropped those packages from the clone's `.deb`
  plan: it planned 22 screen packages where Node Medic 1 plans 86, and still
  reported the caches complete. The plan now stops and names the source; with
  Raspberry Pi OS's own sources it planned the same packages as Node Medic 1. Before that, the setup was checked without a Pi:
  - its tests drive every step against an emulated machine;
  - every pin was checked against its source: every Python pin exists on PyPI
    for Python 3.13 on aarch64; every Arduino library version is in the Library
    Manager's index; the arduino-cli download matches its published SHA-256;
    the OS image's published SHA-256 is the hash of Node Medic 1's copy; the
    RAK and Heltec core archives match their index checksums; PyPI's rns 1.3.7
    with the vendored patch is byte-identical to Node Medic 1's;
  - arduino-cli 1.5.1 and PlatformIO 6.1.19 were run in throwaway folders to
    read the output and files the setup's checks parse;
  - on a Mac, with macOS builds of the same pinned cores and libraries and the
    firmware trees at their pinned commits, the Tracker, EoRa-S3, MeshPocket
    and Heltec V4 colour images compiled with the code's own commands into the
    files BUILD reads, and so did RTNode-2400 for the T-Echo, RAK4631 and T114
    (their Makefile targets) and the Heltec V4 (PlatformIO on espressif32
    6.13.0); the setup's own checks then found every one of those files. That
    is how two faults were found and fixed before any Pi saw them: the Heltec
    core's UF2 step (below), and a check that looked for `firmware.bin` where
    the RTNode-2400 tree names its image `rtnode_<variant>.bin`.

  Only a Pi 5 can prove what the virtual machine could not: the boot chip, the
  kiosk on a real screen and touch panel, and the run on a Pi's own card,
  network and power. Anything the first real run finds belongs in this list.
- **The medic's own radio still needs the keeper and the Tracker**, as on a
  clone: `rnsd`, `lxmd` and the Reticulum config come from the walkthrough's
  radio page, not from the setup.
- **Security hardening is not applied.** The scoped sudoers, key-only SSH and
  the firewall are still run by hand (`provisioning/security/README.md`), as
  they were on Node Medic 1. Until then the medic's user keeps Raspberry Pi
  OS's passwordless sudo.
- **Node Medic 1's own `~/.kivy/config.ini` was not captured.** A clone
  receives that file whole; the setup writes Kivy's default file with the two
  changes the clone's code names (the doubled-tap cure — `probesysfs` off — and
  the pointer hidden). Any other line in Node Medic 1's file is not reproduced.
- **Some versions are pinned no tighter than their source.** The libraries each
  RTNode-2400 PlatformIO env resolves from `platformio.ini`; the Debian
  packages (whatever the archive holds on the day); the phone apps (the newest
  release when fetched); the Python packages Node Medic 1 takes from Debian
  (docutils, packaging and pyserial come from PyPI at install time). Node Medic
  1's Arduino `Crypto` version was not captured; 0.4.0 is pinned because it is
  what the firmware Makefiles' `lib install "Crypto"` installs (manifest notes).
- **Two board cores are not used exactly as published, and Node Medic 1's own
  fixes for them were never written down.** RAKwireless publishes no aarch64
  `nrfjprog` 9.4.0, so `arduino-cli` will not install `rakwireless:nrf52` on a
  Pi as published; the setup lends it the aarch64 `nrfjprog` that the Adafruit
  and Heltec indexes serve for that same tool (no build uses `nrfjprog`).
  Heltec_nRF52 1.6.0 runs its UF2 step through a quoted `python …/uf2conv.py`
  that cannot start off Windows, so every HT-n5262 compile (the MeshPocket
  RNode, the T114 RTNode) stopped before its DFU `.zip`; the setup switches
  that step off in the core's `platform.local.txt`, as the core's own
  `platform_board_manager.txt` has it. Both are in the manifest with their
  reasons.
- **PlatformIO's `nordicnrf52` platform is not installed.** Node Medic 1 has
  it, from an unpinned git URL, for an env no Node Medic build selects.
- **A firmware folder or OS image that differs from its pin is never
  overwritten.** The setup names it and leaves it; move it aside and run the
  setup again to fetch the pinned one. Likewise a built image is not rebuilt
  while its files exist.
- **The direct-cable address a clone keeps from its card is not applied.** A
  clone's card carries `nodemedic-cable-ip.service` and keeps NetworkManager off
  the wired port so its parent can find it on the cable; a medic set up from
  GitHub may be using ethernet for its internet. As a parent it needs neither:
  it sets its own cable address when it clones.
- **A clone made by a parent with no carried wheelhouse installs stock rns
  1.3.8** (the clone's online fallback reads `assets/requirements.txt`), which
  cannot name the Tracker. A medic set up by this script always has the
  wheelhouse and its own frozen versions (step 27), so its clones get the
  patched 1.3.7; a parent that never finished step 27 would not.
- **Node Medic 1 itself still runs the older desktop arrangement** (labwc and an
  XDG autostart). The pieces that belong only to it are not part of this route:
  `scripts/setup_boot.sh`, `scripts/nodemedic-ui.desktop`,
  `docs/MEDIC_KIOSK.md`, and the branded boot splash `scripts/setup_splash.sh`
  (which still assumes `/home/nodemedic`). `scripts/restart_ui.sh` was written
  for that desktop session; on a kiosk medic restart the unit instead (below).

### What a parent medic must carry to clone

The clone ladder (`workflows/clone.py`, shown one row per step on the Clone
screen) moves these from the parent to the child. Its rule is to take
everything the parent has and leave behind only what a short list in the code
names, each entry with its reason, so nothing new has to be remembered to
travel. Only the Pi OS image is required; without it the clone stops and says
so. A medic set up with
`scripts/setup_medic.py` has every item below once the setup has finished (its
records and fleet roster start empty, and the world map may still be
downloading); one that stopped part-way carries what it has.

- the tool tree itself, minus `.git` and caches;
- the offline RNode firmware cache at `~/.config/rnodeconf/update`;
- the parent's **whole home folder**, minus `HOME_NEVER`: its own identity and
  keys, personal and desktop files, and clutter (caches, logs, backups, the
  `~/scratch` work folder and `~/this-medic`). That carries the toolchains
  (`~/.arduino15` without its 1 GB of download leftovers, `~/.platformio`,
  `~/.local`), the Arduino libraries, the Pi OS image `~/pi_os_lite.img.xz`,
  the firmware trees and anything added later. Folders that are not hidden
  travel without their git history. A clone for a new community gets every
  folder but no loose file except the OS image;
- the records in `~/.reticulum-node-medic`: a clone in the parent's fleet takes
  all of them except the parent's own identity, trust, setup state and
  messages (`RECORDS_NEVER`), so boundary walks, certificates, host keys and
  any record added later travel by themselves; a clone for a new community
  takes only the radio defaults and the language;
- the Python wheelhouse in `assets/packages/` with this medic's own frozen
  versions (`requirements-parent.txt`), and the `.deb` cache in
  `assets/packages/debs` (the `cage` display stack, Dire Wolf, ALSA and the
  tools) together with **every package this medic installed after it was
  imaged**, read from dpkg's own log, kept in `packages_added.txt` in its
  records and planned against a fresh card so what those packages rest on
  comes too. The clone installs all of it or stops and names what is missing;
  nothing is optional;
- the medic's Kivy config (the doubled-tap cure), its offline maps (cleaned of
  anything that points at the parent's home), its monitoring records and its
  fleet roster, and the service that keeps filling the world map whenever the
  medic is online;
- the card helper source (`assets/scripts/prepare_card.py`) and the radio
  set-up helper (`assets/scripts/radio_units.py`), both installed root-owned on
  the child, and a fresh SSH keypair generated **on** the child;
- a **fresh Reticulum identity** generated on the child — the parent's is never
  copied — plus a lineage stamp and a trust record for the child;
- last, the parent's own locks: scoped sudo for the child's user, key-only SSH
  and the SSH firewall (`provisioning/security/apply_all.sh`), confirmed only
  after the parent has logged back in from outside and rolled back on any
  doubt. A clone made for a new community then keeps no one's key, so nobody
  can log in to it remotely; a clone for the parent's own fleet keeps the
  parent's key.

### Updating a medic — the details

- On the medic: `cd ~/reticulum-tool && git pull`, then run
  `python3 ~/reticulum-tool/scripts/setup_medic.py` again. It installs packages,
  cores and libraries at any new pin and builds any image that is missing; a
  firmware folder or OS image at an older pin is named, not moved (move it aside
  first).
- Restart the app on a kiosk medic (every clone, and every medic set up with
  this script) through its unit, after the busy check restart_ui.sh uses:
  `cd ~/reticulum-tool && bash scripts/ui_busy_guard.sh && sudo systemctl restart reticulum-node-medic`
- On Node Medic 1's desktop arrangement: `cd ~/reticulum-tool && bash scripts/restart_ui.sh`.
  It refuses while a card write, a flash, an `rnodeconf` run, a DFU, an
  arduino-cli upload or the UI's own busy marker is live; wait, or `FORCE=1`
  only when the running instance is the thing that is broken.
- From a laptop: sync exactly the tracked files and nothing else, then restart —
  `README.md` ▸ *Updating the tool*. A partial sync has taken the UI down
  before; after an rsync, `git log` on the medic does not describe what is
  running.
- If a new privileged command was added to the code, a medic whose sudoers was
  scoped lags the repo until `sudo bash provisioning/security/apply_sudoers.sh`
  is run again.
