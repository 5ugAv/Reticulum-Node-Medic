# Reticulum-Node-Medic

**Easy to use Reticulum network building/monitoring/maintenance tool.**

Node Medic is a Raspberry Pi 5 touchscreen tool for
[Reticulum](https://reticulum.network) LoRa meshes. It **builds** nodes from bare
boards, **monitors** them over the air, **diagnoses and repairs** them,
**messages** over the mesh, and can **clone** itself onto a fresh Pi so the next
keeper has a medic too. It reaches nodes over USB serial, SSH, a cable link
(USB gadget to a Pi node, ethernet to another medic), or by running commands on
the medic itself.

It is designed to work with no internet in the field — carry, don't fetch.
Firmware, Python wheels, Debian packages, Reticulum config templates, board
photographs and map tiles are all carried on disk. What has to be fetched while
online is fetched from one screen (Settings ▸ **Field readiness**), which reads
the disk and says what is still missing rather than calling the medic ready.
Three things it cannot fetch for you: the **offline map** for your area (MAPS ▸
Download offline map), the **Raspberry Pi OS image** used for SD imaging (copied
onto the medic as `~/pi_os_lite.img.xz`), and the **firmware build toolchain**
(fetched by the first RTNode build done with Wi-Fi on). A medic set up from
this repository with `scripts/setup_medic.py` gets the pinned OS image, the
whole toolchain and the world-overview map from that script; the map of your
own area is still yours to choose.

**Build one yourself:** the 3D-printed case, its parts list and the assembly
steps are on Cults 3D —
[Node Medic case](https://cults3d.com/en/3d-model/gadget/node-medic-case). The
software side is [`docs/BUILD_A_MEDIC.md`](docs/BUILD_A_MEDIC.md).

> This is the **tool**. It is kept separate from the node firmware (RNode, and
> the RTNode-2400 fork the medic builds as **RTNode-2400-NM**) that it inspects
> and repairs. Only the health-beacon wire format is vendored here, so the two
> cannot drift — see [`firmware/`](firmware/) and
> [`docs/FIRMWARE_NAMING.md`](docs/FIRMWARE_NAMING.md).

## The front page

The front page is a painted poster with a tap map over it
([`ui/home_zones.py`](ui/home_zones.py), artwork `assets/ui/front_page.png`).
The words on the picture are the interface; a test pins the tap map to the
painted labels. Five cards along the bottom open the five modes:

1. **VITALS** — the node dashboard. A filterable list (All / OK / Warn / Alert)
   of every node the medic knows about, with a quiet divider for nodes not heard
   recently, fed by a live Reticulum announce listener and a 30-second poll
   cycle. Tapping a node opens its detail page (ping, location sharing, range
   test, rebirth, delete).
   The filter row carries a **Self Diagnose** button — the medic diagnosing its own
   radio, GPS and services (see *Self Diagnose* below).
2. **MAPS** — a geographic map of located nodes over offline raster tiles, with
   pan/pinch, a mesh-lines overlay, a terrain toggle, the medic's own GPS fix,
   placement suggestions, and "Use this position →" to hand a location straight
   to BUILD. The screen's internal name is still `scan`.
3. **BUILD** — makes a node from bare hardware. A step-by-step guided
   walkthrough detects what is plugged in and decides whether the board should
   be **born** (flashed and provisioned) or **adopted** (enrolled as kin without
   reflashing), then offers the paths: an RNode radio, a standalone
   RTNode-2400-NM, a Raspberry Pi plus radio, or **Clone this device** (a new
   medic). "Not one of these? Show me what you got" opens the salvage screen
   for second-hand hardware. Internal name `birth`.
4. **ANTENNA** — antenna aiming and site assessment. A thermal bullseye scores
   the signal live while you move the antenna; a "Not Reading" cover is meant
   to drop over the score after a few seconds of radio silence so a stale
   reading cannot be mistaken for a live one (the readiness ledger, #26,
   records that it does not yet fire reliably). From here the **Antenna test**
   ranks antennas against each other on a plugged-in node. Internal name
   `triage`.
5. **CHAT** — the medic's own LXMF messenger: type a message, send it over the
   mesh, read replies. A message with no path right now is handed to the
   medic's own propagation node to hold; one addressed to a peer the mesh has
   never heard is marked failed and goes again the moment that peer announces.
   The "Phone apps" button hands a phone the carried Columba or Sideband APK
   over Wi-Fi instead. See [`docs/CHAT.md`](docs/CHAT.md).

Also on the front page: the **gear** opens Settings; the **Home / Backpack**
toggle sets the medic's network role (Home = routes for the mesh and, by
default, runs the propagation node that stores messages for offline users;
Backpack = transport off, so a medic on the move cannot disturb the mesh;
unless that is switched off in Settings, the medic puts itself in Backpack when
its GPS sees it moving); a
**slide-to-power-off** control; a battery gauge when a UPS is fitted. On the
globe, the **LoRa trunk node** — the filled disc the mesh grows out of — opens
the credits screen, and the **Wi-Fi marker** is a shortcut into Wi-Fi settings.

Two things deliberately have no card:

- **PROBE** — per-node diagnose-and-repair. One button runs the full diagnostic
  with streaming per-category progress, then offers "Fix all" or individual
  fixes; a fix is reported as fixed only after the original check is re-run and
  passes. Today PROBE is reached from the first-use tour, from the credits
  screen's mode row, and from the control socket (`scripts/medic_control.py
  probe`). That it has no everyday door is an open item in the
  [readiness ledger](docs/READINESS_LEDGER.md) (#204, #144).
- **Clone** — once in a device's life, not a weekly mode, so it lives inside
  BUILD's guided walkthrough. (The screen and workflow keep the code name
  `mitosis`.)

The first time a medic is powered on it opens a **setup walkthrough** — the
security choices, then a tour that opens each mode by its painted word — and
goes to the front page when that is finished. Settings can run it again for the
next keeper. As of 2026-10-04, `ui/app.py` registers **33 screens** in total
(node detail, certificate view, Settings and its sub-pages, notifications, the
guide, the SD-card imager, the clone, the firstborn ceremony, Self Diagnose,
credits — see *Counts* below for how that was computed).

## Node types

- **Type A — Raspberry Pi transport / propagation node** (`rnsd` + `lxmd`,
  attached RNode).
- **Type B — standalone RTNode-2400-NM** (the Node Medic build of the
  RTNode-2400 microReticulum firmware; no Pi; health arrives as RNS announce
  beacons — see below).
- **Type C — RNode only** (a LoRa board as a Pi node's radio interface).

`node_profile.py` models five node roles: transport, LXMF propagation, gateway,
Meshtastic bridge, and unknown.

### Board catalogue

Counted from the code on 2026-10-04 (commands under *Counts* below):

- [`workflows/rnode_boards.py`](workflows/rnode_boards.py) carries **17 boards**.
  14 are flashed through `rnodeconf --autoinstall` from the offline firmware
  cache (LilyGO LoRa32 v1.0 / v2.0 / v2.1, T-Beam, T-Beam Supreme, T3S3, T-Deck,
  T-Echo, Heltec LoRa32 v2 / v3 / v4, Heltec Mesh Node T114, RAK4631, Seeed XIAO
  ESP32S3); three are built on the medic — the Heltec Wireless Tracker and the
  Ebyte EoRa-S3 through arduino-cli, the Heltec MeshPocket through nRF52 serial
  DFU.
- **12 of the 14** autoinstall boards have a verified 915 MHz band map. The
  T-Beam and the T3S3 refuse to flash, with the reason: each ships with
  different radio chips under one name and nothing on the USB side can tell
  which is in your hand.
- **15 of the 17** have a photograph in `assets/boards/` (the LoRa32 v1.0 and
  v2.0 do not), shown to the operator so an identification made from silicon
  they cannot see has a visual check.
- **8 boards** can be built as a standalone RTNode-2400-NM
  ([`workflows/rtnode_build.py`](workflows/rtnode_build.py)): Heltec V3, Heltec
  V4, T-Beam Supreme, T-Echo, RAK4631, Heltec Mesh Node T114, Seeed XIAO ESP32S3
  and Ebyte EoRa-S3.
- Which boards have actually been born on real hardware, and as what, is in
  [`docs/BOARD_COVERAGE.md`](docs/BOARD_COVERAGE.md) — generated from the
  medic's own birth certificates, not hand-written.
- RNode firmware is **pinned at 1.86** (`PINNED_FIRMWARE` in
  [`workflows/updater.py`](workflows/updater.py)); a newer upstream release is
  reported by Field readiness, never fetched.

Caveat, stated plainly: the three built-here boards and all eight RTNode-2400
boards build from firmware folders kept in a medic's home directory, not in
this repository. Those folders are published, and pinned by commit in
[`assets/medic_manifest.json`](assets/medic_manifest.json). A medic cloned from
one that has them carries every one of them. A medic set up from this
repository gets them from `scripts/setup_medic.py`, which fetches each at its
pinned commit, builds the Tracker, EoRa-S3, MeshPocket and Heltec V4 colour
images BUILD flashes, and compiles RTNode-2400 once on each toolchain
([`docs/BUILD_A_MEDIC.md`](docs/BUILD_A_MEDIC.md)). That script was written and
tested off the Pi on 2026-10-08; it has not yet been run end-to-end on a Pi 5.

## Deployment defaults (all overridable)

| Frequency | Bandwidth | SF | Coding rate | TX power |
|---|---|---|---|---|
| 915.125 MHz | 125 kHz | SF9 | 4/5 (CR5) | 17 dBm |

Regulatory basis: Australian LIPD Class Licence, 915 MHz band.

These are a mesh-wide invariant: nodes can only hear nodes on the same
settings, and every node is built with them baked in. If the tool's defaults are
changed (Settings ▸ Default radio parameters), the home screen shows a
persistent badge until they are reverted, and the medic's own radio is retuned
to match ([`provisioning/medic_radio.py`](provisioning/medic_radio.py)).

## Diagnostics

Eight category modules in [`diagnostics/`](diagnostics/); counting the distinct
check names declared in them gives **96** (2026-10-04 — 82 across the seven
Pi-side modules, 14 in the RTNode-2400 module). Each check has a plain-English
description, a severity and, where possible, an auto-fix. Checks never
short-circuit — one failure cannot hide a later one.

The Pi repair chain ([`workflows/repair.py`](workflows/repair.py)) runs seven
modules in a fixed, operator-visible order:

Power & hardware → Reticulum software → Radio & firmware → System health →
Network & mesh → Client connectivity → GNSS.

The same code runs whether the target is reached over SSH or over a serial
cable — only the `Connection` differs. A fix is only reported as fixed after the
original check is re-run and passes (`verify_fixed`): a fix command exiting 0
does not prove the fault is gone.

The RTNode-2400 module is **beacon-driven**: those boards have no text console,
so on a physical visit the tool captures the passive serial `[HealthBeacon]`
line, decodes it with the shared codec and derives its checks from the decoded
fields (plus a boot-log FATAL scan) — the same wire contract used over the mesh.

### Self Diagnose (the medic itself)

[`monitor/self_diagnose_runtime.py`](monitor/self_diagnose_runtime.py) gathers
**13 findings** about the tool's own health (2026-10-04): onboard radio present
on USB, the serial splitter, GPS telemetry freshness, disk space, the touch
provider, `rnsd`, CPU temperature, power throttling, Wi-Fi signal, clock sync,
Reticulum responding (`rnstatus`), `lxmd` (only expected when the medic is in
Home mode as a propagation node), and whether NetworkManager has been told to
leave the cable-birth link alone. Three findings have one-tap repairs — restart
the splitter, `rnsd`, or `lxmd`; four more offer written guidance instead; the
rest are read-only. Deeper chip
and firmware probes exist in the module but are deliberately not run here,
because they would reset the board and steal its serial port. Reached from
VITALS (**Self Diagnose**), Settings, and PROBE.

## Type B health beacons

RTNode-2400 nodes carry health in the `app_data` of a periodic RNS **announce**
on the `rtnode.health` aspect — a compact big-endian payload decoded by
[`monitor/health_beacon.py`](monitor/health_beacon.py): 14 bytes in version 1,
with later versions appending a battery/link tail, a self-reported position
(GPS-fitted nodes) and the list of neighbours the node can hear. An
**on-demand poll** ([`monitor/health_poll.py`](monitor/health_poll.py)) sends a
1-byte request (`0x01`) to the node's destination and the node answers with an
immediate beacon.

The encoder is pinned to the firmware by contract: `firmware/rtnode-2400/`
vendors the C++ packer header, and
[`tests/test_firmware_beacon_contract.py`](tests/test_firmware_beacon_contract.py)
compiles it with `g++` and asserts byte-equality with the Python encoder against
golden vectors.

## Monitoring

`ui/app.py` starts two things at launch: a 30-second poll cycle (LAN `/status`
discovery and `rnpath` mesh discovery, with the registry saved about every ten
cycles) and an **RNS announce listener** registered against the shared `rnsd`.
One handler ingests every announce into a persisted node registry, collapsing a
device's several aspect destinations into a single row by identity; a second
collects `rtnode.health` beacons. Every announce heard is logged — that line is
the ground truth for "is the app deaf?".

On a dev box with no RNS library installed, the listener returns quietly and the
rest of the app runs.

## Building, adopting and cloning

- **SD-card imaging** ([`provisioning/pi_imager.py`](provisioning/pi_imager.py))
  writes the carried Raspberry Pi OS image to a USB card reader with `dd`, then
  hands the card to a root-owned helper
  (`/usr/local/lib/nodemedic/prepare-card`, installed from
  [`assets/scripts/prepare_card.py`](assets/scripts/prepare_card.py)) which
  activates the account, bakes the cable link and Wi-Fi onto the root
  filesystem and grows it — because `custom.toml` and cloud-init are both
  inert on the carried image. It refuses any target that is not a present,
  removable USB disk, and requires a typed confirmation. The image is expected
  at `~/pi_os_lite.img.xz`; the app has no download path, and it fails with that
  message if absent (`scripts/setup_medic.py` fetches the pinned image, checked
  against its published SHA-256, when a medic is set up from this repository).
- **Guided birth** walks the operator one instruction per screen: antenna
  first (never power a radio without one), then detect the board, read and
  classify it, then either adopt it or choose a path. The Pi path hands off to
  the SD imager and back.
- **Adoption** ([`workflows/adopt_node.py`](workflows/adopt_node.py)) enrols an
  already-provisioned node as kin without reflashing or renaming it. It works
  over USB, and over the air for a node heard on the mesh but never plugged in.
- **Birth certificates** are photographable cards. Their QR payload
  ([`ui/qr.py`](ui/qr.py)) says what the node *is* — name, type, board, radio
  settings, the board's own hardware ID — never where it is or whose it is; the
  full certificate stays on the medic.
- **Clone** ([`workflows/clone.py`](workflows/clone.py), screen
  [`ui/screens/mitosis_screen.py`](ui/screens/mitosis_screen.py)) has been the
  real flow since 2026-08-25. The medic images a card for the new Pi 5 from its
  own reader, finds the new machine over an ethernet cable or Wi-Fi, logs in
  with its own key, and runs a ladder of named steps shown one row each on
  screen: copy the tool, the firmware cache, the toolchains and OS image;
  install the Python stack from carried wheels and the screen stack from
  carried `.deb`s; hand down the maps, records and fleet roster; give the new
  medic its **own fresh identity** (never a key copy), stamp its lineage,
  install its card helper, radio helper and SSH key, and set it to boot into
  the tool. Last, it locks the new medic the way the parent is locked —
  narrowed admin rights, key-only SSH, an SSH firewall — checking from outside
  with fresh logins and undoing everything on any doubt; a clone made for a new
  community then keeps no one's key. What the parent must carry for all that is
  listed in [`docs/BUILD_A_MEDIC.md`](docs/BUILD_A_MEDIC.md). A full clone,
  Node Medic 2, was made on 2026-10-06 and set up its own radio. On 2026-10-08
  a side-by-side comparison with its parent found what the clone could not do;
  those gaps were closed in the clone itself that day, and
  [`tests/test_clone_parity.py`](tests/test_clone_parity.py) now fails if the
  code comes to need something a clone would not get.

## Map and placement

MAPS renders Web Mercator raster tiles from a local MBTiles file in
`~/.reticulum-node-medic/maps/` (outside the repo tree, so a deploy cannot
delete a downloaded map). Tiles are **not shipped**; they are downloaded
per-region while online ([`ui/map_download.py`](ui/map_download.py) — never by
bulk-fetching `tile.openstreetmap.org`), with attribution drawn on the screen,
after which the map is fully offline. The current basemap source (Esri World
Street Map) is a stop-gap: its provider terms do not cover bulk offline caching,
so a source whose terms allow offline storage is still to be chosen.

Placement suggestions ([`monitor/placement.py`](monitor/placement.py)) mark
where a node would extend the mesh, from an `rnpath`-derived topology and a
log-distance path-loss model self-calibrated against this mesh's observed reach.
Suggestions appear only once `rnpath` returns edges between located nodes.

Distance alone is a poor predictor, and this mesh has the scar to prove it: a
sub-kilometre link failed because the path ran through houses, at a distance the
model called comfortable. So a suggestion is also checked against the ground
([`monitor/terrain.py`](monitor/terrain.py)) — it walks the elevation profile
between the candidate and each partner, adds the earth's curve at the standard
4/3 effective radius, and reports the tightest squeeze against the first Fresnel
zone. A blocked path becomes a caution naming the node it cannot see.

Three limits, stated in the code and worth repeating here:

- **Terrain is not buildings.** The elevation data is bare earth, so a clear
  profile through a suburb is still a suburb. "Clear" means the ground does not
  block it, never that the link will work.
- A missing tile reads as **unknown**, never as sea level — otherwise a mountain
  looks like clear air.
- With no terrain cached at all, suggestions are returned unchanged with a note
  that they are distance-only.

Terrain rides along with the map download — one button, one region, both
datasets — as Tilezen "terrarium" elevation tiles (SRTM-derived, from AWS Open
Data) in the same z/x/y scheme as the basemap. A **Terrain** toggle on MAPS
shades high ground light and low dark, scaled to the range in view, because what
helps a radio is standing above its surroundings.

## Privacy

Exact node locations are meant to stay on the builder's medic.

- **The default is silence.** Whether a node tells the world roughly where it is
  is asked during birth — *Hidden* or *Show on map* — and is *Hidden* until
  someone answers ([`monitor/location_share.py`](monitor/location_share.py)).
  The node's detail page shows the same switch, though a change made there
  does not yet stick (ledger #57).
- When sharing is on, coordinates written into an RTNode-2400 over its setup
  portal are **fuzzed by 800 m** first ([`monitor/geo.py`](monitor/geo.py)),
  deterministically per node and salted with a secret kept only on this medic,
  so repeated announces cannot be averaged back and the circle's centre is not
  the answer. The firmware then applies **its own** deterministic ~500 m offset
  on top, so a public pin sits up to ~1.3 km from the hardware
  (`monitor.geo.public_pin_radius_m`).
- The birth-certificate QR omits location entirely.
- The exact fix **is** kept in the birth certificate on the medic; the
  certificate view offers the builder a navigation link to it.

Known hole, from the readiness ledger (#157): a GPS-fitted RTNode's health
beacon carries its live position unfuzzed, whatever was answered at birth. That
is a firmware-side fix that has not shipped.

## Languages

The UI is translatable ([`ui/i18n.py`](ui/i18n.py)) with offline, source-keyed
catalogues in `assets/i18n/`. Counted 2026-10-04: **14 languages are declared**
(English plus Spanish, French, German, Portuguese, Italian, Indonesian, Swedish,
Polish, Russian, Japanese, Swahili, Tok Pisin, Hindi) and **11 ship a
catalogue**; Italian and Hindi are declared without one and so are never
offered. Eight catalogues are full (the same key count as each other); the
Portuguese, Swahili and Tok Pisin ones are small — 124 keys against 1,295 —
but cover the first-contact path, which is the minimum the picker demands
before it will list a language. Japanese is
offered only when a carried font that can draw it is present. Settings ▸
Language stores the choice; it applies when Node Medic restarts, not live.

Coverage is partial and honest about it: several Settings sub-screens and the
reference guide are still English in every language (ledger #205, #206). An
AST-walking test asserts that every string that *is* wrapped has a translation,
so coverage cannot silently regress.

## Install — building your own medic

The full path from a blank Pi 5 to a running medic, with what each step
actually installs and what the repository does **not** yet provide, is in
**[`docs/BUILD_A_MEDIC.md`](docs/BUILD_A_MEDIC.md)**. In short: write
Raspberry Pi OS Lite (64-bit) to the card with any user name, clone this
repository to `~/reticulum-tool`, and run

    python3 ~/reticulum-tool/scripts/setup_medic.py

as that user. It runs the clone's own ladder on the Pi, with the internet
standing in for a parent medic, installing what Node Medic 1 has at Node Medic
1's versions from [`assets/medic_manifest.json`](assets/medic_manifest.json):
the `cage` kiosk, the packages, Python with the medic's Reticulum patch, the
board cores and libraries, the firmware sources at their pinned commits and the
images built from them, PlatformIO, the OS image, the field caches and the
world map. One line per step; run it again after any failure and it carries
on; `--check` only reports what is missing. Then reboot, and set up the
medic's own radio (its Heltec Wireless Tracker) on the first page of the
walkthrough's tour. Security hardening (scoped sudo, key-only SSH, the
firewall) stays a separate step by hand
([`provisioning/security/README.md`](provisioning/security/README.md)). The
script has not yet been run end-to-end on a Pi 5 — the Known gaps in that file
say what was checked instead.

## If something goes wrong

- **Scrambled or shifted colours on the panel** (typically after a USB device
  is plugged in): Settings ▸ **Display** ▸ **Fix screen colours**. It switches
  the DSI output off with `wlr-randr`, waits two seconds and switches it back on
  (three attempts), re-running the panel's init sequence
  ([`provisioning/screen_fix.py`](provisioning/screen_fix.py)). The screen goes
  black for about two seconds. Software cannot see the panel's true state, so
  the status line only reports that the re-init ran; if the colours are still
  wrong, power the medic off, wait ten seconds, and power back on. The same
  re-init works from SSH:
  `cd ~/reticulum-tool && python3 -c "from provisioning import screen_fix; print(screen_fix.reinit_panel())"`.
- **The app has died** (wallpaper and no menu bar — the desktop panel is
  removed on a kiosk medic, see [`docs/MEDIC_KIOSK.md`](docs/MEDIC_KIOSK.md)):
  from another machine,
  `ssh nodemedic@<medic-hostname>.local 'cd ~/reticulum-tool && bash scripts/restart_ui.sh'`.
  The script **refuses** (exit 3) while the medic is writing hardware — an SD
  image write, a board flash, an `rnodeconf` run, an nRF52 DFU, an arduino-cli
  upload — or while the UI's own busy marker is fresh (a birth over SSH, a
  download, a self-check); wait, or re-run with `FORCE=1` only when the running
  instance is the thing that is broken. `STOP_ONLY=1` stops without restarting.
  That script was written for Node Medic 1's desktop session. A medic that
  boots through the `cage` kiosk unit (every clone, and every medic set up with
  `scripts/setup_medic.py`) is restarted through its unit, after the same busy
  check:
  `cd ~/reticulum-tool && bash scripts/ui_busy_guard.sh && sudo systemctl restart reticulum-node-medic`.
- **Read the logs**: `~/ui.log` (every launch road appends here, unbuffered)
  and `~/ui_crash.log` (`faulthandler` output for native crashes in Kivy / SDL /
  GL / serial code, which otherwise leave no traceback).
- **Drive the running app from a shell** without touching the glass:
  `python3 scripts/medic_control.py list | open <screen> | home | probe`
  ([`ui/remote.py`](ui/remote.py)).

## Updating the tool

- **On the medic**: `cd ~/reticulum-tool && git pull && bash scripts/restart_ui.sh`
  (a kiosk medic: restart its unit, as above). On a medic set up with
  `scripts/setup_medic.py`, run that script again after a pull: it installs
  packages, cores and libraries at any pin that moved in
  `assets/medic_manifest.json` and builds any image that is missing; a firmware
  folder or OS image at an older pin is named, not replaced
  ([`docs/BUILD_A_MEDIC.md`](docs/BUILD_A_MEDIC.md) ▸ *Updating a medic*).
- **From a laptop**, when the medic's checkout is not what runs: copy exactly
  the tracked files and nothing else, e.g.
  `cd <repo> && rsync -a --files-from=<(git ls-files) . nodemedic@<medic-hostname>.local:~/reticulum-tool/`,
  then restart as above. A partial sync has taken the UI down before — sync all
  of `git ls-files`, never a subset. After an rsync, `git log` **on the medic**
  does not describe what is running; `git status --porcelain` there, or a grep
  for the code itself, does.
- **The test gate**: `python3 -m pytest` before anything is deployed; the same
  suite runs on every push in GitHub Actions
  ([`.github/workflows/ci.yml`](.github/workflows/ci.yml), Python 3.11 and 3.12).

## Architecture

```
node_profile.py   dataclasses / enums — node roles, hardware, radio, connection methods
transport/        how a command reaches a target: SSH, Local (on the medic,
                  incl. a PTY runner for rnodeconf), Serial, Emulated
diagnostics/      the check library — base classes + 8 category modules
workflows/        operations performed ON a node: build, flash, repair, adopt,
                  RTNode-2400 build, clone, field readiness (carry), offline
                  firmware / wheel / deb caches, board catalogue, phone apps
monitor/          the observation layer behind VITALS / MAPS: beacon codec, poll,
                  registry, history, alerts, topology, placement, terrain,
                  location sharing, the chat service, the medic's own self-diagnosis
provisioning/     the medic's OWN configuration and the link it uses to reach a
                  node: display, power, storage, clock, identity, records vault,
                  SSH pinning, radio defaults, SD imaging, USB gadget, UART,
                  the security hardening runbook
ui/               the Kivy touchscreen app — shell, theme, widgets, screens,
                  plus non-Kivy helpers (board detection, QR, i18n, tap map,
                  control socket)
firmware/         vendored RTNode-2400 C++ headers, so the health-beacon wire
                  format sits beside its Python counterpart and is contract-tested
sandbox/          a Lima VM mirroring the medic's software surface
scripts/          systemd units, the boot/autostart installer, restart + busy
                  guard, cache refreshers, vault operator scripts, previews
assets/           Reticulum config templates, translation catalogues, board
                  photographs, UI artwork, the root card helper; gitignored
                  carried caches (firmware, wheels, debs, maps, apps, fonts)
tests/            the suite — headless, never imports Kivy
docs/             long-form notes — index below
```

## Development

Test-first throughout. The whole tested core runs headless with no hardware via
an in-memory `EmulatedConnection`, and never imports Kivy.

```bash
pip3 install -r requirements-test.txt   # test-only deps (incl. rns, so the mesh tests run rather than skip)
python3 -m pytest        # the suite (pytest.ini already passes -q)
python3 main.py          # launch the touchscreen app (needs Kivy + a display)
python3 main.py --version
```

`main.py` enables `faulthandler` to `~/ui_crash.log` before starting the UI.

**Emulated demos are opt-in.** On Linux — the deployed medic — flash, build,
Pi-build, PROBE and Clone either do real work or fail with a stated reason;
they never report a fake success. Set `RNM_DEMO=1` to explore the flows with
emulated hardware and seeded demo nodes.

### Counts

Every number in this file was computed on 2026-10-04 from the commit it
describes, with the commands below. Re-run them before quoting a count; the
sentences are written so that the drift is in the number, not the claim.

```bash
python3 -m pytest --collect-only -o addopts= -q | tail -1          # tests (6735 collected)
grep -o 'Screen(name="[^"]*"' ui/app.py | sort -u | wc -l            # screens (33)
python3 -c "from workflows.rnode_boards import RNODE_BOARDS as b; print(len(b))"   # boards (17)
python3 -c "from workflows.rtnode_build import RTNODE_TARGETS as t; print(len(t))" # RTNode targets (8)
ls assets/boards/*.png | wc -l                                       # board + Pi photographs (18)
grep -c '^    ("' ui/i18n.py                                         # declared languages (14, the _LANGUAGES rows)
ls assets/i18n/*.json | grep -v _critical | wc -l                    # catalogues (11)
ls diagnostics/*.py | grep -v 'base\|__init__\|labels' | wc -l      # diagnostic modules (8)
```

## Status

Working and used in the field: the medic boots to the UI, hears the mesh live
through its own attached RNode, and monitors, maps, diagnoses and repairs real
nodes. The project's own records say messages have moved both ways over LoRa
between the medic and a phone (2026-10-01,
[`docs/V1_SCOPE.md`](docs/V1_SCOPE.md)) and that a clone has been produced and
booted into the tool on its own screen (2026-08-25,
[`docs/MITOSIS_READINESS.md`](docs/MITOSIS_READINESS.md)). Which boards have
been born as what is in [`docs/BOARD_COVERAGE.md`](docs/BOARD_COVERAGE.md). The
operator's own definition of "done" for version 1 is
[`docs/V1_SCOPE.md`](docs/V1_SCOPE.md).

Everything known to be wrong is in **[`docs/READINESS_LEDGER.md`](docs/READINESS_LEDGER.md)**
— every finding of a 2026-10-03 readiness sweep, re-verified against the code
and ticked as it is closed. The largest open items, summarised honestly:

- **The clone test has not been passed from a GitHub checkout.** The clone
  needs carried `.deb`s, wheels, a Pi OS image and firmware trees that are
  gitignored or outside the repo; a fresh checkout gets them from
  `scripts/setup_medic.py`, which has not yet been run end-to-end on a Pi 5
  (ledger #115, #154).
- **The clone's automatic lock-down has run only on a stand-in.** On
  2026-10-08 it locked a stand-in clone in a test machine, held after a
  restart, and left a new community's clone with no key; no real clone has
  run it yet.
- **The first-use walkthrough is the tour alone in v1.** Its lock-your-records
  half (recovery key, passphrase, pattern, USB key) collected secrets its summary
  never enrolled, so it is switched off (`ui/setup_flow.SECURITY_HALF`) until a
  later release wires it to the real path, Settings ▸ Encrypt my records
  (ledger #174, #139).
- **Custom-board RNode births** (Wireless Tracker, MeshPocket, EoRa-S3), the
  **eight RTNode-2400 builds** and the **firstborn** (the new medic's own
  Tracker radio) depend on firmware folders in a medic's home directory. A
  clone carries them; a medic set up from GitHub gets them from
  `scripts/setup_medic.py`, at the commits pinned in
  `assets/medic_manifest.json`, with the three custom images built — a route
  not yet run end-to-end on a Pi 5 (ledger #43, #168, #120).
- **A medic with no map carried shows a black pane** with inert controls
  rather than saying a map is missing (ledger #2, #178, #177).
- **GPS-fitted RTNodes beacon their exact position** unfuzzed (ledger #157).
- **PROBE has no everyday door** and the same feature carries three names
  across the UI (ledger #204, #144).
- **Translations are partial**: Settings body copy, five sub-screens and the
  reference guide are English in every language (ledger #205, #206).
- **In Backpack mode CHAT still promises the propagation node** that Backpack
  has switched off (ledger #76, #185).
- **Terrain is bare earth** — buildings and trees are not modelled.

## Documentation

Start here if you are new:

- [`docs/BUILD_A_MEDIC.md`](docs/BUILD_A_MEDIC.md) — blank Pi 5 to running medic with `scripts/setup_medic.py` (pins in [`assets/medic_manifest.json`](assets/medic_manifest.json)), and the known gaps in that path.
- [`docs/HANDOVER_NEXT_SESSION.md`](docs/HANDOVER_NEXT_SESSION.md) — read-this-first for whoever picks the project up: state, deploy and test mechanics, method.
- [`docs/HANDOVER.md`](docs/HANDOVER.md) — the durable reference: architecture, the firmware contracts, the testing model.
- [`docs/WORKING_METHOD.md`](docs/WORKING_METHOD.md) — the working rules that were paid for.
- [`docs/READINESS_LEDGER.md`](docs/READINESS_LEDGER.md) — everything known to be wrong, with status.
- [`docs/V1_SCOPE.md`](docs/V1_SCOPE.md) — what version 1 is and is not.
- [`docs/history/SPEC.md`](docs/history/SPEC.md) — the original July 2026 specification, kept for the record.

Using the medic:

- [`docs/CHAT.md`](docs/CHAT.md) — the messenger and the propagation node.
- [`docs/MEDIC_KIOSK.md`](docs/MEDIC_KIOSK.md) — the on-device desktop changes that make it a kiosk, and how to undo them.
- [`docs/PHONE_APPS.md`](docs/PHONE_APPS.md) — getting a Reticulum messenger onto a phone.
- [`docs/WHICH_NODE_TO_BUILD.md`](docs/WHICH_NODE_TO_BUILD.md), [`docs/CHEAPEST_NODE.md`](docs/CHEAPEST_NODE.md), [`docs/BOARD_SHOPPING_LIST.md`](docs/BOARD_SHOPPING_LIST.md) — choosing and buying hardware.
- [`docs/BOARD_COVERAGE.md`](docs/BOARD_COVERAGE.md) — which boards have been proven, generated from the certificate ledger.
- [`docs/TRACKER_GNSS.md`](docs/TRACKER_GNSS.md) — the medic's own radio and GPS board.
- [`docs/encrypt-at-rest.md`](docs/encrypt-at-rest.md) — the records vault (built, not enabled by default).

Firmware and contracts:

- [`docs/RTNODE2400_INTEGRATION.md`](docs/RTNODE2400_INTEGRATION.md) — the verified firmware ↔ tool contract.
- [`docs/FIRMWARE_NAMING.md`](docs/FIRMWARE_NAMING.md) — why the build is called RTNode-2400-NM.
- [`docs/HEALTH_REPLY_UNICAST.md`](docs/HEALTH_REPLY_UNICAST.md) — health replies through the mesh.
- [`docs/RNODE_FACE.md`](docs/RNODE_FACE.md) and [`docs/RNODE_SALE_CARD.md`](docs/RNODE_SALE_CARD.md) — how a medic-built RNode looks, and the card that ships with one.

## Credits and licence

The people and projects this tool stands on are thanked on the medic's own
credits screen (tap the LoRa trunk node on the front page): Reticulum and RNode,
microReticulum, RNode Firmware CE, the RTNode and RTNode-2400 firmware this
fork grew from, the map and terrain data providers, and every neighbour who puts
a node on a roof. The firmware forks are GPL-3.0 and this tool is downstream of
all of them.

The tool itself is MIT — see [LICENSE](LICENSE).

MIT covers the tool's own code. The patch files under
[`assets/firmware-ports/`](assets/firmware-ports/) and the headers under
[`firmware/rtnode-2400/`](firmware/rtnode-2400/) are modifications of GPL-3.0
firmware (RNode_Firmware_CE and RTNode-2400) and carry that licence; the patches
to RNS-derived code (`rns-serial-detect.patch`, `eora-s3-rnodeconf.patch`) are
under the Reticulum License.
